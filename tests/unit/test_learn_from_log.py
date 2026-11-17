"""scripts/learn_from_log.py: resumen de la traza por proyecto y replay con las reglas actuales."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

from support import PROJECT_DIR, make_git_repo

spec = importlib.util.spec_from_file_location("learn_from_log", PROJECT_DIR / "scripts" / "learn_from_log.py")
assert spec and spec.loader
learn = importlib.util.module_from_spec(spec)
spec.loader.exec_module(learn)


def _write_trace(log_dir: Path, project: Path) -> None:
    rows = []
    for i, (command, decision, by, rule, laya) in enumerate([
        ("bun run typecheck", "ask", "laya", "laya.danger", "flagged"),
        ("git worktree list", "pass", "laya", "sin_objeciones", "ok"),
        ("rg -n 'DROP DATABASE' src", "ask", "python", "regex.ask", None),
        ("rm -rf /", "block", "python", "regex.catastrophic", None),
    ]):
        run = f"r{i}"
        rows.append({"run": run, "hook": "block_dangerous", "step": "input", "cwd": str(project), "command": command})
        if laya:
            rows.append({"run": run, "hook": "block_dangerous", "step": "laya", "result": laya})
        rows.append({"run": run, "hook": "block_dangerous", "step": "decision", "decision": decision, "decided_by": by, "rule": rule, "ms": 200.0 if laya else 5.0})
    rows.append({"run": "x", "hook": "block_dangerous", "step": "input", "cwd": "/otro/proyecto", "command": "ls"})
    log_dir.mkdir(parents=True, exist_ok=True)
    (log_dir / "2026-11-16.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n")


def test_summary_and_replay(tmp_path: Path, monkeypatch) -> None:
    project = make_git_repo("learn", tracked={"src/app.py": "x = 1\n"}, untracked={})
    log_dir = tmp_path / "logs"
    _write_trace(log_dir, project)
    runs = learn.load_runs(log_dir, str(project))
    assert len(runs) == 4  # el run de /otro/proyecto queda fuera

    summary = learn.summarize(runs)
    assert summary["laya_consulted"] == 2
    assert [a["target"] for a in summary["laya_asks"]] == ["bun run typecheck"]
    assert summary["latency"]["laya"]["n"] == 2

    monkeypatch.chdir(project)
    results = {r["command"]: r for r in learn.replay(runs, str(project), 0)}
    assert results["bun run typecheck"]["before"] == "ask(laya)"
    assert results["bun run typecheck"]["after"] == "pass"
    assert results["git worktree list"]["after"] == "pass"
    assert results["rg -n 'DROP DATABASE' src"]["after"] == "pass"
    assert results["rm -rf /"]["after"] == "block"
