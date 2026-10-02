#!/usr/bin/env python3
"""Regresiones de block_dangerous detectadas en el log de auditoría real.

  1. Laya bloqueaba `mkdir && cat > ... <<EOF` aunque lo clasificaba como safe_operation.
  2. `rm -f` sobre un archivo sin seguimiento git (recién creado) pedía confirmación.
"""

from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

PROJECT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
HOOKS_DIR = os.path.join(PROJECT_DIR, "hooks")
if HOOKS_DIR not in sys.path:
    sys.path.insert(0, HOOKS_DIR)

import block_dangerous


def _laya_answers(danger_type: str, danger_conf: float, is_cat: str, cat_conf: float) -> dict:
    return {
        "answers": {
            "danger_type": {"choice": danger_type, "answer_confidence": danger_conf},
            "is_catastrophic": {"choice": is_cat, "answer_confidence": cat_conf},
        }
    }


def _run(command: str, cwd: str) -> tuple[int, str]:
    out, err = io.StringIO(), io.StringIO()
    code = 0
    with redirect_stdout(out), redirect_stderr(err):
        try:
            block_dangerous.evaluate_command_safety(command, cwd)
        except SystemExit as exc:
            code = exc.code if isinstance(exc.code, int) else 0
    decision = None
    if out.getvalue():
        decision = json.loads(out.getvalue())["hookSpecificOutput"]["permissionDecision"]
    return code, decision


class TestDangerousRules(unittest.TestCase):
    def setUp(self) -> None:
        self.log_dir = os.path.join(PROJECT_DIR, "tmp", "test_dangerous_rules_logs")
        os.makedirs(self.log_dir, exist_ok=True)
        os.environ["SYNAPSE_LOG_DIR"] = self.log_dir

        # Fuera de tmp/: cualquier segmento /tmp/ vuelve desechable toda la ruta
        self.repo = os.path.join(PROJECT_DIR, ".test_dangerous_repo")
        shutil.rmtree(self.repo, ignore_errors=True)
        os.makedirs(os.path.join(self.repo, "src"))
        subprocess.run(["git", "init", "-q", self.repo], check=True)
        Path(self.repo, "src", "tracked.py").write_text("x = 1\n")
        subprocess.run(["git", "-C", self.repo, "add", "src/tracked.py"], check=True)
        Path(self.repo, "src", "new.test.tsx").write_text("it()\n")
        os.makedirs(os.path.join(self.repo, "untracked_dir"))
        Path(self.repo, "untracked_dir", "a.ts").write_text("a\n")

        self.no_laya = mock.patch.object(block_dangerous, "should_use_laya", return_value=False)
        self.no_laya.start()

    def tearDown(self) -> None:
        mock.patch.stopall()
        shutil.rmtree(self.repo, ignore_errors=True)
        os.environ.pop("SYNAPSE_LOG_DIR", None)

    def _with_laya(self, answers: dict) -> None:
        self.no_laya.stop()
        router = mock.Mock()
        router.predict.return_value = answers
        mock.patch.object(block_dangerous, "should_use_laya", return_value=True).start()
        mock.patch.object(block_dangerous, "get_laya_router", return_value=router).start()

    def test_laya_safe_operation_is_not_blocked(self) -> None:
        self._with_laya(_laya_answers("safe_operation", 0.9, "yes", 0.7))
        code, _ = _run("mkdir -p a && cat > a/x.test.tsx <<'EOF'\nit()\nEOF", self.repo)
        self.assertEqual(code, 0)

    def test_laya_catastrophic_is_still_blocked(self) -> None:
        self._with_laya(_laya_answers("catastrophic", 0.9, "yes", 0.9))
        code, _ = _run("shred -u important.db", self.repo)
        self.assertEqual(code, 2)

    def test_laya_is_catastrophic_yes_blocks_with_other_danger_type(self) -> None:
        self._with_laya(_laya_answers("sensitive_mutation", 0.6, "yes", 0.8))
        code, _ = _run("shred -u important.db", self.repo)
        self.assertEqual(code, 2)

    def test_rm_untracked_file_is_auto_allowed(self) -> None:
        code, decision = _run("rm -f src/new.test.tsx", self.repo)
        self.assertEqual((code, decision), (0, "allow"))

    def test_rm_tracked_file_still_asks(self) -> None:
        code, decision = _run("rm -f src/tracked.py", self.repo)
        self.assertEqual((code, decision), (0, "ask"))

    def test_rm_untracked_directory_still_asks(self) -> None:
        code, decision = _run("rm -rf untracked_dir", self.repo)
        self.assertEqual((code, decision), (0, "ask"))

    def test_rm_missing_file_still_asks(self) -> None:
        code, decision = _run("rm -f src/ghost.py", self.repo)
        self.assertEqual((code, decision), (0, "ask"))


if __name__ == "__main__":
    unittest.main()
