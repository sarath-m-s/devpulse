"""Developer fingerprint — builds a personal productivity profile from historical data."""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timedelta
from typing import Any

from ghost_pulse import db


def _parse_ts(ts: str) -> datetime | None:
    try:
        return datetime.strptime(ts[:19], "%Y-%m-%dT%H:%M:%S")
    except ValueError:
        return None


class DeveloperFingerprint:
    def __init__(self, llm_provider: Any = None) -> None:
        self.llm = llm_provider

    def generate_energy_map(self, days: int = 30) -> dict[str, Any]:
        """Analyze time-of-day productivity patterns."""
        since = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%S")
        events = db.query_events(event_type="shell_cmd", since=since)
        git_events = db.query_events(event_type="git_commit", since=since)

        # Bucket commands and commits by hour
        cmd_by_hour: Counter[int] = Counter()
        error_by_hour: Counter[int] = Counter()
        commit_by_hour: Counter[int] = Counter()
        cmd_by_day: Counter[int] = Counter()  # 0=Monday

        for ev in events:
            ts = _parse_ts(ev.get("timestamp", ""))
            if not ts:
                continue
            cmd_by_hour[ts.hour] += 1
            cmd_by_day[ts.weekday()] += 1
            exit_code = ev.get("data", {}).get("exit_code", 0)
            if exit_code != 0:
                error_by_hour[ts.hour] += 1

        for ev in git_events:
            ts = _parse_ts(ev.get("timestamp", ""))
            if ts:
                commit_by_hour[ts.hour] += 1

        # Build hourly profile
        hourly: list[dict[str, Any]] = []
        for h in range(24):
            cmds = cmd_by_hour[h]
            errors = error_by_hour[h]
            commits = commit_by_hour[h]
            error_rate = (errors / cmds * 100) if cmds > 0 else 0.0
            hourly.append(
                {
                    "hour": h,
                    "commands": cmds,
                    "commits": commits,
                    "error_rate": round(error_rate, 1),
                }
            )

        # Identify peak hours (top 3 by commit density + low error rate)
        scored = sorted(
            [h for h in hourly if h["commands"] > 5],
            key=lambda x: x["commits"] * 2 - x["error_rate"] * 0.5,
            reverse=True,
        )
        peak_hours = [h["hour"] for h in scored[:3]]
        low_hours = sorted(
            [h["hour"] for h in hourly if h["commands"] > 5 and h["error_rate"] > 20],
            key=lambda x: hourly[x]["error_rate"],
            reverse=True,
        )[:3]

        days_of_week = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
        best_day = days_of_week[cmd_by_day.most_common(1)[0][0]] if cmd_by_day else "unknown"
        worst_day_entry = sorted(cmd_by_day.items(), key=lambda x: x[1])
        worst_day = days_of_week[worst_day_entry[0][0]] if worst_day_entry else "unknown"

        return {
            "hourly": hourly,
            "peak_hours": peak_hours,
            "low_energy_hours": low_hours,
            "best_day": best_day,
            "worst_day": worst_day,
            "total_commands": sum(cmd_by_hour.values()),
            "total_commits": sum(commit_by_hour.values()),
        }

    def generate_workflow_fingerprint(self, days: int = 30) -> dict[str, Any]:
        """Analyze coding style and tool usage patterns."""
        since = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%S")
        events = db.query_events(event_type="shell_cmd", since=since)
        git_events = db.query_events(event_type="git_commit", since=since)

        if not events:
            return {"error": "insufficient data"}

        # Morning vs evening split
        morning_cmds = sum(1 for e in events if _parse_ts(e.get("timestamp", "")) and
                           6 <= (_parse_ts(e.get("timestamp", "")) or datetime.min).hour < 14)
        evening_cmds = len(events) - morning_cmds
        morning_pct = round(morning_cmds / max(len(events), 1) * 100)
        coder_style = "morning-focused" if morning_pct > 60 else "evening-focused" if morning_pct < 40 else "flexible-schedule"

        # Tool preferences (first word of each command)
        tool_counter: Counter[str] = Counter()
        for ev in events:
            cmd = ev.get("data", {}).get("cmd", "").strip()
            if cmd:
                parts = cmd.split()
                tool = parts[0].lower()
                # Filter noise (cd, ls, cat, echo...)
                if tool not in ("cd", "ls", "cat", "echo", "export", "source", ".", "alias"):
                    tool_counter[tool] += 1
        top_tools = [t for t, _ in tool_counter.most_common(5)]

        # Commit frequency and size
        commit_sizes: list[int] = []
        for ev in git_events:
            data = ev.get("data", {})
            insertions = data.get("insertions", 0) or 0
            deletions = data.get("deletions", 0) or 0
            commit_sizes.append(insertions + deletions)

        avg_commit_size = int(sum(commit_sizes) / len(commit_sizes)) if commit_sizes else 0
        commits_per_day = round(len(git_events) / max(days, 1), 1)
        commit_style = (
            "frequent-committer" if commits_per_day > 5
            else "batched-committer" if commits_per_day < 1
            else "regular-committer"
        )

        # Project breadth
        projects_per_day_set: dict[str, set[str]] = defaultdict(set)
        for ev in events:
            ts = _parse_ts(ev.get("timestamp", ""))
            proj = ev.get("project")
            if ts and proj and proj != "unknown":
                projects_per_day_set[ts.strftime("%Y-%m-%d")].add(proj)
        avg_projects_per_day = round(
            sum(len(v) for v in projects_per_day_set.values()) / max(len(projects_per_day_set), 1),
            1,
        )
        focus_style = "single-project" if avg_projects_per_day <= 1.5 else "multi-tasker"

        # Test-first vs test-after
        test_cmds = [e for e in events if "pytest" in (e.get("data", {}).get("cmd", "") or "").lower()
                     or "test" in (e.get("data", {}).get("cmd", "") or "").lower()]
        test_ratio = len(test_cmds) / max(len(events), 1)
        test_style = "test-after" if test_ratio < 0.1 else "test-focused"

        style_parts = [coder_style, test_style, focus_style, commit_style]
        return {
            "style": ", ".join(style_parts),
            "tools_top_5": top_tools,
            "avg_projects_per_day": avg_projects_per_day,
            "avg_commit_size_lines": avg_commit_size,
            "commits_per_day": commits_per_day,
            "morning_activity_pct": morning_pct,
        }

    def generate_focus_pattern(self, days: int = 30) -> dict[str, Any]:
        """Analyze deep work and context switching patterns."""
        from ghost_pulse.analyzers.context_switch import compute_context_switches

        since = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%S")
        ctx = compute_context_switches(since=since)

        deep_blocks = ctx.get("deep_work_blocks", [])
        durations = [b["duration_minutes"] for b in deep_blocks]
        avg_focus = round(sum(durations) / len(durations), 1) if durations else 0
        max_focus = max(durations) if durations else 0

        # Weekly fragmentation trend (compare first half vs second half of period)
        mid = (datetime.now() - timedelta(days=days // 2)).strftime("%Y-%m-%dT%H:%M:%S")
        ctx_recent = compute_context_switches(since=mid)
        ctx_older = compute_context_switches(
            since=since,
            until=mid,
        )
        frag_old = ctx_older.get("fragmentation_score", 50)
        frag_recent = ctx_recent.get("fragmentation_score", 50)
        if frag_recent < frag_old - 5:
            trend = "improving"
        elif frag_recent > frag_old + 5:
            trend = "worsening"
        else:
            trend = "stable"

        # Distractors from top transitions
        transitions = ctx.get("top_transitions", [])
        distractors = [f"{t['to_project']}" for t in transitions[:3]]

        # Best focus day
        since_dt = datetime.now() - timedelta(days=days)
        events = db.query_events(event_type="shell_cmd", since=since)
        blocks_by_day: Counter[int] = Counter()
        for b in deep_blocks:
            # We'd need the full date for each block — approximate from events
            pass
        # Simple proxy: most commands on a single project per day of week
        by_day_project: dict[str, Counter[str]] = defaultdict(Counter)
        for ev in events:
            ts = _parse_ts(ev.get("timestamp", ""))
            proj = ev.get("project") or "unknown"
            if ts and proj != "unknown":
                by_day_project[ts.strftime("%A")][proj] += 1
        best_focus_day = "Monday"
        best_focus_score = 0
        for day, proj_counts in by_day_project.items():
            top_proj_share = max(proj_counts.values()) / max(sum(proj_counts.values()), 1)
            if top_proj_share > best_focus_score:
                best_focus_score = top_proj_share
                best_focus_day = day

        return {
            "avg_focus_block_min": avg_focus,
            "longest_focus_block_min": max_focus,
            "total_deep_blocks": len(deep_blocks),
            "top_distractors": distractors,
            "best_focus_day": best_focus_day,
            "trend": trend,
            "fragmentation_score": ctx.get("fragmentation_score", 0),
            "switches_per_day": ctx.get("switches_per_day", 0),
        }

    def generate_full_profile(self, days: int = 30) -> dict[str, Any]:
        """Generate all three profiles and store them in DB."""
        period_start = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%S")
        period_end = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
        model_used = self.llm.name if self.llm else None

        energy = self.generate_energy_map(days)
        fingerprint = self.generate_workflow_fingerprint(days)
        focus = self.generate_focus_pattern(days)

        for profile_type, data in [
            ("energy_map", energy),
            ("workflow_fingerprint", fingerprint),
            ("focus_pattern", focus),
        ]:
            db.insert_developer_profile(
                profile_type=profile_type,
                data=data,
                period_start=period_start,
                period_end=period_end,
                model_used=model_used,
            )

        return {
            "energy_map": energy,
            "workflow_fingerprint": fingerprint,
            "focus_pattern": focus,
        }

    def get_latest_profile(self, profile_type: str | None = None) -> dict[str, Any] | None:
        """Get the most recent stored profile."""
        return db.get_latest_profile(profile_type=profile_type)

    def generate_insights(self, days: int = 30) -> str:
        """Use LLM to generate personalized narrative insights from all profiles."""
        energy = self.generate_energy_map(days)
        fingerprint = self.generate_workflow_fingerprint(days)
        focus = self.generate_focus_pattern(days)

        summary = (
            f"Energy map: peak hours {energy.get('peak_hours')}, "
            f"best day {energy.get('best_day')}\n"
            f"Workflow: {fingerprint.get('style', 'unknown')}, "
            f"top tools: {fingerprint.get('tools_top_5', [])}, "
            f"commits/day: {fingerprint.get('commits_per_day')}\n"
            f"Focus: avg block {focus.get('avg_focus_block_min')} min, "
            f"longest {focus.get('longest_focus_block_min')} min, "
            f"trend: {focus.get('trend')}, "
            f"best focus day: {focus.get('best_focus_day')}"
        )

        if not self.llm:
            return summary

        v2_prompt = (
            "You are Ghost Pulse, a personal developer productivity assistant analyzing the "
            "developer's workflow data. You have access to their energy patterns, error history, "
            "focus sessions, and workflow sequences.\n\n"
            "Provide insights that are:\n"
            "1. Specific — reference actual numbers, times, projects\n"
            "2. Actionable — suggest concrete changes, not abstract advice\n"
            "3. Honest — if the data shows bad patterns, say so kindly\n"
            "4. Comparative — compare this week to last week when possible\n\n"
            "Don't be preachy. Be a colleague who noticed something useful, not a productivity guru.\n"
            "Keep it under 200 words. Use plain language."
        )

        try:
            resp = self.llm.analyze(summary, system_prompt=v2_prompt)
            return resp.content.strip()
        except Exception:
            return summary
