import pytest
from datetime import time
from unittest.mock import AsyncMock, MagicMock, patch


@pytest.fixture
def scheduler(test_config):
    from hyrax.scheduler import Scheduler
    bot = MagicMock()
    bot.send_message = AsyncMock()
    brain = MagicMock()
    brain.collect_stream = AsyncMock(return_value="hey human")
    sched = Scheduler(test_config, bot, brain)
    return sched, bot, brain


async def test_schedule_today_creates_correct_number_of_jobs(scheduler):
    sched, bot, brain = scheduler
    with patch.object(sched, "_add_proactive_job", new=AsyncMock()) as mock_add:
        # Patch _random_time_today to always return future time
        from datetime import datetime, timedelta
        future = datetime.now() + timedelta(hours=1)
        with patch("hyrax.scheduler._random_time_today", return_value=future):
            await sched._schedule_today()
    # PROACTIVE_MAX_PER_DAY=1 in test_config
    assert mock_add.call_count == 1


async def test_schedule_today_respects_zero_frequency(test_config):
    import os
    os.environ["PROACTIVE_MAX_PER_DAY"] = "0"
    from importlib import reload
    import hyrax.config as m
    reload(m)
    cfg = m.Settings()
    os.environ["PROACTIVE_MAX_PER_DAY"] = "1"

    from hyrax.scheduler import Scheduler
    sched = Scheduler(cfg, MagicMock(), MagicMock())
    with patch.object(sched, "_add_proactive_job", new=AsyncMock()) as mock_add:
        await sched._schedule_today()
    assert mock_add.call_count == 0


def test_random_time_in_window():
    from hyrax.scheduler import _random_time_today
    for _ in range(20):
        dt = _random_time_today()
        assert time(9, 0) <= dt.time() <= time(22, 0)


async def test_proactive_message_sends_to_owner(scheduler):
    sched, bot, brain = scheduler
    brain.collect_stream = AsyncMock(return_value="miss you human 🐹")
    await sched._send_proactive_message()
    bot.send_message.assert_called_once()
    call_kwargs = bot.send_message.call_args[1]
    assert call_kwargs["chat_id"] == sched._config.owner_telegram_id
    assert call_kwargs["text"] == "miss you human 🐹"
