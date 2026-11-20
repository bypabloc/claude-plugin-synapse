"""No regresión en Claude Code (spec 001 · 02 §14, T-20): la salida de cada caso es idéntica a la de 1.6.1.

El golden se capturó antes de tocar el código multi-agente. Para regenerarlo (solo en un commit que lo justifique):
  SYNAPSE_GOLDEN_WRITE=1 python3 -m pytest tests/unit/test_golden_claude.py
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from support import GIT_ROOT, PROJECT_DIR, load_cases, materialize_setup_file, run_in_process

HOOKS = ("block_dangerous", "block_env_read", "detect_secrets", "protect_files")
GOLDEN = Path(__file__).resolve().parents[1] / "golden" / "claude-1.6.1.json"
CASES = [(hook, case) for hook in HOOKS for case in load_cases(hook)]
_written: dict[str, dict[str, object]] = {}


def _normalize(text: str) -> str:
    for real, marker in ((str(PROJECT_DIR), "{project}"), (GIT_ROOT, "{gitroot}"), (str(Path.home()), "{home}")):
        text = text.replace(real, marker)
    return text


@pytest.mark.parametrize("hook,case", CASES, ids=[f"{h}/{c['name']}" for h, c in CASES])
def test_output_matches_golden(hook: str, case: dict) -> None:
    setup = materialize_setup_file(case)
    try:
        result = run_in_process(hook, case["payload"])
    finally:
        if setup:
            setup.unlink(missing_ok=True)
    got = {"exit": result.exit_code, "stdout": _normalize(result.stdout), "stderr": _normalize(result.stderr)}
    key = f"{hook}/{case['name']}"
    if os.environ.get("SYNAPSE_GOLDEN_WRITE") == "1":
        _written[key] = got
        GOLDEN.parent.mkdir(parents=True, exist_ok=True)
        current = json.loads(GOLDEN.read_text(encoding="utf-8")) if GOLDEN.exists() else {}
        current.update(_written)
        GOLDEN.write_text(json.dumps(dict(sorted(current.items())), indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
        return
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
    assert key in golden, f"caso nuevo sin golden: {key} (regenera el golden en un commit propio)"
    assert got == golden[key]
