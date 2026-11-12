"""Strict Ed25519 public point validation for the private workflows."""
import ctypes
import ctypes.util
from functools import lru_cache


@lru_cache(maxsize=1)
def _validator():
    try:
        sodium = ctypes.CDLL(ctypes.util.find_library('sodium') or 'libsodium.so')
        initialize = sodium.sodium_init
        initialize.argtypes = []
        initialize.restype = ctypes.c_int
        if initialize() < 0:
            raise RuntimeError('Falha ao inicializar libsodium')
        validate = sodium.crypto_core_ed25519_is_valid_point
    except (OSError, AttributeError) as exc:
        raise RuntimeError('O fluxo privado requer libsodium com validação de pontos Ed25519') from exc
    validate.argtypes = [ctypes.c_char_p]
    validate.restype = ctypes.c_int
    return validate


def validate_ed25519_point(raw):
    if not isinstance(raw, bytes) or len(raw) != 32 or _validator()(raw) != 1:
        raise ValueError('Chave pública Ed25519 inválida ou de ordem pequena')
    return raw
