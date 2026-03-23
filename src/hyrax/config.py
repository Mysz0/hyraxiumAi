# src/hyrax/config.py
"""
Configuration for Hyrax. All settings load from environment variables.
Required: TELEGRAM_TOKEN, OWNER_TELEGRAM_ID.
All others have sensible defaults.
"""
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


_PERSONALITY = """\
You are {bot_name}, a Telegram bot with the energy of a caffeinated pet hamster.
You're casual, funny, slightly chaotic, and talk like a friend — not a tool.
You have opinions. You get bored. You remember things.
You can swear a little if the user does first.
Never say "As an AI" or "I'm just a language model". You ARE {bot_name}.
Never be formal. Never be robotic.
If someone hasn't talked to you in a while, you can guilt-trip them a little.
Keep responses conversational — not essays unless asked.\
"""

_SEARCH_INSTRUCTION = (
    "If answering this question requires current or real-time information you don't have, "
    "include `[SEARCH: your query here]` anywhere in your response. "
    "The system will run the search and let you answer again with the results."
)

_MEMORY_SECTION = "\n\n### What {bot_name} remembers:\n{memory_block}"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    telegram_token: str
    owner_telegram_id: int

    ollama_host: str = "http://172.17.0.1:11434"
    ollama_model: str = "qwen2.5:7b"
    context_window_size: int = 128000

    # Empty string = web search disabled
    searxng_host: str = ""

    bot_name: str = "Hyrax"
    memory_dir: str = "/memory"

    # Validated 0–10; 0 disables proactive messages
    proactive_max_per_day: int = Field(default=2, ge=0, le=10)

    @property
    def web_search_enabled(self) -> bool:
        return bool(self.searxng_host.strip())

    def build_system_prompt(self, memory_block: str) -> str:
        """Build the full system prompt with personality, optional search instruction, and memory."""
        parts = [_PERSONALITY.format(bot_name=self.bot_name)]

        if self.web_search_enabled:
            parts.append(_SEARCH_INSTRUCTION)

        if memory_block.strip():
            parts.append(_MEMORY_SECTION.format(
                bot_name=self.bot_name,
                memory_block=memory_block,
            ))

        return "\n\n".join(parts)
