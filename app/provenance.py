"""Private HTTP acquisition receipt. No receipt certifies legal force or completeness."""
import hashlib
from datetime import datetime, timedelta
from urllib.parse import urlparse

ALLOWED_HOSTS = frozenset(('www.planalto.gov.br', 'planalto.gov.br'))


def validate_receipt(receipt, raw):
    """Validate a recorded HTTPS response against the raw file, without claiming freshness."""
    if not isinstance(receipt, dict):
        raise ValueError('Comprovante HTTP obrigatório para aquisição network')
    required = ('requested_url', 'final_url', 'http_status', 'mime', 'length_bytes',
                'sha256', 'fetched_at_utc', 'redirects')
    if any(k not in receipt for k in required):
        raise ValueError('Comprovante HTTP incompleto')
    redirects = receipt['redirects']
    if not isinstance(redirects, list) or len(redirects) > 4:
        raise ValueError('Cadeia de redirecionamentos inválida')
    for url in [receipt['requested_url'], *redirects, receipt['final_url']]:
        if not isinstance(url, str) or len(url)>1500:
            raise ValueError('URL inválida no comprovante HTTP')
        parsed = urlparse(url)
        if (parsed.scheme != 'https' or parsed.hostname not in ALLOWED_HOSTS
                or parsed.username or parsed.password or parsed.port not in (None, 443)):
            raise ValueError('Comprovante contém URL fora do domínio oficial autorizado')
    if receipt['http_status'] != 200:
        raise ValueError('Somente captura HTTP 200 é permitida')
    mime = receipt['mime']
    if (not isinstance(mime, str)
            or mime.partition(';')[0].strip().lower() not in ('text/html', 'application/xhtml+xml')):
        raise ValueError('MIME de aquisição não reconhecido')
    if type(receipt['length_bytes']) is not int or receipt['length_bytes'] != len(raw):
        raise ValueError('Comprimento dos bytes não confere')
    if receipt['sha256'] != hashlib.sha256(raw).hexdigest():
        raise ValueError('SHA-256 da aquisição não confere')
    stamp = receipt['fetched_at_utc']
    if not isinstance(stamp, str) or len(stamp)>40 or 'T' not in stamp:
        raise ValueError('Data UTC inválida')
    try:
        fetched_at = datetime.fromisoformat(stamp)
    except ValueError as exc:
        raise ValueError('Data UTC inválida') from exc
    if fetched_at.utcoffset() != timedelta(0):
        raise ValueError('Data UTC precisa de fuso UTC explícito')
    return {key: receipt[key] for key in required}
