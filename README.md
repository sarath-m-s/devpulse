

# Ghost Pulse

A privacy-first developer productivity copilot that runs entirely on your machine.



[Requirements](#requirements) • [Installation](#installation) • [Features](#features) • [Web Dashboard](#web-dashboard) • [CLI Reference](#cli-reference) • [Configuration](#configuration) • [Contributing](#contributing)



---

Ghost Pulse silently observes your workflow — shell commands, git activity, file edits, and window focus — then surfaces actionable insights: time-per-project breakdowns, repeated toil patterns, context-switch scores, deep work blocks, and auto-generated automation scripts.

**No data leaves your machine. LLM analysis is opt-in and provider-agnostic.**

```
╭─ Ghost Pulse · Thursday, April 23 ────────────────────────────╮
│                                                               │
│  ⏱  Active: 6h 23m    📊 Commands: 147    🔀 Switches: 12     │
│  🎯 Focus score: 72/100  (▲ 8 from yesterday)                 │
│                                                               │
├─ Projects ────────────────────────────────────────────────────┤
│                                                               │
│  colearn       ████████████████████░░░░  3h 42m  (58%)        │
│  ghost-pulse  ████████░░░░░░░░░░░░░░░  1h 31m  (24%)          │
│  other         ██░░░░░░░░░░░░░░░░░░░░░  0h 22m  ( 5%)         │
│                                                               │
├─ Toil detected ───────────────────────────────────────────────┤
│                                                               │
│  🔄 docker compose down → up -d           ×8 today (×23 wk)   │
│  🔄 git stash → checkout → pull → pop     ×3 today (×11 wk)   │
│     Run: ghost suggest 1  to auto-fix                         │
│                                                               │
├─ Deep work blocks ────────────────────────────────────────────┤
│                                                               │
│  09:15 - 10:48  colearn  (1h 33m) ████████████████            │
│  14:00 - 15:22  ghost-pulse (1h 22m) ██████████████           │
│                                                               │
╰───────────────────────────────────────────────────────────────╯
```

---

## Requirements

- **Python** 3.10 or newer (3.11+ recommended)
- **pip** (or another PEP 517 installer) for installing from PyPI
- **Git** on `PATH` — used for project discovery, the git collector, and history backfill. `ghost init` warns if it is missing.
- **Operating system** — macOS and Linux are fully supported for the terminal UI, collectors, and daemon. Native Windows is limited; use **WSL** for the same experience as Linux.

Optional, depending on features:

- **Local LLM (default)** — With `llm.provider = "ollama"` (the default), first-time setup can install [Ollama](https://ollama.com), start it, and pull models. That needs **curl** or **wget** on macOS/Linux, or **winget** on Windows. Skip this with `ghost init --skip-ollama` or `GHOST_PULSE_SKIP_OLLAMA=1`.
- **Linux window focus** — If you enable `collectors.window_tracker`, install **xdotool** (X11). `ghost init` mentions this on Linux when `xdotool` is not found.
- **Semantic / local embeddings** — Install optional extras, e.g. `pip install ghost-pulse[rag]`, for heavier ML dependencies when you use local embedding providers.

---

## Installation

From [PyPI](https://pypi.org/project/ghost-pulse/):

```bash
pip install ghost-pulse
ghost init --path ~/your-projects
```

Or install directly from GitHub:

```bash
pip install git+https://github.com/sarath-m-s/ghost-pulse.git
ghost init --path ~/your-projects
```

### What `ghost init` does

1. Creates `~/.ghost-pulse/` and writes `config.toml` if needed.
2. Registers project paths (`--path` or auto-discovery under common home directories).
3. Initializes the SQLite database (`~/.ghost-pulse/ghost-pulse.db`).
4. Prints hints if **git** (and on Linux, **xdotool** for window tracking) is missing.
5. If the LLM provider is **ollama** and the host is local, attempts to install Ollama, start the server, and pull default models — unless you pass **`--skip-ollama`** or set **`GHOST_PULSE_SKIP_OLLAMA=1`**.
6. Shows shell-hook instructions and reminds you to run **`ghost start`**.

The `--path` flag tells Ghost Pulse where your git repos live. It can be a **parent directory containing multiple repos** (e.g. `~/work`, `~/upskill`) or a single repo. Ghost Pulse automatically discovers all individual repos one level deep inside each folder. You can pass it multiple times:

```bash
ghost init --path ~/work --path ~/personal --path ~/oss
```

If you omit `--path`, Ghost Pulse scans common directories (`~/work`, `~/projects`, `~/code`, `~/src`, `~/upskill`, `~/dev`, `~/repos`, `~`) for git repos automatically. You can always add more later:

```bash
ghost projects add ~/another-folder
```

To see all discovered repos and their activity:

```bash
ghost projects
```

### Shell hooks

Shell hooks capture every command you run with zero perceptible latency (<50ms).

**zsh:**

```bash
echo 'source "$(ghost shell-hook --zsh)"' >> ~/.zshrc
source ~/.zshrc
```

**bash:**

```bash
echo 'source "$(ghost shell-hook --bash)"' >> ~/.bashrc
source ~/.bashrc
```

### Start the daemon

```bash
ghost start
```

The daemon runs in the background, polling for git commits, branch switches, file changes, and (optionally) window focus. It also handles periodic data cleanup.

### Verify

```bash
ghost status
ghost today
```

---

## Features

### Time tracking

Ghost Pulse estimates active development time per project by analyzing inter-command gaps, git commit activity, and window focus events. Commands are categorized into `git`, `infrastructure`, `testing`, `build`, `coding`, and `other`. An idle timeout (default 15 min) prevents inflated numbers.

```bash
ghost today          # today's per-project breakdown
ghost week           # 7-day summary with trends
```

### Toil detection

Finds repeated multi-command sequences (2-5 commands) you keep running manually. Patterns are normalized — variable parts like branch names, SHAs, file paths, and container IDs are stripped — so `git checkout feature-a` and `git checkout feature-b` are treated as the same pattern.

```bash
ghost toil --days 14         # show toil from last 2 weeks
ghost suggest 1              # generate a bash alias for pattern #1
```

`ghost suggest` sends the pattern to your configured LLM and generates a reusable shell alias or script, with an interactive flow to name and save it.

### Focus & context switching

Tracks how often you switch between projects and computes a focus score (0-100). Identifies deep work blocks — uninterrupted stretches on a single project — so you can see when you do your best work.

```bash
ghost today                  # includes focus score and deep work blocks
ghost focus                  # detailed focus sessions for today
ghost focus --guard on       # enable real-time focus guard notifications
ghost focus --threshold 20   # notify after 20 min of continuous focus is broken
```

The focus guard runs in the daemon. When you switch projects after a sustained focus session, it notifies you with the cost: *"You were focused for 42 minutes. Context switches typically cost ~23 minutes to recover."*

When you switch to a non-terminal app (Slack, Chrome, etc.), the focus guard detects the active app name via `osascript` (macOS) so the notification shows the real destination — not "unknown".

### Workflow prediction

Ghost Pulse learns your per-project command sequences over time and predicts what you'll do next.

```bash
ghost next                   # show predicted next commands for current project
ghost next colearn           # for a specific project
ghost next --run             # execute predictions immediately
ghost next --list            # list all learned routines
ghost next --dismiss 3       # stop suggesting routine #3
```

Predictions require at least 2 occurrences of a sequence. Confidence reaches 100% at 20+ repetitions.

### Error memory

Automatically records every failed command and links it to the commands that fixed it. Next time you hit the same error, Ghost Pulse surfaces the fix along with a debugging tip.

```bash
ghost recall                 # browse error history
ghost recall "CORS"          # search for specific error types
ghost recall --project myapp # filter by project
ghost recall --days 14       # limit to last 2 weeks
ghost recall --show-diff 5   # show git diff for error #5
```

Each error entry shows: how many times it occurred, when it last happened, what commands fixed it, and a **debugging tip** (heuristic or LLM-generated when a provider is configured).

### Context restore

Captures session snapshots when you stop working, so you can instantly re-orient when you come back.

```bash
ghost resume                 # list all projects with their last session
ghost resume colearn         # show full context for a specific project
ghost resume --open          # resume + open last file in $EDITOR
ghost resume --checkout      # also git checkout the saved branch
```

Snapshots include: git branch, last file edited, last command (with error indicator if it failed), uncommitted files, session duration, and an optional LLM-generated summary.

### Developer fingerprint

Analyzes your historical data to build a personal productivity profile with three components:

- **Energy map** — productivity by hour of day; identifies peak and low-energy hours
- **Workflow fingerprint** — coding style (morning/evening, test-first/after, commit frequency, top tools)
- **Focus pattern** — average deep work block duration, distractors, fragmentation trend

```bash
ghost profile                # show cached profile (generates if none exists)
ghost profile --regenerate   # force fresh analysis
ghost profile --type energy  # show only energy map
ghost profile --days 30      # use 30 days of data
```

### LLM-powered insights

Opt-in AI analysis that summarizes your activity into actionable productivity insights, including energy patterns, error analysis, and workflow fingerprint narrative.

```bash
ghost insights --days 7
```

### Shell history backfill

Already have months of shell history? Import it in one shot:

```bash
ghost backfill --shell auto --limit 5000
```

Supports zsh extended history format and bash timestamped history. Project names are inferred from `cd` commands using forward/backward propagation. Also backfills git commit history from your tracked repos.

### Data export

```bash
ghost export --from 2026-04-01 --to 2026-04-30 --format json
ghost export --format csv --output ~/activity.csv
```

### Web dashboard

A full-featured browser dashboard with no extra dependencies. See the [Web Dashboard](#web-dashboard) section below.

```bash
ghost web
```

---

## Data collectors

Ghost Pulse gathers data through four independent collectors, each toggleable in config:


| Collector          | Event types                       | How it works                                                                                                                                                           |
| ------------------ | --------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Shell**          | `shell_cmd`                       | Shell hooks call `ghost log-cmd` on every command with cwd, exit code, duration, and session ID. Project inferred from nearest `.git` parent.                       |
| **Git**            | `git_commit`, `git_branch_switch` | Polls registered project directories (default every 30s). Detects new commits (SHA, message, diff stats) and branch changes.                                           |
| **File watcher**   | `file_change`                     | Uses `watchdog` to monitor config/infra files: `Dockerfile`, `docker-compose.yml`, `Makefile`, `*.tf`, `package.json`, `pyproject.toml`, lock files, and `.env` files. |
| **Window tracker** | `window_focus`                    | Opt-in. Polls active window every 5s. macOS via `osascript`, Linux via `xdotool`. Only logs on window change.                                                          |


---

## Web dashboard

A single-page dark-themed dashboard served by a stdlib-only HTTP server (no Flask/FastAPI needed).

```bash
ghost web                    # start on port 8765, auto-opens browser
ghost web --port 9000        # custom port
ghost web --no-open          # don't auto-open browser
```

### Pages

- **Dashboard** — Stat cards (active time, commands, commits, context switches, focus score), project distribution charts for today and this week, deep work blocks timeline
- **Projects** — Time distribution doughnut, hours-per-project bar chart, detailed project table with progress bars
- **Toil** — Total wasted time, repetition and time-wasted charts, pattern detail cards with copy-to-clipboard for `ghost suggest`
- **Activity** — Scrollable event feed showing commands, exit codes, projects, and timestamps
- **Focus** — Today and weekly focus score gauges, context switch stats, deep work block list, most common project transitions

Built with Tailwind CSS and Chart.js. Auto-refreshes every 30 seconds.

### API endpoints


| Endpoint                  | Description                                                                                  |
| ------------------------- | -------------------------------------------------------------------------------------------- |
| `GET /api/stats/today`    | Today's stats: minutes, commands, commits, switches, focus score, projects, deep work blocks |
| `GET /api/stats/week`     | 7-day stats: hours, commits, switches/day, fragmentation, project breakdown                  |
| `GET /api/projects`       | All projects with hours, commits, percentage share                                           |
| `GET /api/toil`           | Top 10 toil patterns with command sequences, count, wasted hours                             |
| `GET /api/events?limit=N` | Recent events from the last 7 days                                                           |
| `GET /api/focus`          | Focus quality for today and week (scores, transitions, deep work blocks)                     |
| `GET /api/status`         | Daemon state, PID, DB path, version                                                          |


---

## CLI reference

### Core commands


| Command                               | Description                                                                                                         |
| ------------------------------------- | ------------------------------------------------------------------------------------------------------------------- |
| `ghost init [--path DIR] [--skip-ollama]` | First-time setup: config, project paths, DB, optional tool hints, and (for local Ollama) install/pull models unless skipped. |
| `ghost start`                      | Start the background collector daemon                                                                               |
| `ghost stop`                       | Stop the daemon                                                                                                     |
| `ghost status`                     | Show daemon state, commands today, most active project                                                              |
| `ghost today`                      | Rich terminal dashboard for today                                                                                   |
| `ghost week`                       | Rich terminal summary for the last 7 days                                                                           |
| `ghost web [--port N] [--no-open]` | Launch the web UI                                                                                                   |


### Analysis commands


| Command                                                             | Description                                                     |
| ------------------------------------------------------------------- | --------------------------------------------------------------- |
| `ghost toil [--days N]`                                          | List repeated command patterns (default: 14 days)               |
| `ghost suggest [id]`                                             | Generate an automation script for a toil pattern (requires LLM) |
| `ghost insights [--days N]`                                      | LLM-powered productivity insights (default: 7 days)             |
| `ghost next [project] [--run] [--list] [--dismiss ID]`           | Show/run predicted next actions                                 |
| `ghost recall [query] [--project P] [--days N] [--show-diff ID]` | Search error memory and past fixes                              |
| `ghost resume [project] [--open] [--checkout] [--json]`          | Restore session context for a project                           |
| `ghost profile [--regenerate] [--type T] [--days N] [--json]`    | Show developer fingerprint                                      |
| `ghost focus [--guard on|off] [--threshold N]`                   | Show focus sessions, configure focus guard                      |


### Data commands


| Command                                                                         | Description                          |
| ------------------------------------------------------------------------------- | ------------------------------------ |
| `ghost backfill [--shell auto|zsh|bash] [--limit N] [--no-git]`              | Import shell history and git commits |
| `ghost export [--from DATE] [--to DATE] [--format json|csv] [--output PATH]` | Export events                        |
| `ghost reset [--keep-config]`                                                | Delete all collected data            |


### Config commands


| Command                             | Description                                     |
| ----------------------------------- | ----------------------------------------------- |
| `ghost config`                   | Print current configuration                     |
| `ghost config set <key> <value>` | Set a config value (e.g. `llm.provider ollama`) |
| `ghost config providers`         | Test all LLM providers and show availability    |


### Project commands


| Command                           | Description                                         |
| --------------------------------- | --------------------------------------------------- |
| `ghost projects`               | List all discovered repos with 7-day activity stats |
| `ghost projects add <path>`    | Add a project directory to track                    |
| `ghost projects remove <name>` | Stop tracking a project                             |


---

## LLM providers

Ghost Pulse works fully without an LLM — time tracking, toil detection, dashboards, focus scoring, workflow prediction, error memory, and the web UI all work offline. LLM is only used for `ghost suggest`, `ghost insights`, and optional summaries in `ghost resume` and `ghost profile`.


| Provider   | Model                    | Cost        | Privacy | Setup                                     |
| ---------- | ------------------------ | ----------- | ------- | ----------------------------------------- |
| **Ollama** | llama3.2:3b (local)      | Free        | Local   | `ghost config set llm.provider ollama` |
| **Groq**   | llama-3.1-70b            | Free tier   | Cloud   | `ghost config set llm.provider groq`   |
| **Claude** | claude-sonnet-4-20250514 | ~$0.008/req | Cloud   | `ghost config set llm.provider claude` |
| **OpenAI** | gpt-4o-mini              | ~$0.004/req | Cloud   | `ghost config set llm.provider openai` |
| **None**   | —                        | —           | —       | `ghost config set llm.provider none`   |


### Ollama (recommended — free & local)

```bash
curl -fsSL https://ollama.com/install.sh | sh
ollama pull llama3.2:3b
ghost config set llm.provider ollama
```

### Groq (free tier)

```bash
ghost config set llm.provider groq
ghost config set llm.groq.api_key gsk_xxxxx    # from console.groq.com
```

### Claude

```bash
ghost config set llm.provider claude
ghost config set llm.claude.api_key sk-ant-xxxxx    # or set ANTHROPIC_API_KEY
```

### OpenAI

```bash
ghost config set llm.provider openai
ghost config set llm.openai.api_key sk-xxxxx    # or set OPENAI_API_KEY
```

### Verify providers

```bash
ghost config providers
```

---

## Configuration

Config file: `~/.ghost-pulse/config.toml`

```toml
[general]
data_retention_days = 90        # Auto-delete events older than N days
toil_threshold = 5              # Min repetitions to flag a pattern
idle_timeout_minutes = 15       # Gap before counting as idle
poll_interval_seconds = 30      # Git/window polling interval

[projects]
paths = ["~/work/myproject"]    # Auto-detected from git repos

[llm]
provider = "ollama"             # "ollama", "groq", "claude", "openai", "none"

[llm.ollama]
host = "http://localhost:11434"
model = "llama3.2:3b"

[llm.groq]
api_key = ""                    # or GROQ_API_KEY env var

[llm.claude]
api_key = ""                    # or ANTHROPIC_API_KEY env var
model = "claude-sonnet-4-20250514"

[llm.openai]
api_key = ""                    # or OPENAI_API_KEY env var
model = "gpt-4o-mini"
base_url = ""                   # for OpenAI-compatible APIs

[collectors]
shell = true
git = true
file_watcher = true
window_tracker = false          # opt-in, macOS/Linux X11 only

[ui]
color_theme = "auto"

[v2]
# Workflow prediction
prediction_confidence_threshold = 0.3   # minimum confidence to show a prediction
prediction_learning_days = 30           # days of history to learn from
auto_execute_predictions = false        # if true, 'ghost next --run' skips confirmation

# Error memory
error_retention_days = 180             # how long to keep error records
auto_record_errors = true              # automatically record non-zero exit codes
error_similarity_threshold = 0.8       # fuzzy match threshold (reserved)

# Focus guard
focus_guard_enabled = true
focus_threshold_minutes = 15           # minimum minutes before counting as focus session
focus_notification_method = "terminal" # "terminal", "desktop", "both", "none"
focus_cooldown_minutes = 5             # don't notify again within this window

# Context restorer
session_gap_minutes = 30               # inactivity gap that ends a session
auto_snapshot = true                   # auto-capture snapshots on session end

# Developer profile
profile_auto_generate = true           # regenerate profile weekly
profile_days = 30                      # days of data to analyze
```

---

## Privacy

Ghost Pulse is designed to be privacy-first:

- **All data stays local** — stored in `~/.ghost-pulse/ghost-pulse.db` (SQLite with WAL mode)
- **No telemetry** — the core app does not phone home; optional features may use the network (cloud LLM APIs, or downloading Ollama/models during `ghost init` when using local Ollama)
- **LLM is optional** — time tracking, dashboards, and most features work offline; `suggest`, `insights`, and some summaries need a configured provider
- **You choose the provider** — use Ollama for 100% local inference
- **Data retention** — old events auto-deleted after 90 days (configurable)
- **Full data ownership** — export or delete everything at any time with `ghost export` / `ghost reset`

---

## Platform support


| Platform | Shell hooks | Git collector | File watcher | Window tracker     |
| -------- | ----------- | ------------- | ------------ | ------------------ |
| macOS    | zsh, bash   | Yes           | Yes          | Yes (osascript)    |
| Linux    | zsh, bash   | Yes           | Yes          | X11 only (xdotool) |
| WSL      | zsh, bash   | Yes           | Yes          | No                 |
| Windows  | Use WSL     | —             | —            | —                  |


---

## Architecture

```
~/.ghost-pulse/
├── config.toml          # User configuration
├── ghost-pulse.db       # SQLite database (WAL mode)
└── scripts/             # Generated automation scripts

ghost_pulse/
├── cli.py               # Typer CLI entry point
├── config.py            # Config management with auto-detection
├── db.py                # SQLite with thread-safe writes
├── daemon.py            # Background daemon (double-fork)
├── collectors/
│   ├── shell.py         # Shell command logging + history backfill
│   ├── git_collector.py # Git commit/branch polling + history backfill
│   ├── file_watcher.py  # Config/infra file change detection
│   └── window.py        # Active window tracking (opt-in)
├── analyzers/
│   ├── time_tracker.py  # Per-project time estimation
│   ├── context_switch.py# Focus scoring + deep work blocks
│   ├── toil.py          # Repeated pattern detection
│   ├── workflow_predictor.py  # Command sequence learning + prediction
│   ├── error_memory.py        # Failed command tracking + fix linking
│   ├── context_restorer.py    # Session snapshot + resume
│   ├── developer_fingerprint.py # Energy map, style, focus profiling
│   └── focus_guard.py          # Real-time focus session monitoring
├── generators/
│   ├── script_gen.py    # LLM-powered automation scripts
│   └── report_gen.py    # LLM-powered insights + daily summaries
├── llm/
│   ├── base.py             # Provider interface
│   ├── factory.py          # Auto-detection + instantiation
│   ├── ollama_provider.py  # Ollama (local)
│   ├── groq_provider.py    # Groq (cloud, free tier)
│   ├── claude_provider.py  # Claude (cloud)
│   └── openai_provider.py  # OpenAI (cloud)
├── ui/
│   ├── dashboard.py     # Rich terminal dashboards
│   └── tui/             # Textual TUI screens
└── web/
    ├── server.py        # Stdlib HTTP server + API
    └── static/
        └── index.html   # SPA dashboard (Tailwind + Chart.js)
```

---

## Development

```bash
git clone https://github.com/sarath-m-s/ghost-pulse.git
cd ghost-pulse
pip install -e ".[all,dev]"
make test
```

Build release artifacts (sdist + wheel) with `python -m build` from the repo root; upload to PyPI with `twine upload dist/*` after configuring [PyPI trusted publishing](https://docs.pypi.org/trusted-publishers/) or API tokens.

### Makefile targets


| Target          | Description                                  |
| --------------- | -------------------------------------------- |
| `make install`  | Editable install (core deps only)            |
| `make dev`      | Install with all optional + dev dependencies |
| `make test`     | Run pytest                                   |
| `make test-cov` | Run tests with coverage report               |
| `make lint`     | Compile-check all Python files               |
| `make clean`    | Remove build artifacts                       |


### Running tests

```bash
make test               # quick run
make test-cov           # with coverage
```

Tests cover the database layer, all collectors, all analyzers, and the LLM provider abstraction. All tests use temporary directories for full isolation.

---

## Contributing

1. Fork the repo
2. Create a branch: `git checkout -b feature/my-feature`
3. Write tests for new code
4. Run `make test` — all tests must pass
5. Open a pull request

Please keep the core (`pip install ghost-pulse`) dependency-free from paid LLM providers.

---

## License

[MIT](LICENSE)
