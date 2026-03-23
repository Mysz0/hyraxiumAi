# src/hyrax/memory.py
"""
Persistent memory for Hyrax.

Two storage tiers:
  /memory/YYYY-MM-DD.md  — daily append-only conversation notes
  /memory/core.md        — permanent user facts (cleared by /forget)

All file I/O is async via asyncio.to_thread. Transient errors are logged and
swallowed so a bad disk day doesn't take the whole bot down.
"""
import asyncio
from datetime import date, datetime
from pathlib import Path

import httpx
import structlog

log = structlog.get_logger()


class MemoryConfigError(Exception):
    """Raised at startup if MEMORY_DIR is not writable."""


class Memory:
    def __init__(self, config) -> None:
        self._dir = Path(config.memory_dir)
        self._ollama_host = config.ollama_host
        self._ollama_model = config.ollama_model

    # ------------------------------------------------------------------ #
    # Startup                                                              #
    # ------------------------------------------------------------------ #

    async def init(self) -> None:
        """Create the memory directory or raise MemoryConfigError if not writable."""
        def _check():
            self._dir.mkdir(parents=True, exist_ok=True)
            test_file = self._dir / ".write_test"
            try:
                test_file.touch()
                test_file.unlink()
            except OSError as exc:
                raise MemoryConfigError(
                    f"MEMORY_DIR '{self._dir}' is not writable: {exc}"
                ) from exc

        await asyncio.to_thread(_check)

    # ------------------------------------------------------------------ #
    # Readers                                                              #
    # ------------------------------------------------------------------ #

    async def read_today(self) -> str:
        return await self._read_file(self._today_path())

    async def read_core(self) -> str:
        return await self._read_file(self._dir / "core.md")

    # ------------------------------------------------------------------ #
    # Writers                                                              #
    # ------------------------------------------------------------------ #

    async def append_today(self, text: str) -> None:
        path = self._today_path()
        timestamp = datetime.now().strftime("%H:%M")

        def _write():
            header_needed = not path.exists()
            with path.open("a", encoding="utf-8") as f:
                if header_needed:
                    f.write(f"# {date.today().isoformat()}\n\n")
                f.write(f"## [{timestamp}] Notes\n{text}\n\n")

        try:
            await asyncio.to_thread(_write)
        except OSError:
            log.warning("memory.append_today failed", path=str(path))

    async def write_core(self, text: str) -> None:
        path = self._dir / "core.md"

        def _write():
            path.write_text(f"# Core Facts About My Human\n\n{text}\n", encoding="utf-8")

        try:
            await asyncio.to_thread(_write)
        except OSError:
            log.warning("memory.write_core failed")

    async def clear_core(self) -> None:
        path = self._dir / "core.md"

        def _delete():
            path.unlink(missing_ok=True)

        try:
            await asyncio.to_thread(_delete)
        except OSError:
            log.warning("memory.clear_core failed")

    # ------------------------------------------------------------------ #
    # Summarization                                                        #
    # ------------------------------------------------------------------ #

    async def summarize_and_save(
        self, history: list[dict], client: httpx.AsyncClient
    ) -> None:
        """
        Ask Ollama to extract notable facts from the conversation.
        If Ollama returns 'NOTHING', skip appending (trivial exchange).
        The client is brain.py's shared httpx.AsyncClient.
        """
        if not history:
            return

        summary = await self._call_ollama_for_summary(history, client)
        if summary.strip().upper() == "NOTHING":
            return

        await self.append_today(summary.strip())

    async def _call_ollama_for_summary(
        self, history: list[dict], client: httpx.AsyncClient
    ) -> str:
        """Call Ollama with a summarization prompt. Returns raw text."""
        conversation_text = "\n".join(
            f"{m['role'].upper()}: {m['content']}" for m in history
        )
        prompt = (
            "Extract any notable facts from this conversation worth remembering "
            "about the user (name, preferences, life details, emotional state, context). "
            "Format as bullet points. "
            "If there is nothing notable, respond with exactly: NOTHING\n\n"
            f"CONVERSATION:\n{conversation_text}"
        )
        payload = {
            "model": self._ollama_model,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
        }
        try:
            response = await client.post(
                f"{self._ollama_host}/api/chat",
                json=payload,
                timeout=30.0,
            )
            response.raise_for_status()
            return response.json()["message"]["content"]
        except Exception as exc:
            log.warning("memory.summarize failed", error=str(exc))
            return "NOTHING"

    # ------------------------------------------------------------------ #
    # Helpers                                                              #
    # ------------------------------------------------------------------ #

    def _today_path(self) -> Path:
        return self._dir / f"{date.today().isoformat()}.md"

    async def _read_file(self, path: Path) -> str:
        def _read():
            return path.read_text(encoding="utf-8")

        try:
            return await asyncio.to_thread(_read)
        except FileNotFoundError:
            return ""
        except OSError:
            log.warning("memory.read failed", path=str(path))
            return ""
