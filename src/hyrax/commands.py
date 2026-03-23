# src/hyrax/commands.py
"""
Command handlers for Hyrax.
All handlers are built via make_commands() which injects dependencies.
All handlers are protected by @owner_only.
"""
import time as _time_module

import httpx
import structlog
from telegram import Update
from telegram.ext import CallbackContext

from hyrax.bot import owner_only

log = structlog.get_logger()

_START_TIME = _time_module.time()


def make_commands(config, brain, memory) -> dict:
    """
    Build all command handlers with injected dependencies.
    Returns a dict mapping command name → async handler function.
    """

    @owner_only(config)
    async def start(update: Update, context: CallbackContext) -> None:
        await update.message.reply_text(
            f"oh hey!! i'm {config.bot_name}, your personal AI hamster 🐹\n\n"
            f"i'm wired into a local LLM and i remember stuff between conversations. "
            f"talk to me whenever, i'm literally always here (no life lol)\n\n"
            f"type /help to see what i can do"
        )

    @owner_only(config)
    async def help_cmd(update: Update, context: CallbackContext) -> None:
        await update.message.reply_text(
            "here's the rundown:\n\n"
            "/start — say hi\n"
            "/help — this thing you're reading\n"
            "/memory — see what i remember about you\n"
            "/forget — wipe my permanent memory (drama ensues)\n"
            "/reset — clear today's conversation context\n"
            "/status — see my current setup and health"
        )

    @owner_only(config)
    async def memory_cmd(update: Update, context: CallbackContext) -> None:
        today = await memory.read_today()
        core = await memory.read_core()

        parts = []
        if core.strip():
            parts.append(f"**permanent memory:**\n```\n{core.strip()}\n```")
        if today.strip():
            parts.append(f"**today's notes:**\n```\n{today.strip()}\n```")

        if parts:
            await update.message.reply_text(
                "\n\n".join(parts),
                parse_mode="Markdown",
            )
        else:
            await update.message.reply_text(
                "my memory is blank... we literally just met i think?? 🤔"
            )

    @owner_only(config)
    async def forget(update: Update, context: CallbackContext) -> None:
        await memory.clear_core()
        await update.message.reply_text(
            "ok fine. i deleted everything i knew about you. "
            "you're a stranger to me now. this is fine. i'm fine. 🙂🔥"
        )

    @owner_only(config)
    async def reset(update: Update, context: CallbackContext) -> None:
        chat_id = update.effective_chat.id
        brain.reset_history(chat_id)
        await update.message.reply_text(
            "wait what were we talking about? ...never mind, fresh start i guess 🤷"
        )

    @owner_only(config)
    async def status(update: Update, context: CallbackContext) -> None:
        uptime_secs = int(_time_module.time() - _START_TIME)
        hours, rem = divmod(uptime_secs, 3600)
        minutes, secs = divmod(rem, 60)

        today_mem = await memory.read_today()
        core_mem = await memory.read_core()

        # Ping Ollama to check reachability
        ollama_ok = False
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    f"{config.ollama_host}/api/tags", timeout=3.0
                )
                ollama_ok = resp.status_code == 200
        except Exception:
            pass

        lines = [
            f"**{config.bot_name} status**",
            f"model: `{config.ollama_model}`",
            f"context window: `{config.context_window_size:,}` tokens",
            f"ollama: {'online ✅' if ollama_ok else 'offline ❌'}",
            f"web search: {'enabled ✅' if config.web_search_enabled else 'disabled ❌'}",
            f"uptime: `{hours}h {minutes}m {secs}s`",
            f"today's memory: `{len(today_mem)}` chars",
            f"core memory: `{len(core_mem)}` chars",
        ]
        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

    return {
        "start": start,
        "help": help_cmd,
        "memory": memory_cmd,
        "forget": forget,
        "reset": reset,
        "status": status,
    }
