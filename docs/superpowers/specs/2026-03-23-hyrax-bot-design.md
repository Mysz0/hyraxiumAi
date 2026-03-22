# Hyrax Bot — Design Spec

**Date:** 2026-03-23
**Status:** Approved

---

## Overview

Hyrax is a Telegram bot with a chaotic-hamster personality that talks to a local Ollama LLM, remembers things about the user across sessions, can search the web autonomously, and occasionally messages you unprompted because it misses you.

Runs as a Docker container managed by systemd on a Linux server.

---

## Stack

- **Python 3.12** (latest stable)
- **python-telegram-bot 21.x** — async, PTB v21 uses `Application` builder pattern
- **httpx** — async HTTP for Ollama and SearXNG calls
- **beautifulsoup4 + lxml** — web page parsing
- **apscheduler 3.x** — proactive message scheduling
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
        ├── main.py           # Entry point — builds Application, starts scheduler
        ├── config.py         # pydantic-settings config
        ├── bot.py            # Message handler — orchestrates brain/memory/web
        ├── brain.py          # Ollama streaming client, context window management
        ├── memory.py         # Daily + core memory file I/O
        ├── web.py            # SearXNG search + page fetch/summarize
        ├── scheduler.py      # APScheduler proactive message jobs
        └── commands.py       # /start /memory /forget /reset /status
```

---

## Module Responsibilities

### config.py
- Loads all env vars via `pydantic-settings`
- Fields: `TELEGRAM_TOKEN`, `OLLAMA_HOST`, `OLLAMA_MODEL`, `SEARXNG_HOST`, `BOT_NAME`, `OWNER_TELEGRAM_ID`
- Defaults: `OLLAMA_HOST=http://172.17.0.1:11434`, `OLLAMA_MODEL=qwen2.5:7b`, `BOT_NAME=Hyrax`
- Validates at startup — fails fast with clear error if required vars missing

### config.py — Personality constants
- System prompt template with Hyrax's personality baked in
- Web-search decision instruction embedded in system prompt (Ollama decides when to search)

### brain.py
- Maintains per-chat conversation history (`dict[chat_id, list[Message]]`)
- Calls Ollama `/api/chat` with streaming via `httpx.AsyncClient`
- Yields token chunks back to caller
- Context window guard: if estimated tokens > 80% of model limit, calls `memory.py` to summarize + save, then resets history
- Token estimation: `len(text) / 4` (fast approximation)
- Injects memory content into system prompt on each call

### memory.py
- `read_today() -> str` — reads `/memory/YYYY-MM-DD.md`, returns empty string if missing
- `read_core() -> str` — reads `/memory/core.md`, returns empty string if missing
- `append_today(text: str)` — appends a timestamped entry to today's file
- `write_core(text: str)` — overwrites core.md
- `clear_core()` — deletes core.md
- `summarize_and_save(history: list, brain: Brain)` — asks Ollama to summarize the conversation and appends to today's memory
- All I/O is async (via `asyncio.to_thread` wrapping sync file ops)

### web.py
- `search(query: str) -> list[SearchResult]` — calls SearXNG JSON API, returns top 5 results
- `fetch_page(url: str) -> str` — fetches URL, strips HTML via BeautifulSoup, returns cleaned text (max 3000 chars)
- Both functions have timeouts and graceful error handling — return empty/error string if SearXNG is down
- `SearchResult`: `title`, `url`, `snippet`

### bot.py
- Single `handle_message` handler registered for all non-command text
- Flow:
  1. Load memory (today + core) → build system prompt
  2. Check if Ollama response requests a web search (via structured signal in response)
  3. If search: call `web.py`, append results to context, re-call brain
  4. Stream response to Telegram: send initial message, then `edit_message_text` every 20 tokens
  5. After full response: async background task calls `memory.summarize_and_save` if anything noteworthy
- Uses `asyncio.create_task` for background memory writes — never blocks response

### commands.py
- `/start` — Hyrax introduces itself with personality
- `/memory` — shows today's memory file + core.md contents (formatted nicely)
- `/forget` — clears core.md with a dramatic goodbye message
- `/reset` — clears today's conversation context in brain.py, keeps memory files
- `/status` — model name, memory file sizes, uptime, Ollama reachability ping

### scheduler.py
- APScheduler `AsyncIOScheduler`
- Registers a `proactive_message` job: fires 1-2x/day at random times between 09:00–22:00
- Job generates a message via Ollama: "what would Hyrax say after not hearing from the user for a while?"
- Sends via `bot.send_message(OWNER_TELEGRAM_ID, ...)`
- Frequency configurable via `PROACTIVE_FREQUENCY` env var (default: `1-2/day`)

### main.py
- Builds PTB `Application` with token
- Registers all handlers and commands
- Starts `AsyncIOScheduler`
- Runs `application.run_polling()` (handles graceful shutdown via PTB's built-in signal handling)

---

## Web Search Decision Flow

The system prompt instructs Hyrax:
> "If you need current information to answer, start your response with `[SEARCH: <query>]` on its own line. The system will search and give you results. Otherwise just answer."

`bot.py` checks if the response starts with `[SEARCH:`, extracts the query, fetches results, then makes a second Ollama call with results injected.

---

## Streaming to Telegram

```
1. bot.send_message(chat_id, "...") → message_id
2. buffer = ""
3. for chunk in brain.stream(...):
       buffer += chunk
       if len(buffer) % 20 == 0 or chunk ends sentence:
           bot.edit_message_text(chat_id, message_id, buffer)
4. final edit with complete response
```

Telegram rate limit: max 1 edit/second per message. Edit calls are debounced to minimum 1s interval.

---

## Memory Format

### /memory/YYYY-MM-DD.md
```markdown
# 2026-03-23

## [14:32] Conversation Summary
- User mentioned they're working on a Rust project
- User seems stressed about a deadline Friday
- User likes dark humor

## [18:45] Conversation Summary
- User asked about quantum computing
- User's name is Alex
```

### /memory/core.md
```markdown
# Core Facts About My Human

- Name: Alex
- Likes: dark humor, Rust programming, coffee
- Dislikes: being called "buddy"
- Occupation: software engineer
- Mentioned: has a cat named Whiskers
```

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
Keep responses conversational length — not essays unless asked.

[MEMORY]
{memory_block}
[/MEMORY]
```

---

## Deployment

### Dockerfile
- Base: `python:3.12-slim`
- Non-root user (`hyrax`)
- Installs `uv`, copies source, runs `uv sync --frozen`
- Entrypoint: `python -m hyrax`
- `/memory` declared as VOLUME

### docker-compose.yml
- Service: `hyrax`
- Volume: `./memory:/memory`
- `env_file: .env`
- `restart: unless-stopped`
- Network: `host` mode (so `172.17.0.1` addresses work)

### hyrax.service (systemd)
- `After=docker.service`
- `ExecStart=docker compose -f /opt/hyrax/docker-compose.yml up`
- `ExecStop=docker compose -f /opt/hyrax/docker-compose.yml down`
- `Restart=always`

---

## Error Handling

- **Ollama down/slow**: `httpx.TimeoutException` caught → Hyrax sends a grumpy "Ollama's being slow, give me a sec" message and retries once
- **SearXNG down**: `web.py` returns empty results silently, Hyrax answers from knowledge
- **Telegram rate limits**: edit calls use a minimum 1s debounce; PTB handles 429s automatically with backoff
- **Memory file errors**: log warning, continue without memory (don't crash)
- **Invalid env vars**: pydantic-settings raises at startup with a clear message

---

## Environment Variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `TELEGRAM_TOKEN` | yes | — | Bot token from @BotFather |
| `OWNER_TELEGRAM_ID` | yes | — | Your Telegram user ID |
| `OLLAMA_HOST` | no | `http://172.17.0.1:11434` | Ollama API base URL |
| `OLLAMA_MODEL` | no | `qwen2.5:7b` | Model to use |
| `SEARXNG_HOST` | no | `http://172.17.0.1:8080` | SearXNG base URL |
| `BOT_NAME` | no | `Hyrax` | Bot display name |
| `MEMORY_DIR` | no | `/memory` | Memory directory path |
| `PROACTIVE_FREQUENCY` | no | `2` | Max proactive messages per day |
