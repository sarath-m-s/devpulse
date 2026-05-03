"""Lightweight stdlib-only HTTP server for the DevPulse web UI."""

from __future__ import annotations

import json
import mimetypes
import os
from datetime import datetime, timedelta
from html import escape as _esc
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

STATIC_DIR = Path(__file__).parent / "static"
_SERVER_START = datetime.now()


def _json(data) -> bytes:
    return json.dumps(data, default=str).encode()


def _today_start() -> str:
    return datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).strftime(
        "%Y-%m-%dT%H:%M:%S"
    )


def _week_start() -> str:
    return (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%S")


def _month_start() -> str:
    return (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%S")


def _day_start(days_ago: int) -> str:
    d = datetime.now() - timedelta(days=days_ago)
    return d.replace(hour=0, minute=0, second=0, microsecond=0).strftime(
        "%Y-%m-%dT%H:%M:%S"
    )


def _day_end(days_ago: int) -> str:
    d = datetime.now() - timedelta(days=days_ago - 1)
    return d.replace(hour=0, minute=0, second=0, microsecond=0).strftime(
        "%Y-%m-%dT%H:%M:%S"
    )


class DevPulseHandler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def _send(self, code: int, body: bytes, content_type: str = "application/json") -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self) -> None:
        self._send(204, b"")

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"

        if path == "/" or path == "/index.html":
            self._serve_file(STATIC_DIR / "index.html")
            return

        try:
            if path == "/api/stats/today":
                self._api_stats_today()
            elif path == "/api/stats/week":
                self._api_stats_week()
            elif path == "/api/projects":
                self._api_projects()
            elif path == "/api/toil":
                self._api_toil()
            elif path == "/api/events":
                qs = parse_qs(parsed.query)
                limit = int(qs.get("limit", ["50"])[0])
                self._api_events(limit)
            elif path == "/api/focus":
                self._api_focus()
            elif path == "/api/status":
                self._api_status()
            elif path == "/api/config":
                self._api_config_get()
            elif path == "/api/insights":
                self._api_insights_get()
            elif path == "/api/branches":
                self._api_branches()
            elif path.startswith("/api/"):
                self._send(404, _json({"error": "not found"}))
            else:
                self._send(404, b"Not found", "text/plain")
        except Exception as exc:
            self._send(500, _json({"error": str(exc)}))

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"

        content_len = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_len) if content_len else b""
        try:
            payload = json.loads(body) if body else {}
        except json.JSONDecodeError:
            payload = {}

        try:
            if path == "/api/config":
                self._api_config_post(payload)
            elif path == "/api/insights/ask":
                self._api_insights_ask(payload)
            elif path == "/api/daemon/restart":
                self._api_daemon_restart()
            elif path == "/api/daemon/stop":
                self._api_daemon_stop()
            else:
                self._send(404, _json({"error": "not found"}))
        except Exception as exc:
            self._send(500, _json({"error": str(exc)}))

    def _serve_file(self, path: Path) -> None:
        if not path.exists():
            self._send(404, b"Not found", "text/plain")
            return
        mime, _ = mimetypes.guess_type(str(path))
        body = path.read_bytes()
        self._send(200, body, mime or "application/octet-stream")

    # ── Stats endpoints ──────────────────────────────────────────────

    def _api_stats_today(self) -> None:
        from devpulse import db
        from devpulse.analyzers import time_tracker, context_switch

        since = _today_start()
        time_data = time_tracker.compute_time_per_project(since=since)
        ctx = context_switch.compute_context_switches(since=since)

        total_minutes = sum(p["total_minutes"] for p in time_data.values())
        total_commits = sum(p["commits"] for p in time_data.values())
        total_cmds = db.count_events_today()
        focus_score = max(0, 100 - int(ctx["fragmentation_score"]))

        blocks = ctx.get("deep_work_blocks", [])
        longest_block = None
        if blocks:
            best = max(blocks, key=lambda b: b.get("duration_minutes", 0))
            longest_block = {
                "minutes": round(best.get("duration_minutes", 0)),
                "start": best.get("start", ""),
                "end": best.get("end", ""),
                "project": best.get("project", ""),
            }

        projects = [
            {
                "name": proj,
                "minutes": round(stats["total_minutes"], 1),
                "commits": stats["commits"],
                "pct": round(stats["total_minutes"] / max(total_minutes, 1) * 100, 1),
            }
            for proj, stats in sorted(
                time_data.items(), key=lambda x: x[1]["total_minutes"], reverse=True
            )[:8]
        ]

        n_proj = len([p for p in projects if p["minutes"] > 0])

        self._send(200, _json({
            "total_minutes": round(total_minutes),
            "total_commits": total_commits,
            "total_cmds": total_cmds,
            "switches": ctx["switches"],
            "focus_score": focus_score,
            "fragmentation_score": ctx["fragmentation_score"],
            "projects": projects,
            "project_count": n_proj,
            "deep_work_blocks": blocks,
            "longest_block": longest_block,
        }))

    def _api_stats_week(self) -> None:
        from devpulse.analyzers import time_tracker, context_switch

        since = _week_start()
        time_data = time_tracker.compute_time_per_project(since=since)
        ctx = context_switch.compute_context_switches(since=since)

        total_minutes = sum(p["total_minutes"] for p in time_data.values())
        total_commits = sum(p["commits"] for p in time_data.values())

        projects = [
            {
                "name": proj,
                "minutes": round(stats["total_minutes"]),
                "hours": round(stats["total_minutes"] / 60, 1),
                "commits": stats["commits"],
                "pct": round(stats["total_minutes"] / max(total_minutes, 1) * 100, 1),
            }
            for proj, stats in sorted(
                time_data.items(), key=lambda x: x[1]["total_minutes"], reverse=True
            )[:10]
        ]

        blocks = ctx.get("deep_work_blocks", [])
        avg_block = 0
        if blocks:
            avg_block = round(
                sum(b.get("duration_minutes", 0) for b in blocks) / len(blocks)
            )

        day_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
        now = datetime.now()
        daily = []
        for i in range(6, -1, -1):
            d = now - timedelta(days=i)
            ds = d.replace(hour=0, minute=0, second=0, microsecond=0).strftime("%Y-%m-%dT%H:%M:%S")
            de = (d + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0).strftime("%Y-%m-%dT%H:%M:%S")
            day_time = time_tracker.compute_time_per_project(since=ds, until=de)
            day_ctx = context_switch.compute_context_switches(since=ds, until=de)
            day_mins = sum(p["total_minutes"] for p in day_time.values())
            day_commits = sum(p["commits"] for p in day_time.values())
            daily.append({
                "day": day_names[d.weekday()],
                "date": d.strftime("%Y-%m-%d"),
                "hours": round(day_mins / 60, 1),
                "minutes": round(day_mins),
                "commits": day_commits,
                "switches": day_ctx.get("switches", 0),
                "is_today": i == 0,
                "is_future": False,
            })

        best_day = max(daily, key=lambda d: d["minutes"]) if daily else None

        self._send(200, _json({
            "total_minutes": round(total_minutes),
            "total_hours": round(total_minutes / 60, 1),
            "total_commits": total_commits,
            "switches": ctx["switches"],
            "switches_per_day": ctx.get("switches_per_day", 0),
            "switches_per_hour": ctx.get("switches_per_hour", 0),
            "fragmentation_score": ctx["fragmentation_score"],
            "focus_score": max(0, 100 - int(ctx["fragmentation_score"])),
            "unique_projects": ctx.get("unique_projects", 0),
            "top_transitions": ctx.get("top_transitions", [])[:5],
            "projects": projects,
            "avg_focus_block": avg_block,
            "daily": daily,
            "best_day": {
                "day": best_day["day"],
                "hours": best_day["hours"],
                "commits": best_day["commits"],
            } if best_day else None,
        }))

    # ── Projects ─────────────────────────────────────────────────────

    def _api_projects(self) -> None:
        from devpulse.analyzers import time_tracker

        week_since = _week_start()
        month_since = _month_start()
        today_since = _today_start()

        week_data = time_tracker.compute_time_per_project(since=week_since)
        month_data = time_tracker.compute_time_per_project(since=month_since)
        today_data = time_tracker.compute_time_per_project(since=today_since)

        all_projects = set(week_data.keys()) | set(month_data.keys())
        total_week = max(sum(p["total_minutes"] for p in week_data.values()), 1)

        result = []
        for proj in sorted(all_projects, key=lambda p: week_data.get(p, {}).get("total_minutes", 0), reverse=True):
            ws = week_data.get(proj, {"total_minutes": 0, "commits": 0})
            ms = month_data.get(proj, {"total_minutes": 0, "commits": 0})
            ts = today_data.get(proj, {"total_minutes": 0, "commits": 0})
            result.append({
                "name": proj,
                "today_hours": round(ts["total_minutes"] / 60, 1),
                "today_minutes": round(ts["total_minutes"]),
                "week_hours": round(ws["total_minutes"] / 60, 1),
                "week_minutes": round(ws["total_minutes"]),
                "week_commits": ws["commits"],
                "month_hours": round(ms["total_minutes"] / 60, 1),
                "month_minutes": round(ms["total_minutes"]),
                "month_commits": ms["commits"],
                "pct": round(ws["total_minutes"] / total_week * 100, 1),
            })
        self._send(200, _json(result))

    # ── Toil ─────────────────────────────────────────────────────────

    def _api_toil(self) -> None:
        from devpulse.analyzers.toil import get_ranked_patterns, estimate_time_wasted

        patterns = get_ranked_patterns()[:10]
        result = [
            {
                "id": p.get("id"),
                "commands": p.get("commands", []),
                "label": " → ".join(p.get("commands", [])),
                "count": p.get("count", 1),
                "wasted_hours": round(estimate_time_wasted(p), 2),
            }
            for p in patterns
        ]
        self._send(200, _json(result))

    # ── Events ───────────────────────────────────────────────────────

    def _api_events(self, limit: int) -> None:
        from devpulse import db

        since = _week_start()
        events = db.query_events(since=since)[-limit:]
        result = []
        for e in reversed(events):
            data = e.get("data") or {}
            etype = e.get("event_type", "")
            text = ""
            color = "var(--surface-4)"

            if etype == "git_commit":
                msg = _esc(data.get("message", "")[:60])
                text = f'commit <code>{msg}</code>'
                color = "var(--success)"
            elif etype == "git_branch_switch":
                to_b = _esc(data.get("to_branch", ""))
                text = f'switched to <code>{to_b}</code>'
            elif etype == "shell_cmd":
                cmd = _esc(data.get("cmd", "")[:50])
                text = f'<code>{cmd}</code>'
                exit_code = data.get("exit_code", 0)
                color = "var(--success)" if exit_code == 0 else "var(--danger)"
            elif etype == "file_change":
                fpath = data.get("file", "") or data.get("path", "")
                fname = _esc(fpath.rsplit("/", 1)[-1] if fpath else "file")
                text = f'file changed <code>{fname}</code>'
                color = "var(--warn)"
            elif etype == "window_focus":
                app = _esc(data.get("app", ""))
                text = f'focused <code>{app}</code>'
            else:
                text = _esc(etype)

            ts = e.get("timestamp", "")
            try:
                dt = datetime.fromisoformat(ts)
                time_str = dt.strftime("%H:%M")
            except (ValueError, TypeError):
                time_str = ts[:5] if ts else ""

            result.append({
                "time": time_str,
                "color": color,
                "text": text,
                "proj": e.get("project") or "unknown",
                "type": etype,
                "timestamp": ts,
            })
        self._send(200, _json(result))

    # ── Focus ────────────────────────────────────────────────────────

    def _api_focus(self) -> None:
        from devpulse.analyzers import context_switch, time_tracker

        today = context_switch.compute_context_switches(since=_today_start())
        week = context_switch.compute_context_switches(since=_week_start())

        day_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
        now = datetime.now()
        daily_switches = []
        daily_focus = []
        for i in range(6, -1, -1):
            d = now - timedelta(days=i)
            ds = d.replace(hour=0, minute=0, second=0, microsecond=0).strftime("%Y-%m-%dT%H:%M:%S")
            de = (d + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0).strftime("%Y-%m-%dT%H:%M:%S")
            day_ctx = context_switch.compute_context_switches(since=ds, until=de)
            s = day_ctx.get("switches", 0)
            blocks = day_ctx.get("deep_work_blocks", [])
            longest = max((b.get("duration_minutes", 0) for b in blocks), default=0)
            cls = "none" if s == 0 else ("low" if s < 10 else ("med" if s < 20 else "high"))
            daily_switches.append({
                "day": day_names[d.weekday()],
                "switches": s,
                "cls": cls,
                "is_today": i == 0,
            })
            daily_focus.append({
                "day": day_names[d.weekday()],
                "longest_block": round(longest),
                "is_today": i == 0,
            })

        week_blocks = week.get("deep_work_blocks", [])
        avg_block = 0
        if week_blocks:
            avg_block = round(
                sum(b.get("duration_minutes", 0) for b in week_blocks) / len(week_blocks)
            )

        best_day_sw = min(
            (d for d in daily_switches if d["switches"] > 0),
            key=lambda x: x["switches"],
            default=None,
        )
        worst_day_sw = max(daily_switches, key=lambda x: x["switches"], default=None)

        self._send(200, _json({
            "today": {
                "switches": today["switches"],
                "fragmentation_score": today["fragmentation_score"],
                "focus_score": max(0, 100 - int(today["fragmentation_score"])),
                "deep_work_blocks": today.get("deep_work_blocks", []),
                "top_transitions": today.get("top_transitions", [])[:5],
            },
            "week": {
                "switches": week["switches"],
                "switches_per_day": week.get("switches_per_day", 0),
                "fragmentation_score": week["fragmentation_score"],
                "focus_score": max(0, 100 - int(week["fragmentation_score"])),
                "top_transitions": week.get("top_transitions", [])[:5],
                "avg_focus_block": avg_block,
                "best_day": best_day_sw["day"] if best_day_sw else "—",
                "worst_day": worst_day_sw["day"] if worst_day_sw else "—",
            },
            "daily_switches": daily_switches,
            "daily_focus": daily_focus,
        }))

    # ── Status ───────────────────────────────────────────────────────

    def _api_status(self) -> None:
        from devpulse.daemon import is_running, _read_pid
        from devpulse import db

        pid = _read_pid()
        db_path = db.get_db_path()
        db_size_bytes = db_path.stat().st_size if db_path.exists() else 0
        if db_size_bytes >= 1_048_576:
            db_size_str = f"{db_size_bytes / 1_048_576:.1f} MB"
        else:
            db_size_str = f"{db_size_bytes / 1024:.0f} KB"

        uptime_secs = (datetime.now() - _SERVER_START).total_seconds()
        h = int(uptime_secs // 3600)
        m = int((uptime_secs % 3600) // 60)
        uptime_str = f"{h}h {m:02d}m"

        self._send(200, _json({
            "daemon_running": is_running(),
            "pid": pid,
            "db_path": str(db_path),
            "db_size": db_size_str,
            "total_events": db.count_events_today(),
            "uptime": uptime_str,
            "version": "0.1.0",
        }))

    # ── Config ───────────────────────────────────────────────────────

    def _api_config_get(self) -> None:
        from devpulse.config import load_config

        cfg = load_config()
        self._send(200, _json(cfg))

    def _api_config_post(self, payload: dict) -> None:
        from devpulse.config import load_config, save_config, set_config_value

        cfg = load_config()
        for key, value in payload.items():
            set_config_value(cfg, key, value)
        save_config(cfg)
        self._send(200, _json({"ok": True}))

    # ── Insights ─────────────────────────────────────────────────────

    def _api_insights_get(self) -> None:
        from devpulse.config import load_config
        from devpulse.llm.factory import get_provider

        cfg = load_config()
        provider = get_provider(cfg)
        provider_name = provider.name
        model_name = cfg.get("llm", {}).get("model", "")
        available = provider.is_available()

        insights_text = ""
        if available:
            try:
                from devpulse.generators.report_gen import generate_insights
                insights_text = generate_insights(provider, days=30)
            except Exception as exc:
                insights_text = f"Error generating insights: {exc}"

        self._send(200, _json({
            "provider": provider_name,
            "model": model_name,
            "available": available,
            "insights": insights_text,
        }))

    def _api_insights_ask(self, payload: dict) -> None:
        from devpulse.config import load_config
        from devpulse.llm.factory import get_provider
        from devpulse.generators.report_gen import _build_activity_summary
        from devpulse.llm.base import DEVPULSE_SYSTEM_PROMPT

        question = payload.get("question", "").strip()
        if not question:
            self._send(400, _json({"error": "question is required"}))
            return

        cfg = load_config()
        provider = get_provider(cfg)
        if not provider.is_available():
            self._send(200, _json({
                "answer": "No LLM provider is configured or available. Run `devpulse config set llm.provider ollama` to set one up.",
            }))
            return

        summary = _build_activity_summary(days=30)
        prompt = f"{summary}\n\nUser question: {question}\n\nAnswer concisely based on the data above."
        try:
            response = provider.analyze(prompt, system_prompt=DEVPULSE_SYSTEM_PROMPT)
            self._send(200, _json({"answer": response.content.strip()}))
        except Exception as exc:
            self._send(200, _json({"answer": f"LLM error: {exc}"}))

    # ── Branches ─────────────────────────────────────────────────────

    def _api_branches(self) -> None:
        from devpulse import db

        since = _week_start()
        commit_events = db.query_events(event_type="git_commit", since=since)
        switch_events = db.query_events(event_type="git_branch_switch", since=since)

        branch_stats: dict[str, dict] = {}
        for e in commit_events:
            data = e.get("data") or {}
            branch = data.get("branch", "main")
            proj = e.get("project") or "unknown"
            key = f"{proj}/{branch}"
            if key not in branch_stats:
                branch_stats[key] = {
                    "branch": branch, "project": proj,
                    "commits": 0, "last_activity": e.get("timestamp", ""),
                }
            branch_stats[key]["commits"] += 1
            branch_stats[key]["last_activity"] = e.get("timestamp", "")

        for e in switch_events:
            data = e.get("data") or {}
            branch = data.get("to_branch", "")
            proj = e.get("project") or "unknown"
            key = f"{proj}/{branch}"
            if key not in branch_stats:
                branch_stats[key] = {
                    "branch": branch, "project": proj,
                    "commits": 0, "last_activity": e.get("timestamp", ""),
                }

        result = sorted(
            branch_stats.values(),
            key=lambda b: b["last_activity"],
            reverse=True,
        )[:10]

        for item in result:
            ts = item["last_activity"]
            try:
                dt = datetime.fromisoformat(ts)
                diff = datetime.now() - dt
                if diff.days == 0:
                    item["when"] = "today"
                elif diff.days == 1:
                    item["when"] = "yesterday"
                else:
                    item["when"] = f"{diff.days}d ago"
            except (ValueError, TypeError):
                item["when"] = ""

        self._send(200, _json(result))

    # ── Daemon control ───────────────────────────────────────────────

    def _api_daemon_restart(self) -> None:
        from devpulse.daemon import stop_daemon, start_daemon
        try:
            stop_daemon()
        except Exception:
            pass
        try:
            start_daemon()
            self._send(200, _json({"ok": True, "message": "Daemon restarted"}))
        except Exception as exc:
            self._send(500, _json({"error": str(exc)}))

    def _api_daemon_stop(self) -> None:
        from devpulse.daemon import stop_daemon
        try:
            stop_daemon()
            self._send(200, _json({"ok": True, "message": "Daemon stopped"}))
        except Exception as exc:
            self._send(500, _json({"error": str(exc)}))


def run(port: int = 8765) -> None:
    from devpulse import db
    db.init_db()

    server = HTTPServer(("127.0.0.1", port), DevPulseHandler)
    print(f"DevPulse UI → http://localhost:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
