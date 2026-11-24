from datetime import timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from familiar.config import load_config, to_router_config, to_safety_config
from familiar.gemini.model_router import Role

VALID = """
gemini:
  models:
    lite: { rpm: 0, rpd: 0 }
    flash: { rpm: 5, rpd: 20 }
    hydrogen: { rpd: 20 }
  backend: custom_fc
  chains:
    action: [lite, flash]
    planning: [hydrogen, flash]
  overload: { retries: 2, backoff_sec: [1, 2], fallback_retries: 0, fallback_backoff_sec: 3, cooldown_initial_sec: 30, cooldown_max_min: 5 }
  decision: { model: decision-1, rpm: 200, rpd: 10000, tpm: 50000 }
browser:
  blocked_domains: [bank.example]
unknown_section_is_ignored: true
"""


def write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "config.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def test_loads_and_converts(tmp_path):
    config = load_config(write(tmp_path, VALID))
    router = to_router_config(config)
    assert router.chains[Role.ACTION] == ("lite", "flash")
    assert router.chains[Role.DECISION] == ("decision-1",)
    assert router.limits["flash"].rpm == 5
    assert router.limits["decision-1"].tpm == 50000
    assert router.cooldown_initial == timedelta(seconds=30)
    assert router.cooldown_max == timedelta(minutes=5)
    assert router.backoff_sec == (1.0, 2.0)
    assert (router.fallback_retries, router.fallback_backoff_sec) == (0, 3.0)
    assert to_safety_config(config).blocked_domains == ("bank.example",)


def test_chain_must_reference_known_model(tmp_path):
    text = VALID.replace("action: [lite, flash]", "action: [lite, ghost]")
    with pytest.raises(ValidationError, match="ghost"):
        load_config(write(tmp_path, text))


def test_empty_chain_rejected(tmp_path):
    text = VALID.replace("action: [lite, flash]", "action: []")
    with pytest.raises(ValidationError):
        load_config(write(tmp_path, text))


def test_invalid_backend_rejected(tmp_path):
    with pytest.raises(ValidationError):
        load_config(write(tmp_path, VALID.replace("backend: custom_fc", "backend: magic")))


def test_missing_file_has_clear_error(tmp_path):
    with pytest.raises(FileNotFoundError, match="config"):
        load_config(tmp_path / "nope.yaml")


def test_repository_config_is_valid():
    config = load_config(Path(__file__).parents[2] / "config.yaml")
    assert to_router_config(config).chains[Role.ACTION][0] == "gemini-3.5-flash-lite"
