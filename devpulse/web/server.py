"""Lightweight stdlib-only HTTP server for the DevPulse web UI."""

from __future__ import annotations

import json
import mimetypes
import os
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

STATIC_DIR = Path(__file__).parent / "static"


def _json(data) -> bytes:
    return json.dumps(data, default=str).encode()


def _today_start() -> str:
    return datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).strftime(
        "%Y-%m-%dT%H:%M:%S"
    )


def _week_start() -> str:
    return (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%S")


class DevPulseHandler(BaseHTTPRequestHandler):
    def log_message(self, *_):  # silence request logs
        pass

    def _send(self, code: int, body: bytes, content_type: str = "application/json") -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"

        # Static files
        if path == "/" or path == "/index.html":
            self._serve_file(STATIC_DIR / "index.html")
            return

        # API routes
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
            elif path.startswith("/api/"):
                self._send(404, _json({"error": "not found"}))
            else:
                self._send(404, b"Not found", "text/plain")
        except Exception as exc:
            self._send(500, _json({"error": str(exc)}))

    def _serve_file(self, path: Path) -> None:
        if not path.exists():
            self._send(404, b"Not found", "text/plain")
            return
        mime, _ = mimetypes.guess_type(str(path))
        body = path.read_bytes()
        self._send(200, body, mime or "application/octet-stream")

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

        projects = [
            {
                "name": proj,
                "minutes": stats["total_minutes"],
                "commits": stats["commits"],
                "pct": round(stats["total_minutes"] / max(total_minutes, 1) * 100, 1),
            }
            for proj, stats in sorted(
                time_data.items(), key=lambda x: x[1]["total_minutes"], reverse=True
            )[:8]
        ]

        self._send(200, _json({
            "total_minutes": round(total_minutes),
            "total_commits": total_commits,
            "total_cmds": total_cmds,
            "switches": ctx["switches"],
            "focus_score": focus_score,
            "fragmentation_score": ctx["fragmentation_score"],
            "projects": projects,
            "deep_work_blocks": ctx.get("deep_work_blocks", []),
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
        }))

    def _api_projects(self) -> None:
        from devpulse.analyzers import time_tracker

        since = _week_start()
        time_data = time_tracker.compute_time_per_project(since=since)
        total_m = max(sum(p["total_minutes"] for p in time_data.values()), 1)

        result = [
            {
                "name": proj,
                "hours": round(stats["total_minutes"] / 60, 1),
                "minutes": round(stats["total_minutes"]),
                "commits": stats["commits"],
                "pct": round(stats["total_minutes"] / total_m * 100, 1),
            }
            for proj, stats in sorted(
                time_data.items(), key=lambda x: x[1]["total_minutes"], reverse=True
            )
        ]
        self._send(200, _json(result))

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

    def _api_events(self, limit: int) -> None:
        from devpulse import db

        since = _week_start()
        events = db.query_events(since=since)[-limit:]
        result = [
            {
                "id": e.get("id"),
                "timestamp": e.get("timestamp"),
                "type": e.get("event_type"),
                "project": e.get("project") or "unknown",
                "cmd": e.get("data", {}).get("cmd", "") if e.get("data") else "",
                "exit_code": e.get("data", {}).get("exit_code", 0) if e.get("data") else 0,
            }
            for e in reversed(events)
        ]
        self._send(200, _json(result))

    def _api_focus(self) -> None:
        from devpulse.analyzers import context_switch

        today = context_switch.compute_context_switches(since=_today_start())
        week = context_switch.compute_context_switches(since=_week_start())

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
            },
        }))

    def _api_status(self) -> None:
        from devpulse.daemon import is_running, _read_pid
        from devpulse import db

        self._send(200, _json({
            "daemon_running": is_running(),
            "pid": _read_pid(),
            "db_path": str(db.get_db_path()),
            "total_events": db.count_events_today(),
            "version": "0.1.0",
        }))


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
