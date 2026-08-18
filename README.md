# JARVIS (Voice + Desktop + Browser Automation)

JARVIS is a local automation assistant built with `Python + FastAPI + CLI/Voice + tool-based execution`.
It supports system commands, file actions, desktop automation, browser control (YouTube/WhatsApp), an
autonomous plan→execute→observe→re-plan agent, and config-driven job-application automation.

## What’s Improved

This version adds all requested upgrades across five phases:

1. **Playwright-first browser control** for YouTube + WhatsApp (with keyboard fallback).
2. **Typo-tolerant router** for noisy voice/text inputs.
3. **Desktop/Start Menu shortcut discovery** tools.
4. **Reliability layer**: tool retries, execution metadata, rotating logs, WAL-backed SQLite, VAD voice gating.
5. **Runtime context memory** (`active_surface`) for follow-up commands like `next`.
6. **Evaluation metrics dashboard** (latency, success/failure rates, top failing tools).
7. **Security hardening**: `API_TOKEN` auth, rate limiting, CORS, and a hardened shell policy.
8. **Autonomous agent loop** (`agent_done`, iteration/action caps, double-failure stop, approval handoff).
9. **Job-application automation**: search top job boards, auto-fill forms, upload a PDF resume, auto-submit,
   with a per-site selector config and a dedupe/audit trail.

## Quick Start (Windows)

1. Install dependencies:
```powershell
.\scripts\bootstrap.ps1
```
All versions live in `pyproject.toml`; `requirements.txt` is a thin `-e .[dev]` shim so `bootstrap.ps1` stays simple.

2. Install Playwright browser runtime (required for full browser automation):
```powershell
.\.venv\Scripts\playwright install chromium
```

3. Copy `.env.example` to `.env`, then set at minimum:
- `LLM_PROVIDER=openai` + `OPENAI_API_KEY=...`
- or `LLM_PROVIDER=groq` + `GROQ_API_KEY=...`
- or `LLM_PROVIDER=ollama` (with Ollama running)

4. Run API:
```powershell
.\scripts\run_api.ps1
```

5. Run CLI:
```powershell
.\scripts\run_cli.ps1
```

If PowerShell policy blocks scripts:
```cmd
scripts\run_cli.cmd
```

6. Run Desktop GUI popup:
```powershell
.\scripts\run_gui.ps1
```
Fallback (CMD):
```cmd
scripts\run_gui.cmd
```

## One-Command Start

Use this single command to start Jarvis popup:
```powershell
.\scripts\start_jarvis.ps1
```

With voice auto-start:
```powershell
.\scripts\start_jarvis.ps1 voice
```

CMD fallback:
```cmd
scripts\start_jarvis.cmd
scripts\start_jarvis.cmd voice
```

## Auto-Start

The HUD can start itself automatically in two places:

- **VS Code**: opening this folder runs a `folderOpen` task (`.vscode/tasks.json`) that launches the HUD.
- **Windows login**: a `JARVIS Auto Start` shortcut in the Startup folder launches the HUD.

Turn it off/on anytime:

- Click the **AUTOSTART ON / AUTOSTART OFF** chip in the HUD top bar, or
- `.\scripts\auto_start.ps1 off` / `on` / `status`

Disabled = the HUD does not auto-start until you re-enable it (manual `start_jarvis.ps1` always works).

## Architecture

```mermaid
flowchart LR
  A["Voice / CLI / API"] --> B["Orchestrator"]
  B --> C["Direct Router (typo + context aware)"]
  B --> D["LLM Planner"]
  B --> E["Autonomous Agent loop"]
  C --> F["Action Executor (retry + approval)"]
  D --> F
  E --> F
  F --> G["Tool Registry"]
  G --> H["System + File + Shell + Interaction + Browser + Agent + Job Tools"]
  B --> I["Memory Store (history + context + metrics + jobs)"]
```

## Core Direct Commands

### Browser / Media
- `play <query> on youtube`
- `youtube play <query>`
- `youtube pause`, `youtube resume`
- `youtube next`, `youtube previous`
- `youtube skip 30`, `youtube rewind 20`
- `youtube seek 1:25`
- `youtube jump to 50%`
- `send whatsapp to <phone>: <message>`
- `open whatsapp chat <phone>: <message>`

### Context-Aware Follow-Ups
If your active surface is YouTube, these work directly:
- `next`, `previous`, `pause`, `resume`
- `skip 20`, `rewind 10`
- `seek 2:30`

### Autonomous Agent
Commands that start with research/investigate/figure out/troubleshoot/diagnose/set up/automate/build me/
scan/audit/monitor/analyze/check if run through the multi-iteration agent loop instead of a single plan.

### Job Automation
- `apply for <query>` — search configured job boards and apply automatically (pauses on login/CAPTCHA).
- `job status` — current task status, phase, progress, and counters.
- `job applied` — audit trail of applications (dedupe across runs).
- `job cancel` — stop the running task.

### Discovery / Desktop
- `list shortcuts`
- `list apps`
- `open shortcut <name>`
- `open <app>`
- `close <app>`

### System / Files / Shell
- `system status`
- `list processes`
- `find files <pattern>`
- `read file <path>`
- `move file <src> -> <dst>`
- `run <powershell command>` (allowlist + approval policy)
- `take screenshot`

### Runtime Quality / Dashboard
- `metrics`
- `show dashboard`

## Job Automation Setup

1. Edit `config/job_profile.yaml` with your real identity, skills, work history, and form-field answers.
2. Edit `config/site_profiles.yaml` to enable/disable boards and fix selectors if a site's DOM changes.
3. Set `JOB_TARGET_SITES=linkedin,indeed,...` and `RESUME_PDF=data/resume/CV.pdf` in `.env`.
4. Run: `apply for python developer` (or `POST /v1/job/apply`).

Behavior notes:
- The agent skips jobs already applied to (URL dedupe in SQLite).
- `JOB_MAX_APPLY_PER_RUN` caps submissions per run; `JOB_APPLY_REMOTE_ONLY=true` only applies to remote roles.
- It pauses (and records `paused_reason`) on CAPTCHAs and login walls — it never bypasses CAPTCHAs.
- Uses one persistent Playwright profile (`BROWSER_PROFILE_DIR`), so you can pre-login once.

> **Terms of service caveat**: auto-applying (e.g. LinkedIn Easy Apply) can violate a job site's ToS.
> Keep `JOB_USE_BURNER_ACCOUNT=true` and use a throwaway account; run at modest volume.

## Voice Mode Notes

- Wake word: `jarvis`
- Follow-up window: after a wake-word command, short commands can run without repeating wake word (configurable).
- WhatsApp voice safety: by default, voice sends are staged first; say `confirm send` to send, or `cancel`.

Voice diagnostics in CLI:
- `/voice status`
- `/voice test`
- `/voice probe`
- `/voice devices`
- `/voice outputs`

## API Endpoints

- `GET /v1/health`
- `GET /v1/tools`
- `POST /v1/execute`
- `GET /v1/pending-approvals`
- `POST /v1/approve/{approval_id}`
- `GET /v1/history`
- `GET /v1/history/tools`
- `GET /v1/metrics`
- `POST /v1/voice/start`
- `POST /v1/voice/stop`
- `POST /v1/job/apply`
- `GET /v1/job/task`
- `POST /v1/job/cancel`
- `GET /v1/job/applied`

All endpoints except `/v1/health` require `Authorization: Bearer <API_TOKEN>` and are rate limited.

Example:
```bash
curl -X POST http://localhost:8000/v1/execute \
  -H "Authorization: Bearer $JARVIS_TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"command\":\"play lofi focus on youtube\",\"source\":\"api\",\"auto_approve\":false}"
```

## CLI Helpers

- `/help`
- `/pending`
- `/approve <id>`
- `/metrics [limit]`
- `/quit`

## Desktop GUI

`run_gui.ps1` (or `start_jarvis.ps1`) opens the JARVIS command HUD (`app/gui.py`):

- **Command bar** with ↑/↓ history + `EXECUTE` button, plus quick preset chips (Play/Pause, Next, Apps, MINI)
- **Live terminal + activity feed** — responses, tool statuses, and a scrolling ✓/⏳/✗ command feed
- **Voice engine** with a state-aware wave (LISTENING / PROCESSING / ATTENTION / IDLE) and start/stop toggle
- **Job agent panel** — live status (phase, applied/skipped/failed, pause reason) with APPLY / STATUS / CANCEL
- **Approvals panel**, **AI Bot chat**, **app launcher** (Alt+1..9), **resource meters** (CPU/RAM/network), scene viewer

**Mini floating window** (`app/minihud.py`):
- While you work elsewhere, a small always-on-top window shows live activity (current/last command, result status, voice state, CPU/RAM bars) and a quick-command box.
- Drag it anywhere; it auto-pops when you run a command and its position is remembered between runs.

## Important Config Knobs (`.env`)

- `API_TOKEN` — bearer token required for API calls
- `CORS_ALLOWED_ORIGINS` — comma-separated allowed origins
- `APPROVAL_POLICY`, `AUTO_APPROVE_LOW_RISK`
- `ALLOWED_SHELL_COMMANDS`, `ALLOWED_ROOTS`
- `DB_RETENTION_DAYS`
- `AGENT_MAX_ITERATIONS`, `AGENT_ACTION_CAP`, `AGENT_APPROVAL_POLICY`
- `JOB_TARGET_SITES`, `JOB_MAX_APPLY_PER_RUN`, `JOB_APPLY_REMOTE_ONLY`, `JOB_USE_BURNER_ACCOUNT`
- `JOB_PROFILE`, `JOB_SITE_PROFILES`, `RESUME_PDF`
- `VOICE_FOLLOWUP_WINDOW_SECONDS`, `VOICE_CONFIRM_BEFORE_SEND`
- `TOOL_RETRY_ATTEMPTS`, `TOOL_RETRY_BACKOFF_SECONDS`
- `BROWSER_PROFILE_DIR`, `BROWSER_AUTOMATION_TIMEOUT_MS`

## Safety Model

- Approval policy supports: `none`, `high_only`, `medium_and_high`, `all`.
- Shell commands are prefix-allowlisted and reject compound/pipe operators, destructive patterns,
  and obfuscated execution (`-EncodedCommand`, `Invoke-Expression`, `Bypass`, etc.).
- Protected OS processes are blocked from termination.
- File tools obey safe-root restrictions.
- The job agent never bypasses CAPTCHAs and pauses on login walls.

## Troubleshooting

1. **YouTube/WhatsApp control opens but doesn’t automate**
   - Install Playwright browser runtime: `playwright install chromium`
   - Retry command; fallback mode may still work with keyboard focus.

2. **WhatsApp doesn’t auto-send**
   - Ensure WhatsApp Web is logged in.
   - In voice mode, confirm flow needs `confirm send`.

3. **Job agent gets stuck or pauses**
   - Check `job status` / `GET /v1/job/task` for `paused_reason` (login/CAPTCHA).
   - Pre-login in the persistent browser profile, then re-run.
   - If a site’s selectors changed, update `config/site_profiles.yaml`.

4. **Voice feels slow**
   - Lower `SPEECH_AMBIENT_ADJUST_INTERVAL_SECONDS`
   - Tune `SPEECH_TIMEOUT_SECONDS` and `PHRASE_TIME_LIMIT_SECONDS`.

5. **Command not recognized due spelling/noise**
   - Use `/metrics` and inspect recent command failures.
   - Add aliases in `app/core/router.py` (`_WORD_ALIASES`).

## Testing

```bash
python -m pytest -q
ruff check app tests
mypy app
```

## Project Structure

```text
app/
  agents/
    job_agent.py          # autonomous job search + apply workflow
  bootstrap.py            # runtime assembly (registry, agent, job agent, voice)
  main.py                 # FastAPI app + job/voice/metrics routes
  cli.py
  gui.py                  # tkinter command HUD (dashboard)
  minihud.py              # draggable always-on-top mini window
  config.py               # env-driven settings
  core/
    agent.py              # plan->execute->observe->re-plan loop
    approvals.py
    executor.py
    memory.py             # SQLite history, context, metrics, jobs_applied
    monitor.py            # psutil resource metrics
    orchestrator.py
    prompts.py
    router.py
  llm/
    planner.py
  tools/
    agent_tools.py        # agent_done
    browser_action.py     # reusable browser DSL
    browser_session.py    # shared persistent Playwright session
    browser_tools.py
    file_tools.py
    interaction_tools.py
    job_tools.py
    metrics_tools.py
    shell_tools.py
    system_tools.py
    registry.py
  voice/
    listener.py
    speaker.py
    loop.py
config/
  job_profile.yaml        # your identity/skills/form answers
  site_profiles.yaml      # job-board search + selector profiles
tests/
scripts/                # launchers (.ps1/.cmd) + bootstrap + clean.ps1
```

## Maintenance

- `.\scripts\clean.ps1` removes generated caches (`__pycache__`, pytest/ruff caches) and prunes
  Chromium profile cache folders — it keeps your saved logins in `data/browser_profile`.
- `data/`, `logs/`, and `.venv/` are gitignored runtime state.
