"""Fail-closed protection for the pre-v0.16 bulk publication APIs.

Old APIs remain importable for migration compatibility, but production code may
never call them. Only synthetic pytest fixtures can opt in. An operator with
full write access to the private SQLite is still trusted: this is NOT an
identity-verification or filesystem security boundary. The public runtime uses
a separate read-only snapshot and has no private database mounted.
"""
import os
from threading import Event

# Environment variables alone cannot enable the historical fixture paths.
# The pytest fixture installs a process-local capability shared with its test threads.
_synthetic_fixture_scope = Event()


def require_fixture_legacy(operation: str, *, nonpublishing_fixture: bool = False) -> None:
    if os.environ.get('DIREITO_PUBLIC_READ_ONLY') == '1':
        raise PermissionError('Publicação bloqueada no processo público somente leitura')
    if not (_synthetic_fixture_scope.is_set()
            and (nonpublishing_fixture or os.environ.get('DIREITO_ENABLE_SYNTHETIC_LEGACY_FIXTURES') == '1')
            and os.environ.get('PYTEST_CURRENT_TEST')):
        raise PermissionError(
            f'{operation} desativado desde v0.20.0. '
            'Aprovação integral/importação direta de normas e revisão legada não '
            'são permitidas. Utilize a revisão individual privada; '
            'não há credenciais profissionais verificadas neste serviço.')


def require_private_process() -> None:
    if os.environ.get('DIREITO_PUBLIC_READ_ONLY') == '1':
        raise PermissionError('O processo público somente leitura não pode executar operações privadas')
