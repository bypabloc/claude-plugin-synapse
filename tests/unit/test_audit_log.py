"""Traza JSONL: destino, un paso por línea agrupado por run, y nada de secretos en el log."""

import json
from pathlib import Path

import pytest

import common
from support import run_in_process


def _records(log_dir: Path) -> list[dict]:
    return [json.loads(line) for line in common.get_log_file().read_text(encoding="utf-8").splitlines()]


def test_log_dir_defaults_to_claude_config_dir(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv("SYNAPSE_LOG_DIR")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
    assert common.get_log_dir() == tmp_path / "claude" / "logs" / "synapse"


def test_log_dir_falls_back_to_home_claude(monkeypatch) -> None:
    monkeypatch.delenv("SYNAPSE_LOG_DIR")
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    assert common.get_log_dir() == Path.home() / ".claude" / "logs" / "synapse"


def test_daily_file_name(isolated_logs: Path) -> None:
    assert common.get_log_file().parent == isolated_logs
    assert common.get_log_file().suffix == ".jsonl"


def test_every_step_of_a_run_is_logged(isolated_logs: Path) -> None:
    run_in_process("block_dangerous", {"tool_name": "Bash", "tool_input": {"command": "rm -rf .git"}, "cwd": "/x", "session_id": "abcd1234ffff"})
    records = _records(isolated_logs)
    assert {r["run"] for r in records} == {records[0]["run"]}
    steps = [r["step"] for r in records]
    assert steps[0] == "input" and steps[-1] == "decision"
    assert "structure.git_dir" in steps
    assert records[0]["session"] == "abcd1234" and records[0]["command"] == "rm -rf .git"
    assert records[-1]["decision"] == "block" and "ms" in records[-1]


def test_separate_runs_get_separate_ids(isolated_logs: Path) -> None:
    for _ in range(2):
        run_in_process("block_dangerous", {"tool_name": "Bash", "tool_input": {"command": "ls"}, "cwd": "/x"})
    assert len({r["run"] for r in _records(isolated_logs)}) == 2


def test_write_content_is_never_logged(isolated_logs: Path) -> None:
    secret = "AKIA" + "IOSFODNN7EXAMPLE"
    run_in_process("detect_secrets", {"tool_name": "Write", "tool_input": {"file_path": "a.py", "content": f"k = '{secret}'"}})
    raw = common.get_log_file().read_text(encoding="utf-8")
    assert secret not in raw and "AKIA" not in raw
    assert json.loads(raw.splitlines()[0])["content_chars"] == len(f"k = '{secret}'")


def test_tokens_in_commands_are_redacted(isolated_logs: Path) -> None:
    token = "ghp_" + "a" * 36
    run_in_process("block_dangerous", {"tool_name": "Bash", "tool_input": {"command": f"GH_TOKEN={token} gh pr list"}, "cwd": "/x"})
    raw = common.get_log_file().read_text(encoding="utf-8")
    assert token not in raw and "REDACTED" in raw


@pytest.mark.parametrize(
    "text",
    [
        "git push origin feat/x",
        "GH_TOKEN=$(gh auth token --user bypabloc) gh pr create",
        "Possible secret detected",
        "export TOKEN_TTL",
    ],
)
def test_redact_keeps_non_secrets(text: str) -> None:
    assert common.redact(text) == text


@pytest.mark.parametrize(
    ("text", "secret"),
    [
        ("mysql -u root --password=hunter22", "hunter22"),
        ("mysql --password hunter22", "hunter22"),
        ("export API_KEY='abcd1234efgh'", "abcd1234efgh"),
        ("curl -H 'Authorization: Bearer abc.def.ghi123'", "abc.def.ghi123"),
    ],
)
def test_redact_hides_secrets(text: str, secret: str) -> None:
    assert secret not in common.redact(text)
