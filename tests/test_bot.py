import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock
from tests.conftest import make_update, make_context


@pytest.fixture
def config(test_config):
    return test_config


# ------------------------------------------------------------------ #
# owner_only                                                           #
# ------------------------------------------------------------------ #

async def test_owner_only_allows_owner(config, owner_id):
    from hyrax.bot import owner_only

    @owner_only(config)
    async def handler(update, context):
        return "allowed"

    update = make_update(owner_id)
    assert await handler(update, MagicMock()) == "allowed"


async def test_owner_only_blocks_stranger(config):
    from hyrax.bot import owner_only

    @owner_only(config)
    async def handler(update, context):
        return "allowed"

    update = make_update(user_id=999999)
    update.message.reply_text = AsyncMock()
    result = await handler(update, MagicMock())
    assert result is None
    update.message.reply_text.assert_called_once()


# ------------------------------------------------------------------ #
# safe_task                                                            #
# ------------------------------------------------------------------ #

async def test_safe_task_runs_coroutine():
    from hyrax.bot import safe_task
    ran = []

    async def job():
        ran.append(True)

    task = safe_task(job())
    await asyncio.sleep(0)
    await task
    assert ran == [True]


async def test_safe_task_does_not_propagate_exceptions():
    from hyrax.bot import safe_task

    async def bad_job():
        raise ValueError("oh no")

    task = safe_task(bad_job())
    await asyncio.sleep(0.05)
    assert task.done()
    # Exception should NOT propagate — task is done but no unhandled exception


# ------------------------------------------------------------------ #
# handle_message                                                       #
# ------------------------------------------------------------------ #

@pytest.fixture
def deps(test_config):
    brain = MagicMock()
    brain.collect_stream = AsyncMock(return_value="hey there!")
    brain.client = MagicMock()
    brain._history = {42: []}

    async def mock_stream(*args, **kwargs):
        for chunk in ["streaming ", "response"]:
            yield chunk

    brain.stream = mock_stream

    memory = MagicMock()
    memory.read_today = AsyncMock(return_value="")
    memory.read_core = AsyncMock(return_value="")
    memory.summarize_and_save = AsyncMock()

    web = MagicMock()
    web.search = AsyncMock(return_value=[])

    return brain, memory, web


async def test_handle_message_calls_memory_and_brain(test_config, owner_id, deps):
    from hyrax.bot import make_handler
    brain, memory, web = deps

    update = make_update(owner_id, "hello bot")
    context = make_context()

    await make_handler(test_config, brain, memory, web)(update, context)

    memory.read_today.assert_called_once()
    memory.read_core.assert_called_once()
    brain.collect_stream.assert_called_once()


async def test_handle_message_triggers_memory_save(test_config, owner_id, deps):
    from hyrax.bot import make_handler
    brain, memory, web = deps

    update = make_update(owner_id, "my name is Alex")
    context = make_context()

    await make_handler(test_config, brain, memory, web)(update, context)
    await asyncio.sleep(0.05)
    memory.summarize_and_save.assert_called_once()


async def test_handle_message_detects_search_tag(test_config, owner_id, deps):
    from hyrax.bot import make_handler
    brain, memory, web = deps

    brain.collect_stream = AsyncMock(return_value="[SEARCH: python asyncio tutorial]")

    async def mock_stream_second(*args, **kwargs):
        yield "here are the results"

    brain.stream = mock_stream_second
    web.search = AsyncMock(return_value=[
        MagicMock(title="Tutorial", url="http://example.com", snippet="great tutorial")
    ])

    # Enable web search for this test
    import os
    os.environ["SEARXNG_HOST"] = "http://searx.test"
    from importlib import reload
    import hyrax.config as m
    reload(m)
    cfg = m.Settings()
    os.environ.pop("SEARXNG_HOST", None)

    update = make_update(owner_id, "tell me about asyncio")
    context = make_context()

    await make_handler(cfg, brain, memory, web)(update, context)
    web.search.assert_called_once_with("python asyncio tutorial")


async def test_handle_message_skips_search_on_malformed_tag(test_config, owner_id, deps):
    from hyrax.bot import make_handler
    brain, memory, web = deps

    brain.collect_stream = AsyncMock(return_value="[SEARCH:   ] some response")
    update = make_update(owner_id, "question")
    context = make_context()

    await make_handler(test_config, brain, memory, web)(update, context)
    web.search.assert_not_called()


async def test_handle_message_blocks_non_owner(test_config, deps):
    from hyrax.bot import make_handler
    brain, memory, web = deps

    update = make_update(user_id=99999, text="hello")
    context = make_context()

    await make_handler(test_config, brain, memory, web)(update, context)
    brain.collect_stream.assert_not_called()
    memory.read_today.assert_not_called()


async def test_handle_message_handles_ollama_unavailable(test_config, owner_id, deps):
    from hyrax.bot import make_handler
    from hyrax.brain import OllamaUnavailableError
    brain, memory, web = deps

    brain.collect_stream = AsyncMock(side_effect=OllamaUnavailableError("down"))
    update = make_update(owner_id, "hello")
    sent_msg = MagicMock()
    sent_msg.edit_text = AsyncMock()
    context = make_context(send_message_return=sent_msg)

    await make_handler(test_config, brain, memory, web)(update, context)
    sent_msg.edit_text.assert_called()
    call_text = sent_msg.edit_text.call_args[0][0]
    assert "offline" in call_text or "brain" in call_text
