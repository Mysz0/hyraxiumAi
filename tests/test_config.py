import pytest
import os
from pydantic import ValidationError


def test_missing_required_fields():
    env_backup = {k: os.environ.pop(k, None)
                  for k in ["TELEGRAM_TOKEN", "OWNER_TELEGRAM_ID"]}
    try:
        from importlib import reload
        import hyrax.config as m
        reload(m)
        with pytest.raises(Exception):
            m.Settings()
    finally:
        for k, v in env_backup.items():
            if v is not None:
                os.environ[k] = v


def test_defaults(test_config):
    assert test_config.ollama_host == "http://localhost:11434"
    assert test_config.ollama_model == "test-model"
    assert test_config.context_window_size == 4000
    assert test_config.bot_name == "TestHyrax"
    assert test_config.proactive_max_per_day == 1


def test_web_search_disabled_when_empty(test_config):
    assert test_config.searxng_host == ""
    assert test_config.web_search_enabled is False


def test_web_search_enabled_when_set(test_config):
    os.environ["SEARXNG_HOST"] = "http://localhost:8080"
    from importlib import reload
    import hyrax.config as m
    reload(m)
    cfg = m.Settings()
    assert cfg.web_search_enabled is True
    os.environ.pop("SEARXNG_HOST")


def test_proactive_max_per_day_upper_bound(test_config):
    os.environ["PROACTIVE_MAX_PER_DAY"] = "11"
    from importlib import reload
    import hyrax.config as m
    reload(m)
    with pytest.raises(ValidationError):
        m.Settings()
    os.environ["PROACTIVE_MAX_PER_DAY"] = "1"


def test_proactive_max_per_day_lower_bound():
    os.environ.update({
        "TELEGRAM_TOKEN": "tok",
        "OWNER_TELEGRAM_ID": "1",
        "PROACTIVE_MAX_PER_DAY": "-1",
    })
    from importlib import reload
    import hyrax.config as m
    reload(m)
    with pytest.raises(ValidationError):
        m.Settings()
    for k in ["TELEGRAM_TOKEN", "OWNER_TELEGRAM_ID", "PROACTIVE_MAX_PER_DAY"]:
        os.environ.pop(k, None)


def test_system_prompt_contains_personality(test_config):
    prompt = test_config.build_system_prompt(memory_block="")
    assert "Hyrax" in prompt
    assert "hamster" in prompt.lower()


def test_system_prompt_includes_memory_when_present(test_config):
    prompt = test_config.build_system_prompt(memory_block="User likes coffee")
    assert "User likes coffee" in prompt


def test_system_prompt_includes_search_instruction_when_enabled():
    os.environ.update({
        "TELEGRAM_TOKEN": "tok",
        "OWNER_TELEGRAM_ID": "1",
        "SEARXNG_HOST": "http://searx",
    })
    from importlib import reload
    import hyrax.config as m
    reload(m)
    cfg = m.Settings()
    prompt = cfg.build_system_prompt(memory_block="")
    assert "SEARCH" in prompt
    for k in ["TELEGRAM_TOKEN", "OWNER_TELEGRAM_ID", "SEARXNG_HOST"]:
        os.environ.pop(k, None)
