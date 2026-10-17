"""
Unit tests for configuration loading and validation (tests/test_config.py).
"""

import pytest
from pydantic import ValidationError
from krishibazaar.config import Settings


def test_default_settings_valid():
    cfg = Settings()
    assert cfg.environment in ("development", "staging", "production")
    assert cfg.log_level in ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")
    assert 1024 <= cfg.orchestrator_port <= 65535
    assert 1024 <= cfg.market_agent_port <= 65535
    assert len(cfg.orchestrator_seed) >= 8


def test_invalid_port_validation():
    with pytest.raises(ValidationError):
        Settings(orchestrator_port=80)  # Port under 1024 not allowed

    with pytest.raises(ValidationError):
        Settings(orchestrator_port=70000)  # Port above 65535 not allowed


def test_invalid_environment_validation():
    with pytest.raises(ValidationError):
        Settings(environment="invalid_env")  # type: ignore[arg-type]


def test_custom_settings_override():
    cfg = Settings(
        environment="staging",
        orchestrator_port=9000,
        orchestrator_seed="secure_custom_seed_123",
    )
    assert cfg.environment == "staging"
    assert cfg.orchestrator_port == 9000
    assert cfg.orchestrator_seed == "secure_custom_seed_123"
