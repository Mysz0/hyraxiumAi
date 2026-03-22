# Hyrax Bot — Design Spec

**Date:** 2026-03-23
**Status:** Approved (v3 — post review fixes)

---

## Overview

Hyrax is a Telegram bot with the energy of a caffeinated pet hamster. It talks to a local Ollama LLM, remembers things about the user across sessions, searches the web autonomously when needed, and occasionally messages you unprompted because it has separation anxiety.

Runs as a Docker container managed by systemd on a Linux server.

---

## Stack

- **Python 3.12** (latest stable)
- **python-telegram-bot 21.x** — async, Application builder pattern
- **httpx** — async HTTP for Ollama and SearXNG calls
- **beautifulsoup4 + lxml** — web page parsing
- **apscheduler 4.x** — proactive message scheduling (4.x has clean asyncio integration)
- **pydantic-settings** — env var config with validation
- **structlog** — structured logging
- **uv** — dependency management (pyproject.toml)

---

## Project Structure

```
hyrax/
├── pyproject.toml
├── uv.lock
├── .env.example
├── Dockerfile
├── docker-compose.yml
├── hyrax.service
├── README.md
└── src/
    └── hyrax/
        ├── __init__.py
        ├── main.py           # Entry point — builds Application, registers post_init
        ├── config.py         # pydantic-settings config
        ├── bot.py            # Message handler — orchestrates brain/memory/web
        ├── brain.py          # Ollama streaming client, context window management
        ├── memory.py         # Daily + core memory file I/O
        ├── web.py            # SearXNG search + page fetch/summarize
        ├── scheduler.py      # APScheduler proactive message jobs
        └── commands.py       # /start /memory /forget /reset /status /help
```

---

## Environment Variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `TELEGRAM_TOKEN` | yes | — | Bot token from @BotFather |
| `OWNER_TELEGRAM_ID` | yes | — | Your Telegram user ID (integer) |
| `OLLAMA_HOST` | no | `http://172.17.0.1:11434` | Ollama API base URL |
| `OLLAMA_MODEL` | no | `qwen2.5:7b` | Model to use |
| `CONTEXT_WINDOW_SIZE` | no | `128000` | Model's context window token limit |
| `SEARXNG_HOST` | no | `""` | SearXNG base URL — empty string disables web search entirely |
| `BOT_NAME` | no | `Hyrax` | Bot display name |
| `MEMORY_DIR` | no | `/memory` | Persistent memory directory path |
| `PROACTIVE_MAX_PER_DAY` | no | `2` | Max proactive messages per day — validated: `Field(ge=0, le=10)` |

**Notes:**
- `SEARXNG_HOST` being unset or empty fully disables web search. The system prompt omits the search instruction and `web.py` raises `WebSearchDisabledError` if called accidentally.
- `PROACTIVE_MAX_PER_DAY` is validated as an integer in range 0–10. Setting to 0 disables proactive messages.

---

## Access Control

The bot is **owner-only**. Every handler (messages and commands) is decorated with `@owner_only`, which checks:
```python
if update.effective_user.id != config.OWNER_TELEGRAM_ID:
    await update.message.reply_text("nope")
    return
```
Non-owners get a curt rejection and no memory or context is touched.

---

## Module Responsibilities

### config.py
- Loads all env vars via `pydantic-settings`
- Validates at startup — fails fast with clear message if required vars missing
- `PROACTIVE_MAX_PER_DAY`: `Field(default=2, ge=0, le=10)`
- Exposes `web_search_enabled: bool` property (`SEARXNG_HOST != ""`)
- Personality system prompt template is defined here as a constant

### brain.py
- Maintains per-chat conversation history: `dict[int, list[dict]]`
- Calls Ollama `/api/chat` with streaming via `httpx.AsyncClient`
- Yields string token chunks to callers via `async_generator`
- `collect_stream(chat_id, text, system_prompt) -> str` — convenience wrapper that collects the full stream into a string
- **Context window guard**: if `estimate_tokens(history) > CONTEXT_WINDOW_SIZE * 0.8`, calls `memory.summarize_and_save(history, self._client)` then resets history to empty
  - Token estimation: `sum(len(m["content"]) for m in history) // 4`
  - Note: this is a character-based heuristic — adequate for English; multilingual text (CJK etc.) will undercount tokens. `CONTEXT_WINDOW_SIZE` should be set conservatively for multilingual use cases.
  - `CONTEXT_WINDOW_SIZE` comes from config (env var, default 128000)
- Builds system prompt by injecting memory block on every call
- `reset_history(chat_id)` — clears that chat's history
- **Ollama retry**: `brain.py` implements a `_call_ollama` internal method that retries once on `httpx.TimeoutException` with a 2-second delay. After 2 consecutive timeouts, it raises `OllamaUnavailableError`. `bot.py` catches this and sends a user-facing error message.

### memory.py
- All file paths are built from `config.MEMORY_DIR` — never hardcoded
- **Startup validation**: `memory.init()` is called at bot startup. It creates `MEMORY_DIR` if it doesn't exist (using `os.makedirs`). If the path exists but is not writable, it raises `MemoryConfigError` (hard fail — configuration must be fixed)
- `read_today() -> str` — reads `MEMORY_DIR/YYYY-MM-DD.md`
- `read_core() -> str` — reads `MEMORY_DIR/core.md`
- `append_today(text: str)` — appends timestamped block to today's file
- `write_core(text: str)` — overwrites core.md
- `clear_core()` — deletes core.md (`/forget` clears permanent memory only; daily files are not affected by design — they serve as an audit log)
- `summarize_and_save(history: list[dict], client: httpx.AsyncClient)` — calls Ollama with the history and a summarization prompt. If Ollama returns `NOTHING`, skips `append_today`. The `client` parameter is the same `httpx.AsyncClient` instance owned by `brain.py` (passed in, not created internally — avoids spawning extra connections)
- Transient I/O errors during `read_*` / `append_today` are caught, logged with structlog, and silently skipped. Startup validation catches persistent misconfig.
- All file I/O via `asyncio.to_thread` wrapping sync ops

### web.py

Only active when `config.web_search_enabled` is True.

- `search(query: str) -> list[SearchResult]` — calls `SEARXNG_HOST/search?q=...&format=json`, returns top 5 results (title, url, snippet)
- `fetch_page(url: str) -> str` — fetches URL with a 10s timeout, strips HTML via BeautifulSoup (lxml parser), returns first 3000 characters of cleaned body text
- All functions have explicit `httpx.TimeoutException` and `httpx.HTTPError` handling — return empty list / empty string with a structlog warning
- `SearchResult`: `title: str`, `url: str`, `snippet: str`

### bot.py
- Single `handle_message` handler for all non-command text, decorated with `@owner_only`
- **Flow:**
  1. Load memory: `memory.read_today()` + `memory.read_core()` → build system prompt
  2. Send placeholder message `"..."` to Telegram — get `msg` object with `message_id`
  3. **First stream**: collect full response from `brain.collect_stream(chat_id, user_text, system_prompt)` into `first_response` string. The first stream is **not shown to the user yet** — it exists only to detect the search tag.
     - While collecting, update the placeholder every ~2 seconds with a typing indicator ("thinking...")
  4. **Web search detection**: `re.search(r'\[SEARCH:\s*(.+?)\]', first_response)`. If found with a non-empty group:
     - Edit `msg` to show "🔍 searching..."
     - Call `web.search(query)` → optionally `web.fetch_page(url)` for top result
     - Build an **ephemeral** context injection — a `system` role message containing search results. This message is passed to `brain.stream` but is **not persisted in brain history** (it is passed as a one-time `context_supplement` parameter, not appended to the history dict)
     - Stream the second Ollama response to Telegram (see Streaming section)
     - If search fails: stream the `first_response` with a note that web search failed
  5. If no search tag found: stream `first_response` directly to Telegram
  6. After final response sent: `safe_task(memory.summarize_and_save(history, brain.client))`
- **Long response handling**: if `buffer` exceeds 4000 characters during streaming, split by sending the current buffer as a complete message and starting a new one. This respects Telegram's 4096-character message limit.
- **Background tasks**: all `asyncio.create_task` calls use `safe_task`:
  ```python
  def _on_task_done(task: asyncio.Task) -> None:
      if not task.cancelled() and task.exception():
          logger.error("background task failed", exc_info=task.exception())

  def safe_task(coro: Coroutine) -> asyncio.Task:
      task = asyncio.create_task(coro)
      task.add_done_callback(_on_task_done)
      return task
  ```
  `_on_task_done` is a named function (not a lambda) to correctly handle the `CancelledError` case — `task.cancelled()` is checked first, so `task.exception()` is only called on truly failed tasks.

#### Memory Trigger
`summarize_and_save` is called after every message exchange. The Ollama summarization prompt instructs the model to return the literal string `NOTHING` if there's nothing worth saving. `memory.py` checks for this sentinel and skips `append_today` — making it a true no-op for trivial exchanges.

### commands.py
All commands decorated with `@owner_only`.
- `/start` — Hyrax introduces itself with personality
- `/help` — lists all commands with one-line descriptions
- `/memory` — shows today's memory file + core.md (nicely formatted, code blocks)
- `/forget` — clears core.md only (daily files are intentionally preserved as an audit log). Hyrax delivers a dramatic farewell.
- `/reset` — calls `brain.reset_history(chat_id)`, Hyrax acts confused but rolls with it
- `/status` — model name, context window size, memory file sizes, uptime, Ollama ping (GET `/api/tags` with 3s timeout)

### scheduler.py
- Uses APScheduler 4.x `AsyncScheduler` (native asyncio, no loop-passing needed)
- Created and started inside PTB's `Application.post_init` callback to share the running event loop
- **Daily scheduling strategy**:
  1. `_schedule_today()` — schedules `PROACTIVE_MAX_PER_DAY` one-shot jobs at random `datetime` objects within today's 09:00–22:00 window. Any existing same-day proactive jobs are cancelled first.
  2. A daily `IntervalTrigger(days=1)` job fires at midnight and calls `_schedule_today()` to repopulate the next day's schedule.
  3. On startup, `_schedule_today()` is called immediately (skipping any times already passed today).
- Each proactive job calls Ollama with: "You haven't heard from your human in a while. Send them a short, in-character Hyrax message — funny, weird, or just checking in." Result is sent via `application.bot.send_message(OWNER_TELEGRAM_ID, text)`.

### main.py
- Builds PTB `Application` with token
- Registers all handlers and commands
- Sets `post_init=scheduler.setup` callback (starts `AsyncScheduler` + `memory.init()`)
- Runs `application.run_polling()` — PTB handles graceful shutdown via SIGINT/SIGTERM

---

## Web Search Decision Flow

When `SEARXNG_HOST` is set, the system prompt includes:
> "If answering this question requires current or real-time information you don't have, include `[SEARCH: your query here]` anywhere in your response. The system will run the search and let you answer again with the results."

`bot.py` collects the full first response, then uses `re.search()` to find the tag anywhere — tolerant of LLM preamble. If found:
1. Edit msg to show "🔍 searching..."
2. Run `web.search(query)` + optionally `web.fetch_page` on the top result
3. Inject results as a one-time ephemeral system message — **not added to persistent history**
4. Stream the second response to Telegram
5. If search fails (SearXNG down): stream original first response + append "*(web search failed)*"

Malformed tag (empty query after strip): regex group is empty string → detected, skipped, stream first response as-is.

---

## Streaming to Telegram

```python
msg = await context.bot.send_message(chat_id, "...")
buffer = ""
last_edit = time.monotonic()

async for chunk in brain.stream(chat_id, text, system_prompt):
    buffer += chunk
    now = time.monotonic()

    # Split if approaching Telegram's 4096-char limit
    if len(buffer) >= 4000:
        await msg.edit_text(buffer)       # finalize current message
        msg = await context.bot.send_message(chat_id, "▌")  # new message
        buffer = ""
        last_edit = now
        continue

    if now - last_edit >= 1.0:            # 1s time-based debounce
        await msg.edit_text(buffer + "▌") # ▌ cursor = still streaming
        last_edit = now

await msg.edit_text(buffer)              # final edit, remove cursor
```

- Purely time-based debounce — no character-count heuristics
- Message splitting at 4000 chars (buffer before the 4096 hard limit)
- `▌` cursor removed on final edit

---

## Personality System Prompt

```
You are Hyrax, a Telegram bot with the energy of a caffeinated pet hamster.
You're casual, funny, slightly chaotic, and talk like a friend — not a tool.
You have opinions. You get bored. You remember things.
You can swear a little if the user does first.
Never say "As an AI" or "I'm just a language model". You ARE Hyrax.
Never be formal. Never be robotic.
If someone hasn't talked to you in a while, you can guilt-trip them a little.
Keep responses conversational — not essays unless asked.
{search_instruction}

### What Hyrax remembers:
{memory_block}
```

`{search_instruction}` is populated only when `SEARXNG_HOST` is set.
`{memory_block}` is populated from `memory.read_today()` + `memory.read_core()`.

---

## Memory File Format

### /memory/YYYY-MM-DD.md
```markdown
# 2026-03-23

## [14:32] Notes
- User is working on a Rust project with a Friday deadline
- Seems stressed; responds well to dark humor as stress relief

## [18:45] Notes
- User asked about quantum computing — genuine curiosity, not work-related
- User's name is Alex
```

### /memory/core.md
```markdown
# Core Facts About My Human

- Name: Alex
- Likes: dark humor, Rust programming, coffee
- Dislikes: being called "buddy"
- Occupation: software engineer
- Has a cat named Whiskers
```

---

## Error Handling

| Failure | Behavior |
|---|---|
| Ollama timeout | `brain.py` retries once after 2s delay; on 2nd failure raises `OllamaUnavailableError` |
| `OllamaUnavailableError` | `bot.py` catches it, sends "brain's offline rn, try again in a bit 😵" |
| SearXNG down | `web.py` returns empty list + structlog warning; bot streams first response with failure note |
| Memory read error | structlog warning, continue with empty memory context (no crash) |
| Memory write error | structlog warning, silently skip (no crash) |
| `MemoryConfigError` at startup | Hard fail with clear message — `MEMORY_DIR` is not writable |
| Telegram 4096-char limit | Streaming splits messages at 4000 chars (see Streaming section) |
| Telegram rate limit | PTB 21.x handles 429 automatically with exponential backoff |
| Background task exception | Caught by `_on_task_done` callback, logged via structlog, never propagated |
| Invalid env vars | pydantic-settings raises at startup with field name + constraint |

---

## Deployment

### Dockerfile
- Base: `python:3.12-slim`
- Non-root user `hyrax` (uid 1000)
- Installs `uv`, copies source, runs `uv sync --frozen --no-dev`
- Entrypoint: `python -m hyrax`
- `VOLUME ["/memory"]` declared

### docker-compose.yml
- Service: `hyrax`
- `network_mode: host` — **Linux server only**. Required so `172.17.0.1` (Docker gateway) resolves to the host running Ollama and SearXNG. For local macOS development, override `OLLAMA_HOST=http://host.docker.internal:11434` and `SEARXNG_HOST=http://host.docker.internal:8080` and remove `network_mode: host`.
- Volume: `./memory:/memory`
- `env_file: .env`
- `restart: unless-stopped`

### hyrax.service (systemd)
- `After=docker.service`
- `ExecStart=docker compose -f /opt/hyrax/docker-compose.yml up`
- `ExecStop=docker compose -f /opt/hyrax/docker-compose.yml down`
- `Restart=always`
- `RestartSec=10`
