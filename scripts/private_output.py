"""Write completed private reports atomically with owner-only permissions."""
import os
import stat
from pathlib import Path
from tempfile import mkstemp


def write_private_text(path, text, *, replace=False):
    path = Path(path)
    try:
        existing = path.lstat()
    except FileNotFoundError:
        existing = None
    if existing is not None and (not replace or not stat.S_ISREG(existing.st_mode)):
        raise ValueError('O destino privado já existe ou não é arquivo regular')

    fd, name = mkstemp(prefix='.direito-private-', suffix='.tmp', dir=path.parent)
    temp = Path(name)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8', newline='\n') as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        if existing is None:
            # The link is atomic and refuses to overwrite a concurrently created path.
            os.link(temp, path)
        else:
            current = path.lstat()
            if not stat.S_ISREG(current.st_mode) or not os.path.samestat(existing, current):
                raise ValueError('O destino privado mudou durante a gravação')
            os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)
    return path
