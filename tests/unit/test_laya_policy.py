"""Política común: Laya System 1 nunca bloquea (exit 2), a lo sumo pide confirmación ('ask')."""

import importlib
from unittest import mock

import pytest

from support import run_in_process

FLAGGING_ANSWERS = {
    "detect_secrets": {
        "secret_status": {"choice": "confidential_secret", "answer_confidence": 0.99},
        "secret_type": {"choice": "password_or_connection", "answer_confidence": 0.99},
    },
    "protect_files": {"file_sensitivity": {"choice": "critical_blocked", "answer_confidence": 0.99}},
    "block_env_read": {"env_intent": {"choice": "read_or_exfiltrate", "answer_confidence": 0.99}},
    "block_dangerous": {
        "danger_type": {"choice": "catastrophic", "answer_confidence": 0.99},
        "is_catastrophic": {"choice": "yes", "answer_confidence": 0.99},
    },
}

PAYLOADS = {
    "detect_secrets": {"tool_name": "Edit", "tool_input": {"file_path": "a.py", "new_string": "auth = build_auth(user_token_value)"}},
    "protect_files": {"tool_name": "Write", "tool_input": {"file_path": "src/config.py", "content": "x"}},
    "block_env_read": {"tool_name": "Bash", "tool_input": {"command": "python3 tools/show.py .env.local"}},
    "block_dangerous": {"tool_name": "Bash", "tool_input": {"command": "python3 scripts/migrate.py"}},
}


@pytest.mark.parametrize("hook", sorted(FLAGGING_ANSWERS))
def test_laya_flag_asks_instead_of_blocking(hook: str, tmp_path) -> None:
    mod = importlib.import_module(hook)
    router = mock.Mock()
    router.predict.return_value = {"answers": FLAGGING_ANSWERS[hook]}
    payload = {**PAYLOADS[hook], "cwd": str(tmp_path)}
    with mock.patch.object(mod, "should_use_laya", return_value=True), \
         mock.patch.object(mod, "get_laya_router", return_value=router):
        result = run_in_process(hook, payload)
    router.predict.assert_called_once()
    assert (result.exit_code, result.decision) == (0, "ask"), result.stderr
