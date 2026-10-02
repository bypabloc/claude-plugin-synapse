"""E2E: ejecuta los hooks tal como los declara hooks/hooks.json, igual que Claude Code en PreToolUse.

Para cada escenario se filtran los grupos cuyo `matcher` coincide con la herramienta, se lanzan sus comandos por
shell con CLAUDE_PLUGIN_ROOT / CLAUDE_PLUGIN_DATA y se combinan los resultados con la precedencia de Claude Code:
exit 2 → deny, luego ask, luego allow, y sin salida → passthrough.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from support import PROJECT_DIR, HookResult, hook_env, make_git_repo, run_command

HOOKS_JSON = json.loads((PROJECT_DIR / "hooks" / "hooks.json").read_text(encoding="utf-8"))
FAKE_AWS_KEY = "AKIA" + "IOSFODNN7EXAMPLE"


def commands_for(tool: str) -> list[str]:
    return [
        h["command"]
        for group in HOOKS_JSON["hooks"]["PreToolUse"]
        if re.search(group["matcher"], tool)
        for h in group["hooks"]
    ]


def combined_decision(results: list[HookResult]) -> str:
    if any(r.exit_code == 2 for r in results):
        return "deny"
    decisions = {r.decision for r in results}
    for d in ("ask", "allow"):
        if d in decisions:
            return d
    return "passthrough"


@pytest.fixture(scope="module")
def repo() -> Path:
    return make_git_repo(
        "e2e",
        tracked={"src/app.py": "x = 1\n", ".gitignore": ".env\n"},
        untracked={"client/tests/tdd/chart.tdd.test.tsx": "it()\n", ".env": "API_SECRET=abc123\n"},
    )


@pytest.fixture
def claude(repo: Path, laya_flag: str, tmp_path: Path, monkeypatch):
    """Invoca el pipeline PreToolUse completo y devuelve (decisión combinada, resultados por hook)."""
    monkeypatch.delenv("SYNAPSE_LOG_DIR")
    claude_dir = tmp_path / "claude"
    env = hook_env(
        CLAUDE_PLUGIN_ROOT=str(PROJECT_DIR),
        CLAUDE_PLUGIN_DATA=str(tmp_path / "plugin_data"),
        CLAUDE_CONFIG_DIR=str(claude_dir),
    )
    env.pop("SYNAPSE_LOG_DIR", None)

    def invoke(tool: str, tool_input: dict) -> tuple[str, list[HookResult]]:
        payload = {
            "hook_event_name": "PreToolUse",
            "session_id": "e2e0session",
            "tool_name": tool,
            "tool_input": tool_input,
            "cwd": str(repo),
        }
        results = [run_command(f"{cmd} {laya_flag}", payload, env) for cmd in commands_for(tool)]
        return combined_decision(results), results

    invoke.log_dir = claude_dir / "logs" / "synapse"
    invoke.env = env
    return invoke


SCENARIOS = [
    pytest.param("Bash", {"command": "mkdir -p client/tests/tdd && cat > client/tests/tdd/x.test.tsx <<'EOF'\nit()\nEOF"}, "passthrough", id="bash_heredoc_crea_test"),
    pytest.param("Bash", {"command": "rm -f client/tests/tdd/chart.tdd.test.tsx && grep -n x src/app.py"}, "allow", id="bash_rm_archivo_sin_seguimiento"),
    pytest.param("Bash", {"command": "rm -f src/app.py"}, "ask", id="bash_rm_archivo_rastreado"),
    pytest.param("Bash", {"command": "rm -rf /"}, "deny", id="bash_rm_raiz"),
    pytest.param("Bash", {"command": "git push origin --force main"}, "deny", id="bash_force_push_main"),
    pytest.param("Bash", {"command": "cat .env"}, "deny", id="bash_cat_env"),
    pytest.param("Bash", {"command": "pytest -q"}, "passthrough", id="bash_comando_seguro"),
    pytest.param("Bash", {"command": "rm -rf .git"}, "deny", id="bash_rm_git_dir"),
    pytest.param("Bash", {"command": "echo x > .git/HEAD"}, "deny", id="bash_redireccion_git_dir"),
    pytest.param("Bash", {"command": "cd .. && rm -rf otro-repo"}, "deny", id="bash_rm_fuera_del_proyecto"),
    pytest.param("Bash", {"command": "echo x >> ~/.zshrc"}, "deny", id="bash_escritura_fuera_del_proyecto"),
    pytest.param("Bash", {"command": "GH_TOKEN=$(gh auth token --user bypabloc) gh pr create --base dev --title 'feat: x' --body-file tmp/pr/48.md"}, "passthrough", id="bash_gh_pr_create"),
    pytest.param("Bash", {"command": "git reset --hard HEAD~3"}, "ask", id="bash_reset_hard"),
    pytest.param("Read", {"file_path": ".env"}, "deny", id="read_env"),
    pytest.param("Read", {"file_path": "src/app.py"}, "passthrough", id="read_fuente"),
    pytest.param("Write", {"file_path": ".git/config", "content": "x"}, "deny", id="write_git_dir"),
    pytest.param("Write", {"file_path": "src/cfg.py", "content": f"AWS_KEY = '{FAKE_AWS_KEY}'\n"}, "deny", id="write_con_secreto"),
    pytest.param("Edit", {"file_path": ".claude/settings.json", "old_string": "a", "new_string": "b"}, "ask", id="edit_settings_claude"),
    pytest.param("Write", {"file_path": "src/util.py", "content": "def f():\n    return 1\n"}, "passthrough", id="write_fuente_normal"),
]


@pytest.mark.parametrize(("tool", "tool_input", "expected"), SCENARIOS)
def test_pretooluse_pipeline(claude, tool: str, tool_input: dict, expected: str) -> None:
    decision, results = claude(tool, tool_input)
    assert decision == expected, [(r.exit_code, r.stdout, r.stderr[-300:]) for r in results]


def test_trace_lands_in_claude_config_dir_one_run_per_hook(claude) -> None:
    claude("Bash", {"command": "rm -rf /"})
    files = list(claude.log_dir.glob("*.jsonl"))
    assert len(files) == 1
    records = [json.loads(line) for line in files[0].read_text(encoding="utf-8").splitlines()]
    runs = {(r["run"], r["hook"]) for r in records}
    assert {hook for _, hook in runs} == {"block_dangerous", "block_env_read"}
    assert len(runs) == 2
    final = [r for r in records if r["hook"] == "block_dangerous" and r["step"] == "decision"]
    assert final[0]["decision"] == "block" and final[0]["session"] == "e2e0sess"


def test_log_viewer_renders_runs(claude) -> None:
    claude("Bash", {"command": "rm -rf .git"})
    claude("Bash", {"command": "pytest -q"})
    out = run_command(
        f"python3 {PROJECT_DIR / 'scripts' / 'synapse_log.py'} --decision block --hook block_dangerous", {}, claude.env
    ).stdout
    assert "━━" in out and "structure.git_dir" in out and "DECISION  BLOCK" in out
    assert "pytest -q" not in out
