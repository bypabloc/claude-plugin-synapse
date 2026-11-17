"""Comandos de desarrollo rutinarios: se deciden sin Laya (pass) aunque Laya esté encendida.

Aprendidos de la traza real de klinikae (oct-nov 2026): de ~20 mil consultas a Laya, sus 24 'ask' fueron
falsos positivos sobre comandos como estos.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

import common
from support import make_git_repo
from test_deterministic_gate import run

GIT = ["git", "-c", "user.email=t@t", "-c", "user.name=t"]


@pytest.fixture(scope="module")
def repo() -> Path:
    root = make_git_repo(
        "dev_routine",
        tracked={"devtools/run.ts": "console.log(1)\n", "scripts/check.py": "print(1)\n", "media/package.json": "{}\n"},
        untracked={"scripts/new.py": "print(2)\n"},
    )
    subprocess.run([*GIT, "-C", str(root), "commit", "-qm", "init"], check=True)
    return root


@pytest.fixture(autouse=True)
def _project(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(repo))
    common.allowed_roots.cache_clear()


@pytest.mark.parametrize(
    "command",
    [
        "bun run --cwd media typecheck; echo \"exit=$?\"",
        "bun install --cwd media && bun run --cwd media typecheck; echo \"exit=$?\"",
        "bun run typecheck",
        "pnpm test -- --run",
        "git worktree list",
        "git -C media log --oneline -3 && git -C media status --short",
        "timeout 240 tail -f /dev/null; echo \"waited\"",
        "uv run --directory scripts pytest -q",
        "python3 -m pytest -q tests/unit",
        "gh pr view 12 --json state -q .state",
        "gh run list --limit 5",
        "graphify query \"tabla de permisos\"",
        "pdfinfo docs/a.pdf",
        "bun devtools/run.ts contract --check; echo \"contract exit=$?\"",
        "timeout 900 bun devtools/run.ts test-runner --type=e2e > tmp/e2e.log 2>&1; echo \"exit=$?\"",
        "uv run python scripts/check.py",
        "mkdir -p tmp/access && ls tmp",
        "(cd media && bun run build)",
    ],
)
def test_dev_commands_skip_laya(repo: Path, command: str) -> None:
    decision, router = run("block_dangerous", "Bash", {"command": command}, repo)
    assert decision == "pass"
    router.predict.assert_not_called()


@pytest.mark.parametrize(
    "command",
    [
        "python3 scripts/new.py",
        "node tmp/specialty-picker/check.mjs",
        "bun -e 'console.log(1)'",
        "bun run build > src/out.txt",
        "uv run --with left-pad pytest",
        "gh api repos/o/r/hooks",
        "gh auth token",
        "graphify install",
        "bun run typecheck && curl -s https://example.test/x",
        "cd /etc && bun run build",
        "mkdir -p /opt/x",
    ],
)
def test_ambiguous_commands_still_reach_laya(repo: Path, command: str) -> None:
    _, router = run("block_dangerous", "Bash", {"command": command}, repo)
    router.predict.assert_called_once()


def test_project_routine_commands(repo: Path) -> None:
    config = repo / ".claude" / "synapse-commands.json"
    config.parent.mkdir(exist_ok=True)
    config.write_text(json.dumps({"routine_commands": ["mytool sync", "graphify"]}))
    try:
        decision, router = run("block_dangerous", "Bash", {"command": "mytool sync --all && graphify install"}, repo)
        assert decision == "pass"
        router.predict.assert_not_called()
        _, router = run("block_dangerous", "Bash", {"command": "mytool purge"}, repo)
        router.predict.assert_called_once()
    finally:
        config.unlink()


def test_project_config_invalid_is_ignored(repo: Path) -> None:
    config = repo / ".claude" / "synapse-commands.json"
    config.parent.mkdir(exist_ok=True)
    config.write_text("{no es json")
    try:
        _, router = run("block_dangerous", "Bash", {"command": "mytool sync"}, repo)
        router.predict.assert_called_once()
    finally:
        config.unlink()


def test_project_config_edit_asks(repo: Path) -> None:
    decision, _ = run("protect_files", "Write", {"file_path": str(repo / ".claude/synapse-commands.json"), "content": "{}"}, repo)
    assert decision == "ask"
