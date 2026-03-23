# src/hyrax/brain.py
"""
Ollama client for Hyrax.

Manages per-chat conversation history, streams tokens from Ollama's /api/chat
endpoint, guards against context window overflow, and retries on timeouts.
"""
import asyncio
import json
from collections import defaultdict
from typing import AsyncGenerator

import httpx
import structlog

log = structlog.get_logger()

_RETRY_DELAY = 2.0  # seconds between timeout retries


class OllamaUnavailableError(Exception):
    """Raised after exhausting all retries against Ollama."""


class Brain:
    def __init__(self, config, memory=None) -> None:
        self._config = config
        self._memory = memory  # injected by main.py; may be None in tests
        self._history: dict[int, list[dict]] = defaultdict(list)
        self._client = httpx.AsyncClient(timeout=60.0)

    @property
    def client(self) -> httpx.AsyncClient:
        """Expose shared httpx client for memory.summarize_and_save."""
        return self._client

    # ------------------------------------------------------------------ #
    # Public API                                                           #
    # ------------------------------------------------------------------ #

    async def stream(
        self,
        chat_id: int,
        user_text: str,
        system_prompt: str,
        context_supplement: str | None = None,
    ) -> AsyncGenerator[str, None]:
        """
        Yield token chunks from Ollama.

        context_supplement: ephemeral text injected as a system message for
        this call only — NOT saved to persistent history. Used for search results.
        """
        await self._maybe_compress(chat_id)

        # Append user message to persistent history
        self._history[chat_id].append({"role": "user", "content": user_text})

        messages = self._build_messages(chat_id, system_prompt, context_supplement)
        full_response = ""

        async for chunk in self._stream_from_ollama(messages):
            full_response += chunk
            yield chunk

        # Append assistant response to persistent history
        self._history[chat_id].append({"role": "assistant", "content": full_response})

    async def collect_stream(
        self,
        chat_id: int,
        user_text: str,
        system_prompt: str,
        context_supplement: str | None = None,
    ) -> str:
        """Collect the full stream into a string. Used for search-tag detection."""
        chunks = []
        async for chunk in self.stream(chat_id, user_text, system_prompt, context_supplement):
            chunks.append(chunk)
        return "".join(chunks)

    def reset_history(self, chat_id: int) -> None:
        """Clear conversation history for a chat (used by /reset)."""
        self._history.pop(chat_id, None)

    # ------------------------------------------------------------------ #
    # Internals                                                            #
    # ------------------------------------------------------------------ #

    def _build_messages(
        self,
        chat_id: int,
        system_prompt: str,
        context_supplement: str | None,
    ) -> list[dict]:
        messages = [{"role": "system", "content": system_prompt}]
        messages.extend(self._history[chat_id])

        if context_supplement:
            # Ephemeral injection — goes after history but before Ollama responds.
            # Not added to self._history.
            messages.append({"role": "system", "content": context_supplement})

        return messages

    async def _maybe_compress(self, chat_id: int) -> None:
        """If history exceeds 80% of context window, summarize and reset."""
        history = self._history[chat_id]
        if not history:
            return

        token_estimate = sum(len(m["content"]) for m in history) // 4
        threshold = int(self._config.context_window_size * 0.8)

        if token_estimate > threshold:
            log.info("brain.context_guard triggered", chat_id=chat_id, tokens=token_estimate)
            if self._memory:
                await self._memory.summarize_and_save(history, self._client)
            self._history[chat_id] = []

    async def _stream_from_ollama(self, messages: list[dict]) -> AsyncGenerator[str, None]:
        """Low-level streaming call to Ollama with one retry on timeout."""
        last_exc = None

        for attempt in range(2):
            if attempt > 0:
                await asyncio.sleep(_RETRY_DELAY)
            try:
                async for chunk in self._do_stream(messages):
                    yield chunk
                return
            except httpx.TimeoutException as exc:
                last_exc = exc
                log.warning("brain.ollama_timeout", attempt=attempt + 1)
            except httpx.HTTPError as exc:
                last_exc = exc
                log.warning("brain.ollama_http_error", error=str(exc))
                break  # Non-timeout HTTP errors are not retried

        raise OllamaUnavailableError("Ollama failed after retries") from last_exc

    async def _do_stream(self, messages: list[dict]) -> AsyncGenerator[str, None]:
        """Execute one streaming request to Ollama /api/chat."""
        payload = {
            "model": self._config.ollama_model,
            "messages": messages,
            "stream": True,
        }
        response = await self._client.post(
            f"{self._config.ollama_host}/api/chat",
            json=payload,
        )
        response.raise_for_status()

        for line in response.text.splitlines():
            line = line.strip()
            if not line:
                continue
            data = json.loads(line)
            content = data.get("message", {}).get("content", "")
            if content:
                yield content
            if data.get("done"):
                break
