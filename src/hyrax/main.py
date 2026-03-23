# src/hyrax/main.py
"""
Entry point for Hyrax.

Wires all modules together and starts the PTB Application.
The post_init callback initializes memory directory validation
and starts the APScheduler proactive message scheduler.
"""
import logging

import structlog
from telegram import BotCommand
from telegram.ext import Application, CommandHandler, MessageHandler, filters

from hyrax.config import Settings
from hyrax.memory import Memory
from hyrax.brain import Brain
from hyrax.web import Web
from hyrax.bot import make_handler
from hyrax.commands import make_commands
from hyrax.scheduler import Scheduler

structlog.configure(
    wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
)
logging.basicConfig(level=logging.WARNING)

log = structlog.get_logger()


def main() -> None:
    config = Settings()
    log.info("hyrax.starting", model=config.ollama_model, bot_name=config.bot_name)

    memory = Memory(config)
    brain = Brain(config, memory=memory)
    web = Web(config)

    app = Application.builder().token(config.telegram_token).build()
    scheduler = Scheduler(config, bot=app.bot, brain=brain, memory=memory, web=web)
    
    async def post_init(application: Application) -> None:
        await memory.init()
        await scheduler.start()
        await application.bot.set_my_commands([
            BotCommand("start",    "say hi"),
            BotCommand("help",     "show commands"),
            BotCommand("memory",   "see what i remember about you"),
            BotCommand("research", "see what i've been reading"),
            BotCommand("status",   "health check — model, uptime, memory sizes"),
            BotCommand("reset",    "clear today's conversation context"),
        ])
        log.info("hyrax.ready")

    app.post_init = post_init

    # Message handler (all text that isn't a command)
    message_handler = make_handler(config, brain, memory, web)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))

    # Command handlers
    commands = make_commands(config, brain, memory)
    app.add_handler(CommandHandler("start", commands["start"]))
    app.add_handler(CommandHandler("help", commands["help"]))
    app.add_handler(CommandHandler("memory", commands["memory"]))
    app.add_handler(CommandHandler("research", commands["research"]))
    app.add_handler(CommandHandler("reset", commands["reset"]))
    app.add_handler(CommandHandler("status", commands["status"]))

    log.info("hyrax.polling")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
