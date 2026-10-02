"""block_dangerous.py: casos de tests/cases/block_dangerous.json y política de Laya (solo escala a 'ask')."""

from unittest import mock

import pytest

import block_dangerous
from support import assert_case, load_cases, run_in_process

CASES = load_cases("block_dangerous")


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_case(case: dict) -> None:
    assert_case(run_in_process("block_dangerous", case["payload"]), case)


def _answers(danger_type: str, danger_conf: float, is_cat: str, cat_conf: float) -> dict:
    return {
        "answers": {
            "danger_type": {"choice": danger_type, "answer_confidence": danger_conf},
            "is_catastrophic": {"choice": is_cat, "answer_confidence": cat_conf},
        }
    }


@pytest.fixture
def laya():
    router = mock.Mock()
    with mock.patch.object(block_dangerous, "should_use_laya", return_value=True), \
         mock.patch.object(block_dangerous, "get_laya_router", return_value=router):
        yield router


def _run(command: str, cwd) -> tuple[int, str | None]:
    result = run_in_process("block_dangerous", {"tool_name": "Bash", "tool_input": {"command": command}, "cwd": str(cwd)})
    return result.exit_code, result.decision


@pytest.mark.parametrize(
    ("answers", "expected_decision"),
    [
        pytest.param(("safe_operation", 0.9, "yes", 0.7), None, id="safe_operation_contradice_is_catastrophic"),
        pytest.param(("catastrophic", 0.9, "yes", 0.9), "ask", id="catastrophic_pide_confirmacion"),
        pytest.param(("catastrophic", 0.55, "no", 0.9), "ask", id="danger_type_catastrophic_basta"),
        pytest.param(("sensitive_mutation", 0.6, "yes", 0.8), "ask", id="is_catastrophic_con_otro_tipo"),
        pytest.param(("sensitive_mutation", 0.6, "yes", 0.5), None, id="baja_confianza_pasa"),
    ],
)
def test_laya_never_blocks(laya, tmp_path, answers, expected_decision) -> None:
    laya.predict.return_value = _answers(*answers)
    assert _run("python3 scripts/migrate.py", tmp_path) == (0, expected_decision)


def test_laya_receives_command_without_comments_or_heredoc_body(laya, tmp_path) -> None:
    laya.predict.return_value = _answers("safe_operation", 0.9, "no", 0.9)
    _run("mkdir -p a && cat > a/x.sh <<'EOF'\necho cuerpo-heredoc\nEOF\npython3 x.py  # limpieza segura", tmp_path)
    sent = laya.predict.call_args.args[0]
    assert "limpieza" not in sent and "cuerpo-heredoc" not in sent
    assert "python3 x.py" in sent


@pytest.mark.parametrize(
    "command",
    [
        "gh pr create --repo bypabloc/optical-soft --base dev --head feat/x --title 'feat: x' --body-file tmp/pr/48.md",
        "GH_TOKEN=abc gh pr merge 71 --squash",
        "GH_TOKEN=$(gh auth token --user bypabloc) gh pr create --repo bypabloc/optical-soft --base dev "
        "--head feat/spec-48-otorrinolaringologia --title 'feat(patients): x' --body-file tmp/pr/48.md",
        "git add -A && git commit -m 'feat: x' && git push origin feat/x",
    ],
)
def test_routine_commands_skip_laya(laya, tmp_path, command: str) -> None:
    laya.predict.return_value = _answers("catastrophic", 0.99, "yes", 0.99)
    assert _run(command, tmp_path) == (0, None)
    laya.predict.assert_not_called()


def test_routine_does_not_cover_piped_commands(laya, tmp_path) -> None:
    laya.predict.return_value = _answers("catastrophic", 0.99, "yes", 0.99)
    assert _run("gh pr list | xargs -n1 python3 run.py", tmp_path) == (0, "ask")
