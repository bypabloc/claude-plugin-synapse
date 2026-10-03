"""Utilidades compartidas: ejecución de hooks (in-process y subproceso), carga de casos y repos git efímeros."""

from __future__ import annotations

import importlib
import io
import json
import os
import shutil
import subprocess
import sys
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
HOOKS_DIR = PROJECT_DIR / "hooks"
CASES_DIR = Path(__file__).resolve().parent / "cases"
# Fuera de tmp/: cualquier segmento /tmp/ en la ruta vuelve desechable todo su contenido
GIT_REPOS_DIR = PROJECT_DIR / ".test_repos"

# Fragmentos que no pueden vivir literales en el repo (GitHub push protection)
PLACEHOLDERS = {"{stripe_live_prefix}": "sk_" + "live_"}


@dataclass
class HookResult:
    exit_code: int
    stdout: str
    stderr: str

    @property
    def decision(self) -> str | None:
        if not self.stdout.strip():
            return None
        return json.loads(self.stdout).get("hookSpecificOutput", {}).get("permissionDecision")


def _expand(value, project: str):
    if isinstance(value, str):
        value = value.replace("{project}", project)
        for key, real in PLACEHOLDERS.items():
            value = value.replace(key, real)
        return value
    if isinstance(value, dict):
        return {k: _expand(v, project) for k, v in value.items()}
    if isinstance(value, list):
        return [_expand(v, project) for v in value]
    return value


def load_cases(hook: str, project: Path = PROJECT_DIR) -> list[dict]:
    raw = json.loads((CASES_DIR / f"{hook}.json").read_text(encoding="utf-8"))
    return [_expand(case, str(project)) for case in raw]


def run_in_process(hook: str, payload: dict) -> HookResult:
    """Ejecuta main() del hook capturando stdout/stderr y SystemExit (rápido, comparte el router Laya)."""
    if str(HOOKS_DIR) not in sys.path:
        sys.path.insert(0, str(HOOKS_DIR))
    mod = importlib.import_module(hook)
    common = importlib.import_module("common")
    out, err = io.StringIO(), io.StringIO()
    old_stdin = sys.stdin
    code = 0
    try:
        sys.stdin = io.StringIO(json.dumps(payload))
        with redirect_stdout(out), redirect_stderr(err):
            try:
                common.run_main(mod.main)  # igual que `if __name__ == "__main__"` en cada hook
            except SystemExit as exc:
                code = exc.code if isinstance(exc.code, int) else (0 if exc.code is None else 1)
    finally:
        sys.stdin = old_stdin
    return HookResult(code, out.getvalue(), err.getvalue())


def run_command(command: str, payload: dict, env: dict[str, str], timeout: int = 120) -> HookResult:
    """Ejecuta un comando de hook por shell, igual que Claude Code, enviando el payload por stdin."""
    proc = subprocess.run(
        command,
        shell=True,
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        env=env,
        timeout=timeout,
    )
    return HookResult(proc.returncode, proc.stdout, proc.stderr)


def assert_case(result: HookResult, case: dict) -> None:
    assert result.exit_code == case["expected_exit"], result.stderr
    if "expected_decision" in case:
        assert result.decision == case["expected_decision"], result.stdout
    if "expected_in_stderr" in case:
        assert case["expected_in_stderr"].lower() in result.stderr.lower(), result.stderr


def make_git_repo(name: str, tracked: dict[str, str], untracked: dict[str, str]) -> Path:
    repo = GIT_REPOS_DIR / name
    shutil.rmtree(repo, ignore_errors=True)
    repo.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    for files, add in ((tracked, True), (untracked, False)):
        for rel, content in files.items():
            path = repo / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
            if add:
                subprocess.run(["git", "-C", str(repo), "add", rel], check=True)
    return repo


def materialize_setup_file(case: dict, root: Path = PROJECT_DIR) -> Path | None:
    setup = case.get("setup_file")
    if not setup:
        return None
    path = root / setup["path"]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(setup["content"])
    return path


def hook_env(**extra: str) -> dict[str, str]:
    return {**os.environ, **extra}
