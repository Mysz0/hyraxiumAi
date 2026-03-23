import pytest
from unittest.mock import AsyncMock, MagicMock
from tests.conftest import make_update


@pytest.fixture
def brain_mock():
    b = MagicMock()
    b.reset_history = MagicMock()
    return b


@pytest.fixture
def memory_mock():
    m = MagicMock()
    m.read_today = AsyncMock(return_value="## [14:00] Notes\n- user likes coffee")
    m.read_core = AsyncMock(return_value="# Core\n- Name: Alex")
    m.clear_core = AsyncMock()
    return m


async def test_start_replies(test_config, owner_id, brain_mock, memory_mock):
    from hyrax.commands import make_commands
    cmds = make_commands(test_config, brain_mock, memory_mock)
    update = make_update(owner_id)
    await cmds["start"](update, MagicMock())
    update.message.reply_text.assert_called_once()
    text = update.message.reply_text.call_args[0][0]
    assert "Hyrax" in text or "hyrax" in text.lower()


async def test_help_lists_commands(test_config, owner_id, brain_mock, memory_mock):
    from hyrax.commands import make_commands
    cmds = make_commands(test_config, brain_mock, memory_mock)
    update = make_update(owner_id)
    await cmds["help"](update, MagicMock())
    text = update.message.reply_text.call_args[0][0]
    for cmd in ["/start", "/help", "/memory", "/forget", "/reset", "/status"]:
        assert cmd in text


async def test_memory_shows_content(test_config, owner_id, brain_mock, memory_mock):
    from hyrax.commands import make_commands
    cmds = make_commands(test_config, brain_mock, memory_mock)
    update = make_update(owner_id)
    await cmds["memory"](update, MagicMock())
    text = update.message.reply_text.call_args[0][0]
    assert "coffee" in text or "Alex" in text


async def test_forget_clears_core(test_config, owner_id, brain_mock, memory_mock):
    from hyrax.commands import make_commands
    cmds = make_commands(test_config, brain_mock, memory_mock)
    update = make_update(owner_id)
    await cmds["forget"](update, MagicMock())
    memory_mock.clear_core.assert_called_once()
    update.message.reply_text.assert_called_once()


async def test_reset_clears_brain_history(test_config, owner_id, brain_mock, memory_mock):
    from hyrax.commands import make_commands
    cmds = make_commands(test_config, brain_mock, memory_mock)
    update = make_update(owner_id, chat_id=42)
    await cmds["reset"](update, MagicMock())
    brain_mock.reset_history.assert_called_once_with(42)


async def test_status_replies_with_model_info(test_config, owner_id, brain_mock, memory_mock):
    from hyrax.commands import make_commands
    import httpx
    import respx
    cmds = make_commands(test_config, brain_mock, memory_mock)
    update = make_update(owner_id)
    with respx.mock:
        respx.get(f"{test_config.ollama_host}/api/tags").mock(return_value=httpx.Response(200, json={}))
        await cmds["status"](update, MagicMock())
    text = update.message.reply_text.call_args[0][0]
    assert "test-model" in text


async def test_commands_block_non_owner(test_config, brain_mock, memory_mock):
    from hyrax.commands import make_commands
    cmds = make_commands(test_config, brain_mock, memory_mock)
    update = make_update(user_id=99999)
    update.message.reply_text = AsyncMock()
    await cmds["memory"](update, MagicMock())
    memory_mock.read_today.assert_not_called()
