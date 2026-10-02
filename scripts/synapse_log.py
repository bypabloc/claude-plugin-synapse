#!/usr/bin/env python3
"""Vista legible de la traza JSONL de Synapse (${CLAUDE_CONFIG_DIR:-~/.claude}/logs/synapse/AAAA-MM-DD.jsonl).

Agrupa los pasos por ejecución (run) y los muestra como bloques:

  $ python3 scripts/synapse_log.py                      # hoy
  $ python3 scripts/synapse_log.py --since 30m          # últimos 30 minutos (también 2h, 1d)
  $ python3 scripts/synapse_log.py --decision block     # solo bloqueos (allow | ask | block | pass)
  $ python3 scripts/synapse_log.py --hook block_dangerous --session 49c43f62
  $ python3 scripts/synapse_log.py --date 2026-10-01 --json   # JSONL crudo filtrado
  $ python3 scripts/synapse_log.py --follow             # en vivo
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "hooks"))
from common import get_log_file  # noqa: E402

COLORS = {"block": "\033[91m", "ask": "\033[93m", "allow": "\033[92m", "pass": "\033[90m"}
RESET = "\033[0m"
META = {"ts", "run", "session", "hook", "tool", "step"}


def parse_since(value: str) -> datetime:
    match = re.fullmatch(r"(\d+)([mhd])", value)
    if not match:
        raise argparse.ArgumentTypeError("formato: 30m, 2h, 1d")
    n, unit = int(match.group(1)), match.group(2)
    return datetime.now() - timedelta(**{{"m": "minutes", "h": "hours", "d": "days"}[unit]: n})


def files_for(args: argparse.Namespace) -> list[Path]:
    if args.since:
        day, today = args.since.date(), datetime.now().date()
        days = []
        while day <= today:
            days.append(datetime.combine(day, datetime.min.time()))
            day += timedelta(days=1)
        return [get_log_file(d) for d in days]
    day = datetime.strptime(args.date, "%Y-%m-%d") if args.date else datetime.now()
    return [get_log_file(day)]


def read_records(paths: list[Path]) -> list[dict]:
    records = []
    for path in paths:
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return records


def group_runs(records: list[dict]) -> list[list[dict]]:
    runs: dict[str, list[dict]] = {}
    for r in records:
        runs.setdefault(r.get("run", "-"), []).append(r)
    return list(runs.values())


def decision_of(run: list[dict]) -> str | None:
    return next((r.get("decision") for r in reversed(run) if r.get("step") == "decision"), None)


def matches(run: list[dict], args: argparse.Namespace) -> bool:
    first = run[0]
    if args.hook and first.get("hook") != args.hook:
        return False
    if args.session and not str(first.get("session", "")).startswith(args.session):
        return False
    if args.decision and decision_of(run) != args.decision:
        return False
    if args.since and datetime.fromisoformat(first["ts"]) < args.since:
        return False
    return True


def _oneline(value) -> str:
    return " ⏎ ".join(str(value).splitlines())


def _fields(record: dict) -> str:
    return "  ".join(f"{k}={_oneline(v)}" for k, v in record.items() if k not in META and v not in (None, "", []))


def render(run: list[dict], color: bool) -> str:
    first = run[0]
    ts = first["ts"].replace("T", " ")
    lines = [f"━━ {ts} ━━ {first.get('hook')} ━━ run {first.get('run')} ━━ session {first.get('session')}"]
    steps = [r for r in run if r["step"] not in ("input", "decision")]
    for r in run:
        if r["step"] == "input":
            lines.append(f"   tool    {r.get('tool')}")
            for key in ("cwd", "engine", "command", "file_path", "content_chars", "new_string_chars"):
                if key in r:
                    label = "input" if key in ("command", "file_path") else key
                    value = _oneline(r[key])
                    lines.append(f"   {label:7s} {value}")
    for i, r in enumerate(steps):
        branch = "└─" if i == len(steps) - 1 and not decision_of(run) else "├─"
        lines.append(f"   {branch} {r['step']:24s} {_fields(r)}")
    final = next((r for r in run if r["step"] == "decision"), None)
    if final:
        decision = final.get("decision", "?")
        tag = decision.upper()
        if color:
            tag = f"{COLORS.get(decision, '')}{tag}{RESET}"
        lines.append(f"   └─ DECISION  {tag}  ({final.get('ms', '?')} ms)  {_oneline(final.get('reason', ''))}")
    return "\n".join(lines)


def emit(runs: list[list[dict]], args: argparse.Namespace, color: bool) -> None:
    for run in runs:
        if not matches(run, args):
            continue
        if args.json:
            for r in run:
                print(json.dumps(r, ensure_ascii=False))
        else:
            print(render(run, color) + "\n")


def follow(args: argparse.Namespace, color: bool) -> None:
    path = get_log_file()
    offset = path.stat().st_size if path.exists() else 0
    pending: dict[str, list[dict]] = {}
    while True:
        if path.exists() and path.stat().st_size > offset:
            with open(path, encoding="utf-8") as f:
                f.seek(offset)
                chunk = f.read()
                offset = f.tell()
            for line in chunk.splitlines():
                try:
                    r = json.loads(line)
                except json.JSONDecodeError:
                    continue
                pending.setdefault(r.get("run", "-"), []).append(r)
                if r.get("step") == "decision":
                    emit([pending.pop(r["run"])], args, color)
        time.sleep(0.5)


def main() -> None:
    parser = argparse.ArgumentParser(description="Vista legible de la traza de Synapse")
    parser.add_argument("--date", help="AAAA-MM-DD (default: hoy)")
    parser.add_argument("--since", type=parse_since, help="ventana relativa: 30m, 2h, 1d")
    parser.add_argument("--session", help="prefijo del session_id de Claude Code")
    parser.add_argument("--hook", help="block_dangerous | block_env_read | detect_secrets | protect_files")
    parser.add_argument("--decision", choices=["allow", "ask", "block", "pass"])
    parser.add_argument("--json", action="store_true", help="JSONL crudo (para jq)")
    parser.add_argument("--follow", "-f", action="store_true", help="seguir el log de hoy en vivo")
    args = parser.parse_args()
    color = sys.stdout.isatty() and not os.environ.get("NO_COLOR")
    if args.follow:
        follow(args, color)
        return
    emit(group_runs(read_records(files_for(args))), args, color)


if __name__ == "__main__":
    main()
