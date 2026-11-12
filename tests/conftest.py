"""Enable old fake-law exercises only inside pytest synthetic fixtures.

Not an operational or legal publication entitlement. Production runtime refuses
all writes because it mounts a separate SQLite snapshot read-only.
"""
import pytest
from app.publication_boundary import _synthetic_fixture_scope

@pytest.fixture(autouse=True)
def enable_synthetic_legacy_fixtures(monkeypatch):
    monkeypatch.setenv('DIREITO_ENABLE_SYNTHETIC_LEGACY_FIXTURES','1')
    _synthetic_fixture_scope.set()
    try:
        yield
    finally:
        _synthetic_fixture_scope.clear()
