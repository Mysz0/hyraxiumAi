# Hyrax

A Telegram bot powered by a local Ollama LLM. Casual, funny, slightly chaotic — like a digital pet hamster that remembers you, searches the web, and will absolutely text you first if you ignore it too long.

## Features

- Talks to Ollama locally (default: `qwen2.5:7b`)
- Streams responses word-by-word in Telegram
- Persistent memory across sessions (daily notes + permanent user facts)
- Autonomous web search via SearXNG when it needs current info
- Proactive messages — it will text you unprompted sometimes
- Owner-only access control
- Runs as a Docker container under systemd

## Prerequisites

- Docker + docker-compose on a Linux server
- [Ollama](https://ollama.ai) running on the host (`ollama serve`)
- A Telegram bot token from [@BotFather](https://t.me/BotFather)
- Your Telegram user ID (get it from [@userinfobot](https://t.me/userinfobot))
- (Optional) [SearXNG](https://searxng.org) for web search

## Setup

### 1. Pull the model

```bash
ollama pull qwen2.5:7b
```

### 2. Clone and configure

```bash
git clone <this-repo> /opt/hyrax
cd /opt/hyrax
cp .env.example .env
nano .env   # fill in TELEGRAM_TOKEN and OWNER_TELEGRAM_ID at minimum
```

### 3. Build and run

```bash
docker compose up -d
docker compose logs -f   # watch startup
```

### 4. Install as systemd service (recommended for production)

```bash
sudo cp hyrax.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable hyrax
sudo systemctl start hyrax
```

## Commands

| Command | What it does |
|---|---|
| `/start` | Hyrax introduces itself |
| `/help` | List all commands |
| `/memory` | See what Hyrax remembers about you |
| `/forget` | Clear permanent memory (`core.md`) |
| `/reset` | Clear today's conversation context |
| `/status` | Current model, Ollama health, uptime, memory sizes |

## Memory

Memory files live in `./memory/` (mounted as `/memory` in the container):

- `YYYY-MM-DD.md` — daily conversation notes, appended automatically
- `core.md` — permanent facts about you (only cleared by `/forget`)

Back up this directory if you want to preserve long-term memory across reinstalls.

## Configuration

See `.env.example` for all options.

| Variable | Default | Description |
|---|---|---|
| `TELEGRAM_TOKEN` | required | From @BotFather |
| `OWNER_TELEGRAM_ID` | required | Your Telegram user ID |
| `OLLAMA_HOST` | `http://172.17.0.1:11434` | Ollama API URL |
| `OLLAMA_MODEL` | `qwen2.5:7b` | Any model installed in Ollama |
| `CONTEXT_WINDOW_SIZE` | `128000` | Adjust for your model's context limit |
| `SEARXNG_HOST` | `""` (disabled) | Set to enable web search |
| `PROACTIVE_MAX_PER_DAY` | `2` | How often Hyrax texts you first (0–10, 0 = off) |

## macOS Local Development

Remove `network_mode: host` from `docker-compose.yml` and set in `.env`:

```
OLLAMA_HOST=http://host.docker.internal:11434
SEARXNG_HOST=http://host.docker.internal:8080
```

## Running Tests

```bash
uv sync
uv run pytest -v
```
