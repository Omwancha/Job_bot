# Deploying JobBot with Docker

The bot runs as a single container. A cron scheduler (`supercronic`) inside the
container runs the pipeline once a day — the Python process no longer schedules
itself. State (the dedup database and any OAuth token) lives on a Docker volume
so it survives restarts and rebuilds, and secrets are injected from `.env` at
runtime rather than baked into the image.

## Prerequisites

- Docker Desktop running (Linux containers).
- A filled-in `.env` in this directory (copy `.env.template`). It is **not**
  copied into the image — it is read at runtime by `env_file`.

## Build and run

```bash
docker compose build      # builds the image (installs Chrome + deps)
docker compose up -d       # starts the scheduler in the background
docker compose logs -f     # follow output
```

The container schedules a daily run from `RUN_TIME` in `.env` (default `08:00`),
interpreted in the `TZ` set in `docker-compose.yml` (`Africa/Nairobi`).

## Run the pipeline once, on demand (e.g. to test)

This sends a real digest email:

```bash
docker compose run --rm jobbot python run.py --now
```

Or make the long-running container fire once on startup by setting
`RUN_ON_START: "true"` in `docker-compose.yml`.

## What maps where

| Concern            | Where it lives                                                    |
| ------------------ | ----------------------------------------------------------------- |
| Schedule           | `RUN_TIME` in `.env` → cron expr built by `docker-entrypoint.sh`  |
| Timezone           | `TZ` in `docker-compose.yml`                                      |
| Secrets            | `.env` (via `env_file`) — not in the image                        |
| Dedup DB           | `/data/jobbot.db` on the `jobbot-data` volume (`DB_PATH` override) |
| Logs               | container stdout → `docker compose logs`                          |
| Chrome / Selenium  | installed in the image; `USE_SELENIUM=true` enables Fuzu rendering |

## Persisting / inspecting state

The volume `jobbot-data` holds `jobbot.db`. To reset dedup (re-notify everything):

```bash
docker compose down
docker volume rm job_bot_jobbot-data
```

## Enabling auto-apply (optional)

`applier.py` uses an interactive OAuth consent flow that opens a browser — that
cannot run inside a headless container. To use auto-apply:

1. Generate the token once on a desktop (run the applier locally so the browser
   consent completes and writes `gmail_token.json`).
2. Copy it into the volume at `/data/credentials/gmail_token.json`, and place
   `gmail_oauth.json` next to it.
3. Set `auto_apply_enabled: true` in `profile.json`.

## Rotate exposed secrets

The Gmail app password and Anthropic key were previously committed in plaintext.
Revoke and reissue both, then put the new values in `.env`.
