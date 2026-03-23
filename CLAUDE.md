# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Build & Run

```bash
# Build and deploy (production)
docker compose up -d --build

# Clean rebuild (no cache)
docker compose build --no-cache && docker compose up -d

# View logs
docker compose logs -f

# Run tests
uv sync && uv run pytest -v
```

## Architecture

Hyrax is an autonomous Telegram bot powered by a local Ollama LLM. It runs as a single async Python process inside Docker, with a persistent memory filesystem mounted at `/memory`.

### Module dependency flow

```
main.py → config.py, memory.py, brain.py, web.py, bot.py, commands.py, scheduler.py
```

- **config.py** — `Settings` (pydantic-settings from `.env`) + `build_system_prompt()` which assembles: role_lock → memory (capped 3000 chars) → SOUL.md personality → capabilities → search instruction. **Prompt order is critical for small models** — personality must be last/closest to the user message.
- **brain.py** — Ollama `/api/chat` client. Manages per-`chat_id` conversation history, streams tokens, compresses history at 80% context window via `_maybe_compress()`.
- **bot.py** — Telegram message handler factory (`make_handler`). Two-pass flow: first collects full response to detect `[SEARCH: query]` tags, then either streams search-augmented response or processes `[WRITE_FILE]` tags and displays. Also extracts suggestions (tasks user asks Hyrax to do later).
- **commands.py** — Slash command handlers (`/start`, `/help`, `/memory`, `/research`, `/reset`, `/status`). Built via `make_commands()` factory.
- **scheduler.py** — Autonomous activity loop using APScheduler. The LLM decides what to do (research/project/think/rest/message) and when (10–45 min delay). Uses separate negative `chat_id` buckets (-1 through -5) to isolate autonomous history from user conversations.
- **memory.py** — File-based persistence under `/memory/`: journal (`YYYY-MM-DD.md`), research notes, project files, suggestions, core facts. All I/O via `asyncio.to_thread()`.
- **web.py** — SearXNG search + BeautifulSoup page scraping. Disabled when `SEARXNG_HOST` is empty.

### Key patterns

- **`[WRITE_FILE: path]...[/WRITE_FILE]`** — LLM tool-use for file creation. Parsed with dual regex (strict with closing tag + loose fallback using code fences). Used in both `bot.py` (user conversations) and `scheduler.py` (autonomous projects).
- **`[SEARCH: query]`** — LLM-initiated web search. Detected in first-pass response, triggers second pass with search results injected as ephemeral system message.
- **`owner_only(config)`** — Decorator blocking non-owner Telegram users. Applied to all handlers.
- **`safe_task(coro)`** — Fire-and-forget `asyncio.create_task` with error logging callback.
- **SOUL.md** — Personality file at `/memory/SOUL.md`, read at runtime by `build_system_prompt()`. Falls back to `_DEFAULT_SOUL` in config.py if missing.

### Deployment

Docker container with `network_mode: host` (Linux only) so it can reach Ollama and SearXNG on the host. Memory persists via `./memory:/memory` volume mount. The LLM model is configured via `OLLAMA_MODEL` in `.env` (currently `llama3.1:8b`; pyproject.toml default is `qwen2.5:7b`).

### Gotchas

- The `forget` command handler still exists in `commands.py` but is NOT registered in `main.py` or the command menu — intentionally removed from the user-facing bot.
- `_do_stream()` in brain.py does a full `POST` then iterates lines (not true HTTP streaming) — works because Ollama returns newline-delimited JSON.
- Scheduler's message nudge tracks `_last_message_at` and `_actions_since_message` in memory only — resets on container restart.
