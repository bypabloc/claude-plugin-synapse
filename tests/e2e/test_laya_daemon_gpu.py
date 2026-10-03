"""E2E con el modelo real (pytest --laya gpu|cpu): latencia de los hooks con el daemon de Laya.

Replica la traza del 2026-10-03: cada tool_use dispara 2 hooks y varias sesiones corren a la vez. Antes cada
hook cargaba los 3 checkpoints (~7 s, 4.7 GB de VRAM por proceso) y con 4-8 simultáneos se llegaba a 112 s.
"""

from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

import laya_daemon
from support import PROJECT_DIR, hook_env, run_command

HOOKS = PROJECT_DIR / "hooks"
WARM_LAYA_MS = 1500  # inferencia en caliente ~30 ms; margen para la cola de 8 peticiones serializadas
WARM_HOOK_SECONDS = 6.0  # arranque de python + reglas deterministas + git + Laya


@pytest.fixture(scope="module")
def mode(laya_flag: str) -> str:
    """El runtime aislado del daemon y su apagado al final los pone conftest.engine_mode."""
    if laya_flag == "--fallback":
        pytest.skip("requiere --laya gpu o --laya cpu")
    return "cpu" if laya_flag == "--cpu" else "gpu"


@pytest.fixture
def run_hook(mode, laya_flag: str, tmp_path: Path):
    claude_dir = tmp_path / "claude"
    env = hook_env(CLAUDE_CONFIG_DIR=str(claude_dir), CLAUDE_PROJECT_DIR=str(PROJECT_DIR))
    env.pop("SYNAPSE_LOG_DIR", None)

    def invoke(hook: str, tool: str, tool_input: dict) -> float:
        payload = {"session_id": "gpu0e2e0", "tool_name": tool, "tool_input": tool_input, "cwd": str(PROJECT_DIR)}
        started = time.monotonic()
        run_command(f"python3 {HOOKS / f'{hook}.py'} {laya_flag}", payload, env, timeout=120)
        return time.monotonic() - started

    def records() -> list[dict]:
        return [json.loads(line) for f in (claude_dir / "logs" / "synapse").glob("*.jsonl") for line in f.read_text().splitlines()]

    invoke.records = records
    return invoke


def laya_steps(records: list[dict]) -> list[dict]:
    return [r for r in records if r["step"] == "laya"]


def test_cold_start_then_concurrent_burst(run_hook) -> None:
    cold = run_hook("block_dangerous", "Bash", {"command": "python3 scripts/migrate.py --cold"})
    assert cold < 60, f"arranque en frío: {cold:.1f} s"
    first = laya_steps(run_hook.records())
    assert first and first[0]["result"] in {"ok", "flagged"}, first

    commands = [f"python3 scripts/job_{i}.py --dry-run" for i in range(8)]
    with ThreadPoolExecutor(max_workers=8) as pool:
        walls = list(pool.map(lambda c: run_hook("block_dangerous", "Bash", {"command": c}), commands))
    steps = laya_steps(run_hook.records())[1:]
    assert len(steps) == 8 and all(s["result"] in {"ok", "flagged"} for s in steps), steps
    assert max(s["laya_ms"] for s in steps) < WARM_LAYA_MS, [s["laya_ms"] for s in steps]
    assert max(walls) < WARM_HOOK_SECONDS, walls


def test_all_hook_types_share_one_daemon(run_hook, mode) -> None:
    calls = [
        ("block_dangerous", "Bash", {"command": "python3 tools/report.py"}),
        ("block_env_read", "Bash", {"command": "python3 tools/show_config.py settings.env.local"}),
        ("protect_files", "Write", {"file_path": "src/config/database.py", "content": "x"}),
        ("detect_secrets", "Write", {"file_path": "a.py", "content": "auth_token = load_token_from(vault)"}),
    ] * 3
    with ThreadPoolExecutor(max_workers=8) as pool:
        walls = list(pool.map(lambda c: run_hook(*c), calls))
    assert max(walls) < WARM_HOOK_SECONDS, walls
    errors = [r for r in run_hook.records() if r["step"] == "laya" and r.get("result") == "error"]
    assert not errors, errors
    pid = laya_daemon.send(laya_daemon.socket_path(mode), {"op": "ping"})["pid"]
    run_hook("block_dangerous", "Bash", {"command": "python3 tools/again.py"})
    assert laya_daemon.send(laya_daemon.socket_path(mode), {"op": "ping"})["pid"] == pid


def test_finetuned_delta_does_not_leak_to_other_hooks(mode) -> None:
    import block_dangerous
    import protect_files

    base = {"op": "predict", "state": "src/config/database.py", "questions": protect_files.FILE_SENSITIVITY_QUESTIONS, "model": "english"}
    before = laya_daemon.request(base, mode)["result"]["answers"]
    delta = str(block_dangerous.delta_path(block_dangerous.load_laya_policy()))
    tuned = laya_daemon.request({**base, "delta": delta}, mode)["result"]["answers"]
    after = laya_daemon.request(base, mode)["result"]["answers"]
    assert before == after
    assert tuned != before  # el delta sí cambia la salida mientras está aplicado
