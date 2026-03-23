import pytest
from unittest.mock import AsyncMock, MagicMock
from telegram import Update, Message, User, Chat
from telegram.ext import CallbackContext


@pytest.fixture
def owner_id() -> int:
    return 123456789


@pytest.fixture
def test_config(tmp_path, owner_id):
    """Config instance with test values, memory pointed at tmp_path."""
    import os
    os.environ.update({
        "TELEGRAM_TOKEN": "1234567890:AAAA-test-token",
        "OWNER_TELEGRAM_ID": str(owner_id),
        "MEMORY_DIR": str(tmp_path / "memory"),
        "OLLAMA_HOST": "http://localhost:11434",
        "OLLAMA_MODEL": "test-model",
        "CONTEXT_WINDOW_SIZE": "4000",
        "SEARXNG_HOST": "",
        "BOT_NAME": "TestHyrax",
        "PROACTIVE_MAX_PER_DAY": "1",
    })
    # Re-import to pick up env
    from importlib import reload
    import hyrax.config as cfg_module
    reload(cfg_module)
    config = cfg_module.Settings()
    yield config
    # Clean up env
    for key in ["TELEGRAM_TOKEN", "OWNER_TELEGRAM_ID", "MEMORY_DIR",
                "OLLAMA_HOST", "OLLAMA_MODEL", "CONTEXT_WINDOW_SIZE",
                "SEARXNG_HOST", "BOT_NAME", "PROACTIVE_MAX_PER_DAY"]:
        os.environ.pop(key, None)


def make_update(user_id: int, text: str = "hello", chat_id: int = 42) -> MagicMock:
    """Create a minimal PTB Update mock."""
    user = MagicMock(spec=User)
    user.id = user_id

    message = MagicMock(spec=Message)
    message.text = text
    message.chat_id = chat_id
    message.reply_text = AsyncMock()

    update = MagicMock(spec=Update)
    update.effective_user = user
    update.message = message
    update.effective_chat = MagicMock(spec=Chat)
    update.effective_chat.id = chat_id
    return update


def make_context(send_message_return=None) -> MagicMock:
    """Create a minimal PTB CallbackContext mock."""
    sent_msg = MagicMock()
    sent_msg.edit_text = AsyncMock()

    bot = MagicMock()
    bot.send_message = AsyncMock(return_value=send_message_return or sent_msg)

    context = MagicMock(spec=CallbackContext)
    context.bot = bot
    return context
