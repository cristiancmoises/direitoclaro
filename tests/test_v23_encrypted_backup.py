"""Backup tests exercise an actual persisted file and real SQLite restore."""
import hashlib
import sqlite3
from pathlib import Path

import pytest


def sqlite_database(path):
    with sqlite3.connect(path) as con:
        con.executescript("""
          CREATE TABLE laws(slug TEXT PRIMARY KEY);
          CREATE TABLE articles(id INTEGER PRIMARY KEY, law TEXT REFERENCES laws(slug), content TEXT);
          CREATE TABLE capture_candidates(id INTEGER PRIMARY KEY);
          INSERT INTO laws VALUES ('synthetic');
          INSERT INTO articles VALUES(1,'synthetic','PRIVATE_SYNTHETIC_MARKER');
        """)


def backup_key(path, value=1):
    path.write_bytes(bytes([value])*32)
    path.chmod(0o600)
    return path


def test_persisted_encrypted_backup_restores_wal_rows(tmp_path):
    from app.encrypted_backup import create_backup,restore_backup
    source=tmp_path/'private.sqlite3';sqlite_database(source)
    key=backup_key(tmp_path/'backup.key')
    output=tmp_path/'vault.dcbackup';restored=tmp_path/'restored.sqlite3'
    with sqlite3.connect(source) as live:
        assert live.execute('PRAGMA journal_mode=WAL').fetchone()[0]=='wal'
        live.execute("INSERT INTO articles VALUES(2,'synthetic','Uncheckpointed row')")
        live.commit()
        report=create_backup(source,output,key)
        assert output.stat().st_mode & 0o777==0o600
        assert b'PRIVATE_SYNTHETIC_MARKER' not in output.read_bytes()
        assert report['encrypted_sha256']==hashlib.sha256(output.read_bytes()).hexdigest()
    recovered=restore_backup(output,restored,key)
    with sqlite3.connect(restored) as con:
        assert con.execute('SELECT content FROM articles ORDER BY id').fetchall()==[
            ('PRIVATE_SYNTHETIC_MARKER',),('Uncheckpointed row',)]
    assert restored.stat().st_mode & 0o777==0o600
    assert recovered['sqlite_sha256']==report['sqlite_sha256']
    assert recovered['integrity_check']=='ok'
    assert recovered['foreign_key_check']=='ok'


@pytest.mark.parametrize('kind',['wrong-key','tamper','truncated'])
def test_restore_authentication_failure_leaves_no_output(tmp_path,kind):
    from app.encrypted_backup import create_backup,restore_backup
    source=tmp_path/'private.sqlite3';sqlite_database(source)
    key=backup_key(tmp_path/'backup.key')
    output=tmp_path/'vault.dcbackup';restored=tmp_path/'restored.sqlite3'
    create_backup(source,output,key)
    if kind=='wrong-key':key=backup_key(tmp_path/'other.key',2)
    else:
        raw=bytearray(output.read_bytes())
        if kind=='tamper':raw[-5]^=1
        else:del raw[-20:]
        output.write_bytes(raw)
    with pytest.raises(ValueError):
        restore_backup(output,restored,key)
    assert not restored.exists()


def test_create_and_restore_never_clobber_existing_files_or_follow_output_links(tmp_path):
    from app.encrypted_backup import create_backup,restore_backup
    source=tmp_path/'private.sqlite3';sqlite_database(source)
    key=backup_key(tmp_path/'backup.key')
    output=tmp_path/'vault.dcbackup'
    create_backup(source,output,key)
    original=output.read_bytes()
    with pytest.raises(FileExistsError):
        create_backup(source,output,key)
    with pytest.raises(FileExistsError):
        restore_backup(output,source,key)
    link=tmp_path/'output.sqlite3';link.symlink_to(source)
    with pytest.raises(FileExistsError):
        restore_backup(output,link,key)
    assert output.read_bytes()==original
    assert link.is_symlink()


def test_restore_refuses_existing_wal_and_excludes_later_live_content(tmp_path):
    from app.encrypted_backup import create_backup,restore_backup
    source=tmp_path/'private.sqlite3';sqlite_database(source)
    key=backup_key(tmp_path/'backup.key')
    output=tmp_path/'vault.dcbackup';restored=tmp_path/'restored.sqlite3'
    wal=Path(str(restored)+'-wal')
    with sqlite3.connect(source) as live:
        live.execute('PRAGMA journal_mode=WAL')
        create_backup(source,output,key)
        live.execute("UPDATE articles SET content='Unbacked later content' WHERE id=1")
        live.commit()
        wal.write_bytes(Path(str(source)+'-wal').read_bytes())
        with pytest.raises(FileExistsError):
            restore_backup(output,restored,key)
        assert not restored.exists()
        wal.unlink()
    restore_backup(output,restored,key)
    with sqlite3.connect(restored) as con:
        assert con.execute('SELECT content FROM articles').fetchone()[0]=='PRIVATE_SYNTHETIC_MARKER'
        assert con.execute('PRAGMA journal_mode').fetchone()[0]=='delete'


def test_public_process_cannot_encrypt_private_database(tmp_path,monkeypatch):
    from app.encrypted_backup import create_backup
    source=tmp_path/'private.sqlite3';sqlite_database(source)
    key=backup_key(tmp_path/'backup.key')
    monkeypatch.setenv('DIREITO_PUBLIC_READ_ONLY','1')
    with pytest.raises(PermissionError):
        create_backup(source,tmp_path/'vault.dcbackup',key)


def test_legacy_development_backup_treats_uri_characters_as_literal_filename(tmp_path):
    from scripts.backup import backup
    source=tmp_path/'source?literal.sqlite3';sqlite_database(source)
    output=tmp_path/'snapshot.sqlite3'
    backup(output,source)
    with sqlite3.connect(output) as con:
        assert con.execute('SELECT content FROM articles').fetchone()[0]=='PRIVATE_SYNTHETIC_MARKER'
    assert not (tmp_path/'source').exists()
