"""Private, authenticated AES-256-GCM backups of consistent SQLite snapshots."""
import hashlib
import os
import secrets
import sqlite3
import time
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.access_control import read_private_bytes
from app.publication_boundary import require_private_process

MAGIC = b'DCBAK\x01\x00'
NONCE_BYTES = 12
KEY_BYTES = 32
TAG_BYTES = 16
MAX_SQLITE_BYTES = 64 * 1024 * 1024
SQLITE_MAGIC = b'SQLite format 3\x00'
ROLLBACK_JOURNAL_HEADER = b'\x01\x01'
REQUIRED = frozenset({'laws', 'articles', 'capture_candidates'})
CRITICAL = tuple(sorted(REQUIRED | {
    'source_inventory', 'source_inventory_items', 'article_triage',
    'staged_article_reviews', 'authenticated_operation_events',
    'authenticated_access_policies',
    'professional_credential_evidence',
}))


def _require_new(path):
    if os.path.lexists(path):
        raise FileExistsError('O destino já existe. Escolha um novo caminho de backup ou restauração.')


def _require_restore_target(target):
    for suffix in ('', '-wal', '-shm', '-journal'):
        _require_new(target.with_name(target.name + suffix))


def _write_private(path, raw):
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())


def _publish_new_file(target, raw, *, validate_sqlite=False):
    ensure_target = _require_restore_target if validate_sqlite else _require_new
    ensure_target(target)
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(target.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        parent = os.fstat(fd)
        if parent.st_uid != os.geteuid() or parent.st_mode & 0o022:
            raise PermissionError('O diretório deve pertencer ao operador e impedir escrita de terceiros')
        with TemporaryDirectory(prefix='.direito-backup-', dir=target.parent) as tmp:
            staged = Path(tmp) / 'output'
            _write_private(staged, raw)
            if validate_sqlite:
                with closing(sqlite3.connect(staged.resolve().as_uri() + '?mode=ro&immutable=1', uri=True)) as con:
                    report = _inspect_database(con)
            else:
                report = {}
            ensure_target(target)
            # Link atomically, failing if any file or link occupied the destination.
            os.link(staged, target.name, dst_dir_fd=fd, follow_symlinks=False)
            os.fsync(fd)
    finally:
        os.close(fd)
    return report


def _inspect_database(con):
    con.execute('PRAGMA query_only=ON')
    if con.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
        raise ValueError('O SQLite restaurado falhou na verificação de integridade')
    if con.execute('PRAGMA foreign_key_check').fetchone() is not None:
        raise ValueError('O SQLite restaurado contém chaves estrangeiras inválidas')
    tables = {row[0] for row in con.execute("SELECT name FROM sqlite_schema WHERE type='table'")}
    if not REQUIRED.issubset(tables):
        raise ValueError('O SQLite restaurado não tem o esquema base do Direito Claro')
    counts = {name: con.execute(f'SELECT COUNT(*) FROM {name}').fetchone()[0]
              for name in CRITICAL if name in tables}
    return {'integrity_check': 'ok', 'foreign_key_check': 'ok', 'tables_verified': counts}


def _cipher(key_path):
    key = read_private_bytes(key_path, KEY_BYTES)
    if len(key) != KEY_BYTES:
        raise ValueError('A chave de backup precisa ter 32 bytes aleatórios')
    return AESGCM(key)


def create_key(path):
    require_private_process()
    _publish_new_file(Path(path), secrets.token_bytes(KEY_BYTES))


def create_backup(source, output, key_path):
    """Snapshot SQLite, verify the copy, then persist only authenticated ciphertext."""
    require_private_process()
    source, output = Path(source), Path(output)
    _require_new(output)
    if not source.is_file() or source.is_symlink():
        raise ValueError('Origem SQLite ausente ou link simbólico')
    cipher = _cipher(key_path)
    with (closing(sqlite3.connect(source.resolve().as_uri() + '?mode=ro', uri=True, timeout=5)) as src,
          closing(sqlite3.connect(':memory:')) as dst):
        page_size = src.execute('PRAGMA page_size').fetchone()[0]
        deadline = time.monotonic() + 30

        def enforce_bounds(_status, _remaining, total):
            if total * page_size > MAX_SQLITE_BYTES:
                raise ValueError('SQLite excede o limite de 64 MiB para o backup criptografado')
            if time.monotonic() >= deadline:
                raise TimeoutError('O SQLite não forneceu uma cópia consistente em 30 segundos')

        enforce_bounds(0, 0, src.execute('PRAGMA page_count').fetchone()[0])
        src.backup(dst, pages=128, progress=enforce_bounds, sleep=0.05)
        dst.execute('PRAGMA temp_store=MEMORY')
        # Rebuild in memory so a WAL-mode source yields a self-contained image.
        dst.execute('VACUUM')
        if not hasattr(dst, 'serialize'):
            raise RuntimeError('O backup requer Python SQLite com suporte a serialize()')
        raw = dst.serialize()
        if len(raw) > MAX_SQLITE_BYTES or raw[18:20] != ROLLBACK_JOURNAL_HEADER:
            raise ValueError('O snapshot precisa ser um SQLite autônomo de até 64 MiB')
        report = _inspect_database(dst)
    nonce = secrets.token_bytes(NONCE_BYTES)
    header = MAGIC + nonce
    encrypted = header + cipher.encrypt(nonce, raw, header)
    _publish_new_file(output, encrypted)
    return {**report, 'schema': 'direito-claro-encrypted-backup-v1', 'encryption': 'AES-256-GCM',
            'encrypted_sha256': hashlib.sha256(encrypted).hexdigest(),
            'sqlite_sha256': hashlib.sha256(raw).hexdigest(), 'encrypted_bytes': len(encrypted)}


def restore_backup(backup, output, key_path):
    """Authenticate the full file before writing and verify SQLite before installing."""
    require_private_process()
    output = Path(output)
    _require_restore_target(output)
    header_size = len(MAGIC) + NONCE_BYTES
    blob = read_private_bytes(backup, MAX_SQLITE_BYTES + header_size + TAG_BYTES)
    if not blob.startswith(MAGIC) or len(blob) <= header_size + TAG_BYTES:
        raise ValueError('Formato ou tamanho do backup criptografado inválido')
    header = blob[:header_size]
    cipher = _cipher(key_path)
    try:
        raw = cipher.decrypt(header[len(MAGIC):], blob[header_size:], header)
    except InvalidTag as exc:
        raise ValueError('Não foi possível autenticar o backup criptografado') from exc
    if not raw.startswith(SQLITE_MAGIC) or raw[18:20] != ROLLBACK_JOURNAL_HEADER:
        raise ValueError('O backup autenticado não contém um SQLite autônomo válido')
    report = _publish_new_file(output, raw, validate_sqlite=True)
    return {**report, 'schema': 'direito-claro-encrypted-restore-v1', 'authenticated': True,
            'encrypted_sha256': hashlib.sha256(blob).hexdigest(),
            'sqlite_sha256': hashlib.sha256(raw).hexdigest(), 'off_host_restore_verified': False}
