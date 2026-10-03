"""Cada bloqueo y cada 'ask' dicen quién decidió (Python o Laya), con qué regla y por qué: en pantalla y en el log."""

from __future__ import annotations

import importlib
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

import pytest

import common
from support import HOOKS_DIR, PROJECT_DIR, load_cases, materialize_setup_file, run_in_process

HOOKS = ["block_dangerous", "block_env_read", "detect_secrets", "protect_files"]
ALL_CASES = [(hook, case) for hook in HOOKS for case in load_cases(hook)]
DECIDED = [(h, c) for h, c in ALL_CASES if c["expected_exit"] == 2 or c.get("expected_decision") in {"ask", "allow"}]


def records() -> list[dict]:
    return [json.loads(line) for line in common.get_log_file().read_text(encoding="utf-8").splitlines()]


def final_decision() -> dict:
    return [r for r in records() if r["step"] == "decision"][-1]


@pytest.mark.parametrize(("hook", "case"), DECIDED, ids=[f"{h}:{c['name']}" for h, c in DECIDED])
def test_every_block_and_ask_says_who_decided(hook: str, case: dict, isolated_logs) -> None:
    setup = materialize_setup_file(case)
    try:
        result = run_in_process(hook, case["payload"])
    finally:
        if setup:
            setup.unlink(missing_ok=True)
    final = final_decision()
    assert final["decided_by"] == "python" and final["rule"] and final["reason"]
    shown = result.stderr if result.exit_code == 2 else json.loads(result.stdout)["hookSpecificOutput"]["permissionDecisionReason"]
    assert f"Synapse · {hook} (python) · regla {final['rule']}" in shown


FLAGGING = {
    "detect_secrets": ({"secret_status": {"choice": "confidential_secret", "answer_confidence": 0.97},
                        "secret_type": {"choice": "api_token", "answer_confidence": 0.9}},
                       {"tool_name": "Write", "tool_input": {"file_path": "a.py", "content": "auth_token = '" + "f3K9xQ2mL7pR" + "4tW8zB1nV6cY'"}}),
    "protect_files": ({"file_sensitivity": {"choice": "critical_blocked", "answer_confidence": 0.95}},
                      {"tool_name": "Write", "tool_input": {"file_path": "src/config/secret_settings.py", "content": "x"}}),
    "block_env_read": ({"env_intent": {"choice": "read_or_exfiltrate", "answer_confidence": 0.93}},
                       {"tool_name": "Bash", "tool_input": {"command": "python3 tools/show.py .env.local"}}),
    "block_dangerous": ({"danger": {"choice": "A", "probabilities": {"A": 0.9999, "B": 0.0001}}},
                        {"tool_name": "Bash", "tool_input": {"command": "python3 scripts/migrate.py"}}),
}


def _with_router(hook: str, router: mock.Mock, payload: dict, cwd: Path):
    mod = importlib.import_module(hook)
    patches = [mock.patch.object(mod, "should_use_laya", return_value=True), mock.patch.object(mod, "get_laya_router", return_value=router)]
    if hasattr(mod, "apply_finetuned_delta"):
        patches.append(mock.patch.object(mod, "apply_finetuned_delta", return_value=True))
    for p in patches:
        p.start()
    try:
        return run_in_process(hook, {**payload, "cwd": str(cwd)})
    finally:
        for p in patches:
            p.stop()


@pytest.mark.parametrize("hook", sorted(FLAGGING))
def test_laya_ask_says_laya_decided_with_evidence(hook: str, tmp_path, isolated_logs) -> None:
    answers, payload = FLAGGING[hook]
    router = mock.Mock()
    router.predict.return_value = {"answers": answers}
    result = _with_router(hook, router, payload, tmp_path)
    reason = json.loads(result.stdout)["hookSpecificOutput"]["permissionDecisionReason"]
    final = final_decision()
    assert reason.startswith(f"Synapse · {hook} (laya) · regla {final['rule']}")
    assert final["decided_by"] == "laya" and final["rule"].startswith("laya.") and final["evidence"]


@pytest.mark.parametrize("hook", sorted(FLAGGING))
def test_laya_errors_are_logged(hook: str, tmp_path, isolated_logs) -> None:
    _, payload = FLAGGING[hook]
    router = mock.Mock()
    router.predict.side_effect = TimeoutError("el daemon de Laya no respondió")
    result = _with_router(hook, router, payload, tmp_path)
    assert result.exit_code == 0
    errors = [r for r in records() if r["step"] == "laya" and r.get("result") == "error"]
    assert errors and "no respondió" in errors[0]["error"]


def test_pass_after_laya_is_attributed_to_laya(tmp_path, isolated_logs) -> None:
    router = mock.Mock()
    router.predict.return_value = {"answers": {"danger": {"choice": "B", "probabilities": {"A": 0.001, "B": 0.999}}}}
    _with_router("block_dangerous", router, FLAGGING["block_dangerous"][1], tmp_path)
    final = final_decision()
    assert final["decision"] == "pass" and final["decided_by"] == "laya"


def test_pass_without_laya_is_attributed_to_python(tmp_path, isolated_logs) -> None:
    run_in_process("block_dangerous", {"tool_name": "Bash", "tool_input": {"command": "ls -la"}, "cwd": str(tmp_path)})
    final = final_decision()
    assert final["decision"] == "pass" and final["decided_by"] == "python" and final["rule"]


@pytest.mark.parametrize(
    ("hook", "payload"),
    [
        ("block_dangerous", {"tool_name": "Bash", "tool_input": {"command": "   "}}),
        ("detect_secrets", {"tool_name": "Write", "tool_input": {"file_path": "a.py", "content": ""}}),
        ("protect_files", {"tool_name": "Read", "tool_input": {"file_path": "a.py"}}),
    ],
)
def test_early_exits_close_the_run_with_a_decision(hook: str, payload: dict, tmp_path) -> None:
    log_dir = tmp_path / "logs"
    env = {**os.environ, "SYNAPSE_LOG_DIR": str(log_dir)}
    subprocess.run([sys.executable, str(HOOKS_DIR / f"{hook}.py"), "--fallback"], input=json.dumps(payload), text=True, env=env, check=False)
    lines = [json.loads(line) for f in log_dir.glob("*.jsonl") for line in f.read_text().splitlines()]
    assert lines[0]["step"] == "input" and lines[-1]["step"] == "decision"
    assert lines[-1]["decision"] == "pass" and lines[-1]["rule"] == "exit.early"


def test_log_viewer_shows_who_decided(isolated_logs) -> None:
    run_in_process("block_dangerous", {"tool_name": "Bash", "tool_input": {"command": "rm -rf /"}, "cwd": str(PROJECT_DIR)})
    out = subprocess.run([sys.executable, str(PROJECT_DIR / "scripts" / "synapse_log.py")], capture_output=True, text=True,
                         env={**os.environ}, check=True).stdout
    assert "DECISION  BLOCK" in out and "python · regex.catastrophic" in out


def test_old_logs_are_pruned_by_retention(isolated_logs: Path, monkeypatch) -> None:
    isolated_logs.mkdir(parents=True, exist_ok=True)
    old = isolated_logs / f"{datetime.now() - timedelta(days=40):%Y-%m-%d}.jsonl"
    recent = isolated_logs / f"{datetime.now() - timedelta(days=5):%Y-%m-%d}.jsonl"
    other = isolated_logs / "notas.txt"
    for f in (old, recent, other):
        f.write_text("{}\n")
    monkeypatch.setenv("SYNAPSE_LOG_RETENTION_DAYS", "30")
    common.log_step("x")
    assert not old.exists() and recent.exists() and other.exists()


def test_retention_zero_keeps_everything(isolated_logs: Path, monkeypatch) -> None:
    isolated_logs.mkdir(parents=True, exist_ok=True)
    old = isolated_logs / f"{datetime.now() - timedelta(days=400):%Y-%m-%d}.jsonl"
    old.write_text("{}\n")
    monkeypatch.setenv("SYNAPSE_LOG_RETENTION_DAYS", "0")
    common.log_step("x")
    assert old.exists()


def test_emit_requires_attribution() -> None:
    with pytest.raises(TypeError):
        common.emit_block("motivo", hook_name="h", tool_name="Bash", target="x")  # type: ignore[call-arg]
    with pytest.raises(TypeError):
        common.emit_decision("ask", "motivo", hook_name="h", tool_name="Bash", target="x")  # type: ignore[call-arg]
