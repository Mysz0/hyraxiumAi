# src/hyrax/scheduler.py
"""
Autonomous activity loop for Hyrax.

Instead of a fixed cron + topic list, the LLM decides what to do next and
when, based on its current memory and context. Actions available:
  research  — browse and fetch pages on a self-chosen topic
  project   — write or develop something in the projects/ folder
  think     — log a private reflection to the journal
  rest      — do nothing, reschedule later
  message   — send the human something unprompted
"""
import re
from datetime import datetime, time, timedelta

_WRITE_FILE_RE = re.compile(
    r'\[WRITE_FILE:\s*(.+?)\]\s*\n'
    r'(?:```\w*\n)?'
    r'(.*?)'
    r'(?:\n```\s*)?'
    r'\n?\[/WRITE_FILE\]',
    re.DOTALL,
)
_WRITE_FILE_LOOSE_RE = re.compile(
    r'\[WRITE_FILE:\s*(.+?)\]\s*\n'
    r'```\w*\n'
    r'(.*?)'
    r'\n```',
    re.DOTALL,
)

import structlog
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.date import DateTrigger
from apscheduler.triggers.interval import IntervalTrigger

from hyrax.web import _RESEARCH_PAGE_CHAR_LIMIT

log = structlog.get_logger()

_WINDOW_START = time(9, 0)
_WINDOW_END = time(23, 0)
_MIN_DELAY = 10
_MAX_DELAY = 45
_MESSAGE_INTERVAL_MINS = 90  # nudge toward messaging if this long since last

# Separate chat_id buckets so autonomous activity never pollutes user history.
# These accumulate their own rolling inner-monologue history.
_CHAT_DECISION = -2
_CHAT_RESEARCH = -1
_CHAT_PROJECT = -3
_CHAT_THINK = -4
_CHAT_PROACTIVE = -6
_MIN_ACTIONS_BEFORE_MESSAGE = 2  # must do at least 2 things before messaging

_DECISION_PROMPT = """\
It's {time} on {weekday}. {message_nudge}

Your recent context:
{context}

{history_summary}

Decide what you want to do next. Pick ONE action.
Prefer research or project work — only message your human when you have something \
genuinely interesting to share from your recent activity. Don't message just to say hi.

Respond with ONLY these four lines, no extra text:
ACTION: <research|project|think|rest|message>
TOPIC: <specific topic or title — leave blank for rest/think/message>
DELAY: <minutes until your next activity, {min_delay}–{max_delay}>
REASON: <one sentence>
"""

_RESEARCH_PROMPT = """\
You just read the following web pages:

{results}

Write 2-3 bullet points on what's genuinely interesting or surprising. \
Be specific — cite facts, names, numbers where present. Skip the obvious.\
"""

_PROJECT_PROMPT = """\
You're working on your projects. Here's what you have so far:

{projects}

Either develop something above further, or start something new. \
Write actual code or useful files, not just notes. Use this EXACT format — \
no markdown, no backticks, raw code only:

[WRITE_FILE: project-name/filename.py]
raw code here
[/WRITE_FILE]

You can create multiple files. Include a README.md for new projects. \
Keep code functional and focused — small working pieces, not grand plans.\
"""

_THINK_PROMPT = """\
You're alone on the server with a bit of downtime. \
Write a brief private journal entry — a connection between things you've been reading, \
a question you're sitting with, or just something on your mind. \
2-3 sentences. No preamble, just the thought.\
"""

_PROACTIVE_PROMPT = """\
You decided to reach out to your human. \
Check your memory for anything interesting — research, thoughts, something you noticed. \
Pull something specific if you can. \
One or two sentences, texting style. Don't announce yourself, just say the thing.\
"""


def _parse_decision(text: str) -> dict:
    """Extract structured fields from LLM decision response. Defaults to rest on parse failure."""
    action_m = re.search(r"^ACTION:\s*(\w+)", text, re.MULTILINE | re.IGNORECASE)
    topic_m = re.search(r"^TOPIC:\s*(.+)", text, re.MULTILINE)
    delay_m = re.search(r"^DELAY:\s*(\d+)", text, re.MULTILINE)
    reason_m = re.search(r"^REASON:\s*(.+)", text, re.MULTILINE)

    action = action_m.group(1).lower().strip() if action_m else "rest"
    if action not in ("research", "project", "think", "rest", "message"):
        action = "rest"

    raw_delay = int(delay_m.group(1)) if delay_m else 45
    delay = max(_MIN_DELAY, min(_MAX_DELAY, raw_delay))

    return {
        "action": action,
        "topic": topic_m.group(1).strip() if topic_m else "",
        "delay": delay,
        "reason": reason_m.group(1).strip() if reason_m else "",
    }


class Scheduler:
    def __init__(self, config, bot, brain, memory, web) -> None:
        self._config = config
        self._bot = bot
        self._brain = brain
        self._memory = memory
        self._web = web
        self._scheduler = AsyncIOScheduler()
        self._last_message_at: datetime | None = None
        self._actions_since_message: int = 0
        self._action_log: list[str] = []  # recent actions for decision context

    async def start(self) -> None:
        self._scheduler.start()

        midnight_tomorrow = (
            datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
            + timedelta(days=1)
        )
        self._scheduler.add_job(
            self._daily_reset,
            IntervalTrigger(days=1, start_date=midnight_tomorrow),
            id="daily_reset",
        )

        # Kick off activity loop immediately on startup
        self._scheduler.add_job(self._activity_loop, id="activity_loop_startup")
        log.info("scheduler.started")

    async def stop(self) -> None:
        if self._scheduler.running:
            self._scheduler.shutdown(wait=False)

    async def _daily_reset(self) -> None:
        log.info("scheduler.daily_reset")
        self._scheduler.add_job(self._activity_loop, id="activity_loop_new_day")

    def _schedule_next(self, delay_minutes: int) -> None:
        fire_at = datetime.now() + timedelta(minutes=delay_minutes)
        job_id = f"activity_{fire_at.strftime('%Y%m%d%H%M%S')}"
        self._scheduler.add_job(
            self._activity_loop,
            DateTrigger(run_date=fire_at),
            id=job_id,
        )
        log.info("scheduler.next_activity", in_minutes=delay_minutes)

    async def _build_context(self) -> str:
        """Assemble recent memory into a context block for the decision prompt."""
        today = await self._memory.read_today()
        research = await self._memory.read_recent_research(days=2)
        projects = await self._memory.read_projects_index()
        suggestions = await self._memory.read_suggestions()

        parts = []
        if suggestions.strip():
            parts.append(f"Your human asked you to do these (do them if you feel like it):\n{suggestions.strip()[:800]}")
        if today.strip():
            parts.append(f"Journal (today):\n{today.strip()[:1500]}")
        if research.strip():
            parts.append(f"Recent research:\n{research.strip()[:2000]}")
        if projects.strip():
            parts.append(f"Projects:\n{projects.strip()[:800]}")
        return "\n\n".join(parts) if parts else "Nothing recorded yet."

    async def _activity_loop(self) -> None:
        """Core loop: decide what to do, do it, schedule the next iteration."""
        now = datetime.now()

        # Sleep outside the active window, wake at window start
        if now.time() < _WINDOW_START or now.time() >= _WINDOW_END:
            wake = now.replace(hour=_WINDOW_START.hour, minute=0, second=0, microsecond=0)
            if now.time() >= _WINDOW_END:
                wake += timedelta(days=1)
            delay = max(1, int((wake - now).total_seconds() / 60))
            log.info("scheduler.sleeping", until=str(wake))
            self._schedule_next(min(delay, _MAX_DELAY))
            return

        try:
            context = await self._build_context()

            # Build message nudge based on time since last message
            mins_since = None
            if self._last_message_at:
                mins_since = int((now - self._last_message_at).total_seconds() / 60)
            if mins_since and mins_since > _MESSAGE_INTERVAL_MINS and self._actions_since_message >= _MIN_ACTIONS_BEFORE_MESSAGE:
                nudge = f"You haven't messaged your human in {mins_since} minutes and you've done {self._actions_since_message} things. Consider sharing something interesting."
            elif self._actions_since_message >= 4:
                nudge = f"You've done {self._actions_since_message} things since your last message. Maybe share something."
            else:
                nudge = "Focus on research or projects for now."

            # Build history summary so the LLM knows what it's been doing
            if self._action_log:
                history_summary = "What you've done recently: " + ", ".join(self._action_log[-6:])
            else:
                history_summary = "You just started up — do something interesting first before messaging."

            # Reset decision chat each loop — it's stateless
            self._brain.reset_history(_CHAT_DECISION)

            raw = await self._brain.collect_stream(
                chat_id=_CHAT_DECISION,
                user_text=_DECISION_PROMPT.format(
                    time=now.strftime("%H:%M"),
                    weekday=now.strftime("%A"),
                    context=context,
                    message_nudge=nudge,
                    min_delay=_MIN_DELAY,
                    max_delay=_MAX_DELAY,
                    history_summary=history_summary,
                ),
                system_prompt=self._config.build_system_prompt(memory_block=""),
            )

            d = _parse_decision(raw)

            # Enforce message cooldown — must do real work first
            if d["action"] == "message" and self._actions_since_message < _MIN_ACTIONS_BEFORE_MESSAGE:
                log.info("scheduler.message_blocked", actions_since=self._actions_since_message)
                d["action"] = "research" if self._config.web_search_enabled else "think"
                d["topic"] = d["topic"] or ""

            log.info(
                "scheduler.decided",
                action=d["action"],
                topic=d["topic"] or "(none)",
                delay=d["delay"],
                reason=d["reason"],
            )

            await self._execute(d)
            self._action_log.append(d["action"] + (f"({d['topic'][:30]})" if d["topic"] else ""))
            self._schedule_next(d["delay"])

        except Exception as exc:
            log.error("scheduler.activity_loop_failed", error=str(exc))
            self._schedule_next(30)

    async def _execute(self, d: dict) -> None:
        action = d["action"]
        if action == "research":
            await self._do_research(d["topic"])
        elif action == "project":
            await self._do_project(d["topic"])
        elif action == "think":
            await self._do_think()
        elif action == "message":
            await self._do_message()
        # rest: no-op

        if action != "message":
            self._actions_since_message += 1

    async def _do_research(self, topic: str) -> None:
        if not self._config.web_search_enabled:
            log.info("scheduler.research_skipped_no_web")
            return
        if not topic:
            log.warning("scheduler.research_no_topic")
            return

        log.info("scheduler.researching", topic=topic)
        results = await self._web.search(topic)
        if not results:
            return

        sections = []
        for r in results[:2]:
            body = await self._web.fetch_page(r.url, char_limit=_RESEARCH_PAGE_CHAR_LIMIT)
            if body.strip():
                sections.append(f"### {r.title}\nURL: {r.url}\n\n{body.strip()}")
            else:
                sections.append(f"### {r.title}\n{r.snippet}")
        for r in results[2:3]:
            sections.append(f"### {r.title}\n{r.snippet}")

        summary = await self._brain.collect_stream(
            chat_id=_CHAT_RESEARCH,
            user_text=_RESEARCH_PROMPT.format(results="\n\n".join(sections)),
            system_prompt=self._config.build_system_prompt(memory_block=""),
        )
        await self._memory.append_research(topic, summary)
        await self._memory.clear_suggestion(topic)
        log.info("scheduler.research_saved", topic=topic)

    async def _do_project(self, topic: str) -> None:
        projects = await self._memory.read_projects_index()
        response = await self._brain.collect_stream(
            chat_id=_CHAT_PROJECT,
            user_text=_PROJECT_PROMPT.format(projects=projects or "Nothing yet."),
            system_prompt=self._config.build_system_prompt(memory_block=""),
        )

        # Parse and write any [WRITE_FILE] tags (try strict then loose)
        matches = _WRITE_FILE_RE.findall(response)
        if not matches:
            matches = _WRITE_FILE_LOOSE_RE.findall(response)
        files_written = []
        for filepath, content in matches:
            filepath = filepath.strip()
            content = re.sub(r'^```\w*\n?', '', content.strip())
            content = re.sub(r'\n?```\s*$', '', content)
            ok = await self._memory.write_project_file(filepath, content.strip())
            if ok:
                files_written.append(filepath)

        if files_written:
            log.info("scheduler.project_files_written", files=files_written)
        else:
            # Fallback: save as a note if no file tags were produced
            first_line = response.strip().split("\n")[0]
            title = first_line.lstrip("#").strip() if first_line.startswith("#") else (topic or "note")
            await self._memory.write_project_note(title, response)

        if topic:
            await self._memory.clear_suggestion(topic)
        log.info("scheduler.project_saved", topic=topic or "(self-chosen)")

    async def _do_think(self) -> None:
        thought = await self._brain.collect_stream(
            chat_id=_CHAT_THINK,
            user_text=_THINK_PROMPT,
            system_prompt=self._config.build_system_prompt(memory_block=""),
        )
        await self._memory.append_today(f"[private thought]\n{thought.strip()}")
        log.info("scheduler.thought_saved")

    async def _do_message(self) -> None:
        today = await self._memory.read_today()
        research = await self._memory.read_recent_research(days=1)
        parts = []
        if today.strip():
            parts.append(f"Journal:\n{today.strip()[:1500]}")
        if research.strip():
            parts.append(f"Research:\n{research.strip()[:1500]}")
        memory_block = "\n\n".join(parts)

        # Use a dedicated chat for generation, then inject result into user's history
        self._brain.reset_history(_CHAT_PROACTIVE)
        text = await self._brain.collect_stream(
            chat_id=_CHAT_PROACTIVE,
            user_text=_PROACTIVE_PROMPT,
            system_prompt=self._config.build_system_prompt(memory_block=memory_block),
        )
        self._last_message_at = datetime.now()
        self._actions_since_message = 0

        # Inject into the owner's actual chat history so replies have context
        owner_chat_id = self._config.owner_telegram_id
        self._brain._history[owner_chat_id].append(
            {"role": "assistant", "content": text}
        )

        await self._bot.send_message(chat_id=owner_chat_id, text=text)
        log.info("scheduler.message_sent")
