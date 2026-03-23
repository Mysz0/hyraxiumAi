import json
import pytest
import httpx
import respx
from unittest.mock import AsyncMock, MagicMock


def make_ollama_line(content: str, done: bool = False) -> bytes:
    return (json.dumps({
        "message": {"role": "assistant", "content": content},
        "done": done,
    }) + "\n").encode()


@pytest.fixture
def brain(test_config):
    from hyrax.brain import Brain
    return Brain(test_config)


async def test_collect_stream_returns_full_response(brain):
    lines = [make_ollama_line("Hello"), make_ollama_line(" world"), make_ollama_line("!", done=True)]
    with respx.mock:
        respx.post(f"{brain._config.ollama_host}/api/chat").mock(
            return_value=httpx.Response(200, content=b"".join(lines))
        )
        result = await brain.collect_stream(1, "hi", "system")
    assert result == "Hello world!"


async def test_stream_yields_chunks(brain):
    lines = [make_ollama_line("chunk1"), make_ollama_line("chunk2"), make_ollama_line("", done=True)]
    with respx.mock:
        respx.post(f"{brain._config.ollama_host}/api/chat").mock(
            return_value=httpx.Response(200, content=b"".join(lines))
        )
        chunks = []
        async for chunk in brain.stream(1, "hi", "system"):
            chunks.append(chunk)
    assert chunks == ["chunk1", "chunk2"]


async def test_history_updated_after_stream(brain):
    lines = [make_ollama_line("response", done=True)]
    with respx.mock:
        respx.post(f"{brain._config.ollama_host}/api/chat").mock(
            return_value=httpx.Response(200, content=b"".join(lines))
        )
        await brain.collect_stream(1, "user message", "system")
    history = brain._history[1]
    assert history[-2] == {"role": "user", "content": "user message"}
    assert history[-1] == {"role": "assistant", "content": "response"}


async def test_reset_history_clears_chat(brain):
    brain._history[1] = [{"role": "user", "content": "test"}]
    brain.reset_history(1)
    assert brain._history.get(1, []) == []


async def test_retry_on_timeout(brain):
    lines = [make_ollama_line("ok", done=True)]
    call_count = 0

    with respx.mock:
        def side_effect(request):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise httpx.TimeoutException("slow")
            return httpx.Response(200, content=b"".join(lines))

        respx.post(f"{brain._config.ollama_host}/api/chat").mock(side_effect=side_effect)
        result = await brain.collect_stream(1, "hi", "system")

    assert result == "ok"
    assert call_count == 2


async def test_raises_unavailable_after_two_timeouts(brain):
    from hyrax.brain import OllamaUnavailableError
    with respx.mock:
        respx.post(f"{brain._config.ollama_host}/api/chat").mock(
            side_effect=httpx.TimeoutException("slow")
        )
        with pytest.raises(OllamaUnavailableError):
            await brain.collect_stream(1, "hi", "system")


async def test_context_window_guard_triggers_summarize(test_config):
    from hyrax.brain import Brain
    mock_memory = MagicMock()
    mock_memory.summarize_and_save = AsyncMock()
    brain = Brain(test_config, memory=mock_memory)

    # CONTEXT_WINDOW_SIZE=4000 in test_config → threshold = 3200 tokens
    # 13000 chars // 4 = 3250 tokens > 3200
    brain._history[1] = [{"role": "user", "content": "x" * 13000}]
    lines = [make_ollama_line("response", done=True)]

    with respx.mock:
        respx.post(f"{brain._config.ollama_host}/api/chat").mock(
            return_value=httpx.Response(200, content=b"".join(lines))
        )
        await brain.collect_stream(1, "hi", "system")

    mock_memory.summarize_and_save.assert_called_once()
    assert len(brain._history.get(1, [])) <= 2


async def test_ephemeral_supplement_not_stored_in_history(brain):
    lines = [make_ollama_line("response", done=True)]
    with respx.mock:
        respx.post(f"{brain._config.ollama_host}/api/chat").mock(
            return_value=httpx.Response(200, content=b"".join(lines))
        )
        await brain.collect_stream(1, "hi", "system", context_supplement="ephemeral search results")

    for msg in brain._history[1]:
        assert "ephemeral search results" not in msg.get("content", "")
