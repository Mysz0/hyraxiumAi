# src/hyrax/bot.py
"""
Hyrax Telegram message handler.

Contains:
  owner_only(config)  — decorator that blocks non-owner users
  safe_task(coro)     — asyncio.create_task with error logging
  make_handler(...)   — factory returning the main message handler
"""
import asyncio
import re
import time
from collections.abc import Coroutine
from functools import wraps

import structlog
from telegram import Update
from telegram.ext import CallbackContext

log = structlog.get_logger()

_TELEGRAM_MSG_LIMIT = 4000  # stay well under Telegram's 4096-char hard limit
_SEARCH_RE = re.compile(r'\[SEARCH:\s*(.+?)\]')
# Strict: with closing tag. Loose: code fence acts as implicit close.
_WRITE_FILE_RE = re.compile(
    r'\[WRITE_FILE:\s*(.+?)\]\s*\n'
    r'(?:```\w*\n)?'       # optional opening code fence
    r'(.*?)'
    r'(?:\n```\s*)?'        # optional closing code fence
    r'\n?\[/WRITE_FILE\]',
    re.DOTALL,
)
_WRITE_FILE_LOOSE_RE = re.compile(
    r'\[WRITE_FILE:\s*(.+?)\]\s*\n'
    r'```\w*\n'             # opening fence (required for unclosed match)
    r'(.*?)'
    r'\n```',               # closing fence acts as implicit end
    re.DOTALL,
)
_THINKING_TEXTS = ["thinking.", "thinking..", "thinking..."]


# ------------------------------------------------------------------ #
# Utilities                                                            #
# ------------------------------------------------------------------ #

def owner_only(config):
    """
    Decorator factory. Wraps a PTB handler so only the owner can use it.
    Usage: @owner_only(config)
    """
    def decorator(handler):
        @wraps(handler)
        async def wrapper(update: Update, context: CallbackContext, *args, **kwargs):
            if update.effective_user.id != config.owner_telegram_id:
                await update.message.reply_text("nope")
                return None
            return await handler(update, context, *args, **kwargs)
        return wrapper
    return decorator


def _on_task_done(task: asyncio.Task) -> None:
    """Error callback for safe_task — logs exceptions without propagating them."""
    if not task.cancelled() and task.exception():
        log.error("background task failed", exc_info=task.exception())


def safe_task(coro: Coroutine) -> asyncio.Task:
    """Create a fire-and-forget task that logs exceptions instead of swallowing them."""
    task = asyncio.create_task(coro)
    task.add_done_callback(_on_task_done)
    return task


# ------------------------------------------------------------------ #
# Message handler                                                      #
# ------------------------------------------------------------------ #

async def _thinking_loop(msg, stop_event: asyncio.Event) -> None:
    """Edit the placeholder message with animated dots while brain thinks."""
    i = 0
    while not stop_event.is_set():
        try:
            await msg.edit_text(_THINKING_TEXTS[i % len(_THINKING_TEXTS)])
        except Exception:
            pass
        i += 1
        await asyncio.sleep(2.0)


async def _stream_to_telegram(msg, stream_gen, context, chat_id: int) -> None:
    """
    Read chunks from an async generator and progressively edit msg.
    Splits into new messages when approaching Telegram's 4096-char limit.
    """
    buffer = ""
    last_edit = time.monotonic()

    async for chunk in stream_gen:
        buffer += chunk
        now = time.monotonic()

        if len(buffer) >= _TELEGRAM_MSG_LIMIT:
            try:
                await msg.edit_text(buffer)
            except Exception:
                pass
            msg = await context.bot.send_message(chat_id, "▌")
            buffer = ""
            last_edit = now
            continue

        if now - last_edit >= 1.0:
            try:
                await msg.edit_text(buffer + "▌")
            except Exception:
                pass
            last_edit = now

    if buffer:
        try:
            await msg.edit_text(buffer)
        except Exception:
            pass


def make_handler(config, brain, memory, web):
    """
    Factory that creates the handle_message PTB handler with injected dependencies.
    Returns an async function compatible with MessageHandler(filters.TEXT, handler).
    """

    @owner_only(config)
    async def handle_message(update: Update, context: CallbackContext) -> None:
        chat_id = update.effective_chat.id
        user_text = update.message.text

        # Build system prompt with current memory
        today_mem = await memory.read_today()
        core_mem = await memory.read_core()
        research = await memory.read_recent_research()
        memory_block = "\n\n".join(filter(None, [core_mem, today_mem, research]))
        system_prompt = config.build_system_prompt(memory_block=memory_block)

        # Send placeholder and start thinking animation
        msg = await context.bot.send_message(chat_id, "thinking.")
        stop_thinking = asyncio.Event()
        thinking_task = safe_task(_thinking_loop(msg, stop_thinking))

        # First pass: collect full response to detect [SEARCH:] tag
        from hyrax.brain import OllamaUnavailableError
        try:
            first_response = await brain.collect_stream(chat_id, user_text, system_prompt)
        except OllamaUnavailableError:
            stop_thinking.set()
            thinking_task.cancel()
            await msg.edit_text("brain's offline rn, try again in a bit 😵")
            return
        finally:
            stop_thinking.set()
            thinking_task.cancel()

        # Web search detection — scan full response for [SEARCH: query]
        match = _SEARCH_RE.search(first_response)
        query = match.group(1).strip() if match else ""

        if query and config.web_search_enabled:
            try:
                await msg.edit_text("🔍 searching...")
            except Exception:
                pass

            search_results = await web.search(query)
            supplement_lines = [f"Search results for '{query}':"]
            for r in search_results:
                supplement_lines.append(f"- [{r.title}]({r.url}): {r.snippet}")
            context_supplement = "\n".join(supplement_lines)

            # Second pass: stream the real answer with search context injected
            await _stream_to_telegram(
                msg,
                brain.stream(chat_id, user_text, system_prompt, context_supplement=context_supplement),
                context,
                chat_id,
            )
        else:
            # No search — process file tags and display the response
            display_text, files = await _process_files(first_response)

            async def _replay():
                yield display_text

            await _stream_to_telegram(msg, _replay(), context, chat_id)

            if files:
                file_list = "\n".join(f"  {f}" for f in files)
                await context.bot.send_message(chat_id, f"wrote files:\n{file_list}")

        # Background: summarize and save notable facts to memory
        safe_task(memory.summarize_and_save(brain._history.get(chat_id, []), brain.client))
        # Background: check if the user asked Hyrax to do something later
        safe_task(_extract_suggestion(user_text, first_response))

    async def _process_files(response_text: str) -> tuple[str, list[str]]:
        """Parse [WRITE_FILE] tags, write files, return cleaned text and list of written paths."""
        # Try strict pattern first, fall back to loose (unclosed tag with code fences)
        matches = _WRITE_FILE_RE.findall(response_text)
        clean_re = _WRITE_FILE_RE
        if not matches:
            matches = _WRITE_FILE_LOOSE_RE.findall(response_text)
            clean_re = _WRITE_FILE_LOOSE_RE

        written = []
        for filepath, content in matches:
            filepath = filepath.strip()
            # Strip any markdown fences the LLM snuck in
            content = re.sub(r'^```\w*\n?', '', content.strip())
            content = re.sub(r'\n?```\s*$', '', content)
            ok = await memory.write_project_file(filepath, content.strip())
            if ok:
                written.append(filepath)

        cleaned = clean_re.sub(lambda m: f"[created: {m.group(1).strip()}]", response_text)
        return cleaned, written

    async def _extract_suggestion(user_text: str, bot_response: str) -> None:
        """Check if the user asked Hyrax to do something and save it as a suggestion."""
        try:
            # Reset history each time — this is a stateless extraction, not a conversation
            brain.reset_history(-5)
            result = await brain.collect_stream(
                chat_id=-5,
                user_text=(
                    f"USER: {user_text}\nBOT: {bot_response[:500]}\n\n"
                    "Did the user ask or suggest the bot do something later "
                    "(research a topic, start a project, look into something, check something out)? "
                    "If yes, reply with ONLY the task in a few words. If no, reply with NONE."
                ),
                system_prompt="You extract tasks from conversations. Reply with just the task or NONE. No other text.",
            )
            cleaned = result.strip().lstrip("-•* ").rstrip(".")
            if cleaned and cleaned.upper() != "NONE" and len(cleaned) < 200 and "NONE" not in cleaned.upper():
                await memory.append_suggestion(cleaned)
        except Exception as exc:
            log.warning("bot.suggestion_extract_failed", error=str(exc))

    return handle_message
