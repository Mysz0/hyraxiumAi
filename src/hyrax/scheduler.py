# src/hyrax/scheduler.py
"""
Proactive message scheduler for Hyrax.

Uses APScheduler 3.x AsyncIOScheduler. Started inside PTB's post_init hook
to share the running asyncio event loop.

Daily strategy:
  1. On startup: schedule PROACTIVE_MAX_PER_DAY one-shot jobs at random
     times within today's 09:00–22:00 window (skipping past times).
  2. At midnight each day: reschedule for the next day.
"""
import asyncio
import random
from datetime import datetime, time, timedelta

import structlog
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.date import DateTrigger
from apscheduler.triggers.interval import IntervalTrigger

log = structlog.get_logger()

_WINDOW_START = time(9, 0)
_WINDOW_END = time(22, 0)

_PROACTIVE_PROMPT = (
    "You haven't heard from your human in a while. "
    "Send them a short, in-character message — funny, weird, or just checking in. "
    "Keep it under 3 sentences. No hashtags."
)


def _random_time_today() -> datetime:
    """Return a random datetime today between 09:00 and 22:00."""
    now = datetime.now()
    start = now.replace(hour=_WINDOW_START.hour, minute=0, second=0, microsecond=0)
    end = now.replace(hour=_WINDOW_END.hour, minute=0, second=0, microsecond=0)
    delta_seconds = int((end - start).total_seconds())
    random_seconds = random.randint(0, delta_seconds)
    return start + timedelta(seconds=random_seconds)


class Scheduler:
    def __init__(self, config, bot, brain) -> None:
        self._config = config
        self._bot = bot
        self._brain = brain
        self._scheduler = AsyncIOScheduler()
        self._proactive_job_ids: list[str] = []

    async def start(self) -> None:
        """Start the scheduler and schedule today's proactive messages."""
        self._scheduler.start()

        # Midnight daily reset job
        midnight_tomorrow = datetime.now().replace(
            hour=0, minute=0, second=0, microsecond=0
        ) + timedelta(days=1)
        self._scheduler.add_job(
            self._daily_reset,
            IntervalTrigger(days=1, start_date=midnight_tomorrow),
            id="daily_reset",
        )

        await self._schedule_today()
        log.info("scheduler.started", proactive_per_day=self._config.proactive_max_per_day)

    async def stop(self) -> None:
        if self._scheduler.running:
            self._scheduler.shutdown(wait=False)

    async def _daily_reset(self) -> None:
        """Called at midnight — cancel old jobs and schedule new ones for today."""
        for job_id in self._proactive_job_ids:
            try:
                self._scheduler.remove_job(job_id)
            except Exception:
                pass
        self._proactive_job_ids = []
        await self._schedule_today()

    async def _schedule_today(self) -> None:
        """Schedule PROACTIVE_MAX_PER_DAY one-shot jobs at random times today."""
        now = datetime.now()
        count = 0

        for i in range(self._config.proactive_max_per_day):
            fire_at = _random_time_today()
            if fire_at <= now:
                continue  # Skip times already past
            await self._add_proactive_job(fire_at, i)
            count += 1

        log.info("scheduler.scheduled_today", jobs=count)

    async def _add_proactive_job(self, fire_at: datetime, index: int = 0) -> None:
        """Add a single one-shot proactive message job to the scheduler."""
        job_id = f"proactive_{fire_at.strftime('%H%M%S')}_{index}"
        self._scheduler.add_job(
            self._send_proactive_message,
            DateTrigger(run_date=fire_at),
            id=job_id,
        )
        self._proactive_job_ids.append(job_id)

    async def _send_proactive_message(self) -> None:
        """Generate a proactive message via Ollama and send it to the owner."""
        try:
            text = await self._brain.collect_stream(
                chat_id=0,
                user_text=_PROACTIVE_PROMPT,
                system_prompt=self._config.build_system_prompt(memory_block=""),
            )
            await self._bot.send_message(
                chat_id=self._config.owner_telegram_id,
                text=text,
            )
            log.info("scheduler.proactive_sent")
        except Exception as exc:
            log.error("scheduler.proactive_failed", error=str(exc))
