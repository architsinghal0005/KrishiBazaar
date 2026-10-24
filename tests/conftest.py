"""
KrishiBazaar Pytest Suite Configuration.
Ensures comprehensive storage isolation so no test run ever pollutes or alters the production database.
"""

from pathlib import Path
import asyncio
try:
    asyncio.get_event_loop()
except RuntimeError:
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

import pytest
from krishibazaar.storage import StorageEngine


@pytest.fixture(autouse=True)
def isolate_test_storage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """
    Globally isolate test database for all tests across the pytest suite.
    Ensures no test ever reads, writes, or modifies the production/runtime database (krishibazaar.db).
    """
    test_db_file = tmp_path / "test_isolated_suite.db"
    test_storage = StorageEngine(db_path=test_db_file)
    monkeypatch.setattr("krishibazaar.storage.storage", test_storage)
    monkeypatch.setattr("krishibazaar.agents.orchestrator.storage", test_storage)
    monkeypatch.setattr("krishibazaar.config.settings.enable_payment_protocol", False)
    yield test_storage

