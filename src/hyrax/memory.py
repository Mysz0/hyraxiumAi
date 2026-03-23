# src/hyrax/memory.py
import asyncio
import re
from datetime import date, datetime, timedelta
from pathlib import Path

import httpx
import structlog

log = structlog.get_logger()


class MemoryConfigError(Exception):
    pass


class Memory:
    def __init__(self, config) -> None:
        self._dir = Path(config.memory_dir)
        self._ollama_host = config.ollama_host
        self._ollama_model = config.ollama_model

    def _journal_path(self) -> Path:
        today = date.today()
        return self._dir / "journal" / today.strftime("%Y-%m") / f"{today.isoformat()}.md"

    def _research_path(self) -> Path:
        today = date.today()
        return self._dir / "research" / f"{today.isoformat()}.md"

    async def init(self) -> None:
        def _check():
            for subdir in ["journal", "research", "projects"]:
                (self._dir / subdir).mkdir(parents=True, exist_ok=True)
            test_file = self._dir / ".write_test"
            try:
                test_file.touch()
                test_file.unlink()
            except OSError as exc:
                raise MemoryConfigError(f"MEMORY_DIR '{self._dir}' is not writable: {exc}") from exc
        await asyncio.to_thread(_check)

    async def read_today(self) -> str:
        return await self._read_file(self._journal_path())

    async def read_core(self) -> str:
        return await self._read_file(self._dir / "core.md")

    async def read_recent_research(self, days: int = 1) -> str:
        """Read research notes from the last N days, newest first."""
        parts = []
        for i in range(days):
            d = date.today() - timedelta(days=i)
            path = self._dir / "research" / f"{d.isoformat()}.md"
            content = await self._read_file(path)
            if content.strip():
                parts.append(content.strip())
        return "\n\n".join(parts)

    async def read_projects_index(self) -> str:
        """Read the projects ideas file."""
        return await self._read_file(self._dir / "projects" / "ideas.md")

    async def write_project_note(self, title: str, content: str) -> None:
        """Write a dated project note to projects/YYYY-MM-DD-{slug}.md."""
        slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:40] or "note"
        path = self._dir / "projects" / f"{date.today().isoformat()}-{slug}.md"

        def _write():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")

        try:
            await asyncio.to_thread(_write)
        except OSError:
            log.warning("memory.write_project_note failed", title=title)

    async def append_today(self, text: str) -> None:
        path = self._journal_path()
        timestamp = datetime.now().strftime("%H:%M")

        def _write():
            path.parent.mkdir(parents=True, exist_ok=True)
            header_needed = not path.exists()
            with path.open("a", encoding="utf-8") as f:
                if header_needed:
                    f.write(f"# {date.today().isoformat()}\n\n")
                f.write(f"## [{timestamp}] Notes\n{text}\n\n")

        try:
            await asyncio.to_thread(_write)
        except OSError:
            log.warning("memory.append_today failed", path=str(path))

    async def append_suggestion(self, task: str) -> None:
        """Save a suggestion from the human for later."""
        path = self._dir / "suggestions.md"

        def _write():
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as f:
                f.write(f"- [{date.today().isoformat()}] {task.strip()}\n")

        try:
            await asyncio.to_thread(_write)
            log.info("memory.suggestion_saved", task=task.strip()[:80])
        except OSError:
            log.warning("memory.append_suggestion failed")

    async def read_suggestions(self) -> str:
        """Read pending suggestions from the human."""
        return await self._read_file(self._dir / "suggestions.md")

    async def clear_suggestion(self, fragment: str) -> None:
        """Remove a suggestion that contains the given fragment."""
        path = self._dir / "suggestions.md"

        def _remove():
            if not path.exists():
                return
            lines = path.read_text(encoding="utf-8").splitlines()
            remaining = [l for l in lines if fragment.lower() not in l.lower()]
            path.write_text("\n".join(remaining) + "\n" if remaining else "", encoding="utf-8")

        try:
            await asyncio.to_thread(_remove)
        except OSError:
            log.warning("memory.clear_suggestion failed")

    async def write_project_file(self, relative_path: str, content: str) -> bool:
        """Write a file under projects/ with path safety checks."""
        if ".." in relative_path or relative_path.startswith("/"):
            log.warning("memory.write_project_file rejected path", path=relative_path)
            return False

        path = self._dir / "projects" / relative_path

        def _write():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")

        try:
            await asyncio.to_thread(_write)
            log.info("memory.project_file_written", path=relative_path)
            return True
        except OSError as exc:
            log.warning("memory.write_project_file failed", path=relative_path, error=str(exc))
            return False

    async def append_research(self, topic: str, notes: str) -> None:
        """Save autonomous research notes."""
        path = self._research_path()
        timestamp = datetime.now().strftime("%H:%M")

        def _write():
            path.parent.mkdir(parents=True, exist_ok=True)
            header_needed = not path.exists()
            with path.open("a", encoding="utf-8") as f:
                if header_needed:
                    f.write(f"# Research — {date.today().isoformat()}\n\n")
                f.write(f"## [{timestamp}] {topic}\n{notes}\n\n")

        try:
            await asyncio.to_thread(_write)
        except OSError:
            log.warning("memory.append_research failed", path=str(path))

    async def append_project_idea(self, idea: str) -> None:
        """Save a project idea to projects/ideas.md."""
        path = self._dir / "projects" / "ideas.md"

        def _write():
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as f:
                f.write(f"- [{date.today().isoformat()}] {idea}\n")

        try:
            await asyncio.to_thread(_write)
        except OSError:
            log.warning("memory.append_project_idea failed")

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
        try:
            await asyncio.to_thread(lambda: path.unlink(missing_ok=True))
        except OSError:
            log.warning("memory.clear_core failed")

    async def summarize_and_save(self, history: list[dict], client: httpx.AsyncClient) -> None:
        if not history:
            return
        summary = await self._call_ollama_for_summary(history, client)
        # The LLM sometimes wraps "NOTHING" in bullets or punctuation
        cleaned = summary.strip().lstrip("-•* ").strip().rstrip(".")
        if not cleaned or "NOTHING" in cleaned.upper():
            return
        await self.append_today(summary.strip())

    async def _call_ollama_for_summary(self, history: list[dict], client: httpx.AsyncClient) -> str:
        conversation_text = "\n".join(f"{m['role'].upper()}: {m['content']}" for m in history)
        prompt = (
            "Extract any notable facts from this conversation worth remembering "
            "about the user (name, preferences, life details, emotional state, context). "
            "Format as bullet points. "
            "If there is nothing notable, respond with exactly: NOTHING\n\n"
            f"CONVERSATION:\n{conversation_text}"
        )
        payload = {"model": self._ollama_model, "messages": [{"role": "user", "content": prompt}], "stream": False}
        try:
            response = await client.post(f"{self._ollama_host}/api/chat", json=payload, timeout=30.0)
            response.raise_for_status()
            return response.json()["message"]["content"]
        except Exception as exc:
            log.warning("memory.summarize failed", error=str(exc))
            return "NOTHING"

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
