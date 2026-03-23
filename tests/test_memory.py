import pytest
import httpx
from datetime import date
from unittest.mock import AsyncMock, patch


@pytest.fixture
def mem(test_config):
    from hyrax.memory import Memory
    return Memory(test_config)


async def test_init_creates_directory(mem, tmp_path):
    mem_dir = tmp_path / "memory"
    assert not mem_dir.exists()
    await mem.init()
    assert mem_dir.exists()


async def test_init_raises_on_non_writable_dir(mem, tmp_path):
    from hyrax.memory import MemoryConfigError
    mem_dir = tmp_path / "memory"
    mem_dir.mkdir()
    mem_dir.chmod(0o444)
    try:
        with pytest.raises(MemoryConfigError):
            await mem.init()
    finally:
        mem_dir.chmod(0o755)


async def test_read_today_returns_empty_when_missing(mem):
    await mem.init()
    assert await mem.read_today() == ""


async def test_read_core_returns_empty_when_missing(mem):
    await mem.init()
    assert await mem.read_core() == ""


async def test_append_today_creates_and_appends(mem):
    await mem.init()
    await mem.append_today("first note")
    await mem.append_today("second note")
    content = await mem.read_today()
    assert "first note" in content
    assert "second note" in content


async def test_write_and_read_core(mem):
    await mem.init()
    await mem.write_core("Name: Alex")
    assert "Name: Alex" in await mem.read_core()


async def test_clear_core_removes_file(mem, tmp_path):
    await mem.init()
    await mem.write_core("some facts")
    await mem.clear_core()
    assert not (tmp_path / "memory" / "core.md").exists()
    assert await mem.read_core() == ""


async def test_summarize_and_save_appends_when_not_nothing(mem):
    await mem.init()
    history = [
        {"role": "user", "content": "my name is Alex"},
        {"role": "assistant", "content": "nice to meet you Alex!"},
    ]
    mock_client = AsyncMock(spec=httpx.AsyncClient)
    with patch.object(mem, "_call_ollama_for_summary", new=AsyncMock(return_value="- User's name is Alex")):
        await mem.summarize_and_save(history, mock_client)
    assert "User's name is Alex" in await mem.read_today()


async def test_summarize_and_save_skips_on_nothing(mem):
    await mem.init()
    history = [{"role": "user", "content": "hey"}, {"role": "assistant", "content": "hey!"}]
    mock_client = AsyncMock(spec=httpx.AsyncClient)
    with patch.object(mem, "_call_ollama_for_summary", new=AsyncMock(return_value="NOTHING")):
        await mem.summarize_and_save(history, mock_client)
    assert await mem.read_today() == ""


async def test_read_today_survives_io_error(mem, tmp_path):
    await mem.init()
    today_file = tmp_path / "memory" / f"{date.today().isoformat()}.md"
    today_file.write_text("some data")
    today_file.chmod(0o000)
    try:
        assert await mem.read_today() == ""
    finally:
        today_file.chmod(0o644)
