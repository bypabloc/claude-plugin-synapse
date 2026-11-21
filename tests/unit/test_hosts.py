"""Núcleo multi-agente (spec 001 · 02, 04 §3–§5): normalización con fixtures reales, matriz de equivalencia por
host, contratos de emisión estrictos, patch.py, escrituras de PowerShell y aprobación de un solo uso (Codex)."""

from __future__ import annotations

import io
import json
import os
from pathlib import Path

import pytest

import approvals
import dispatch
import hosts
import patch
from support import PLACEHOLDERS, PROJECT_DIR, load_cases, materialize_setup_file

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
GOLDEN = json.loads((Path(__file__).resolve().parents[1] / "golden" / "claude-1.6.1.json").read_text(encoding="utf-8"))
HOOKS = ("block_dangerous", "block_env_read", "detect_secrets", "protect_files")

# Contratos estrictos por host (04 §5): cualquier otra clave hace fallar el test (H-01)
CODEX_PRE_KEYS = {"hookEventName", "permissionDecision", "permissionDecisionReason", "additionalContext"}
ANTIGRAVITY_KEYS = {"decision", "reason"}
ANTIGRAVITY_DECISIONS = {"deny", "force_ask", "auto_approve", "allow"}
OPENCODE_KEYS = {"verdict", "reason", "hook", "rule", "target"}


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    monkeypatch.setenv("SYNAPSE_STATE_DIR", str(tmp_path / "state"))
    yield
    hosts.set_context("claude", None)


def contract(host: str, out: str) -> dict:
    if host == "codex":
        if not out:
            return {}
        data = json.loads(out)
        assert set(data) == {"hookSpecificOutput"}
        assert set(data["hookSpecificOutput"]) <= CODEX_PRE_KEYS
        assert data["hookSpecificOutput"].get("permissionDecision", "deny") == "deny"
        return data
    data = json.loads(out)
    if host == "antigravity":
        assert set(data) <= ANTIGRAVITY_KEYS and data["decision"] in ANTIGRAVITY_DECISIONS
    else:
        assert set(data) == OPENCODE_KEYS
    return data


# --- Fixtures reales ---------------------------------------------------------------------------------------

def fixture_files(pattern: str) -> list[Path]:
    return sorted(FIXTURES.glob(pattern))


EXPECTED_KIND = {
    "Bash": "shell", "apply_patch": "write", "run_command": "shell", "read_file": "read", "write_file": "write",
    "edit_file": "write", "ask_question": "other", "request_user_input_async": "other",
    "shell": "shell", "read": "read", "write": "write", "edit": "write", "question": "other",
}


@pytest.mark.parametrize("path", fixture_files("*/*/*/PreToolUse-*.json"), ids=lambda p: str(p.relative_to(FIXTURES)))
def test_subprocess_fixtures_normalize(path: Path) -> None:
    host = path.relative_to(FIXTURES).parts[0]
    raw = json.loads(path.read_text(encoding="utf-8"))["stdin"]
    event = hosts.normalize(host, raw)
    assert event.kind == EXPECTED_KIND[event.tool]
    assert event.session and event.cwd
    if event.kind == "shell":
        assert event.command
    if event.kind == "write":
        assert event.files and all(f.path for f in event.files)


@pytest.mark.parametrize("path", fixture_files("opencode/*/*/tool-before-*.json"), ids=lambda p: str(p.relative_to(FIXTURES)))
def test_opencode_fixtures_normalize(path: Path) -> None:
    ev = json.loads(path.read_text(encoding="utf-8"))["event"]
    payload = {"tool": ev["tool"], "input": ev["input"], "sessionID": ev["sessionID"], "callID": ev["id"],
               "directory": "/proyecto", "project": "/proyecto"}
    event = hosts.normalize("opencode", payload)
    assert event.kind == EXPECTED_KIND[event.tool] and event.cwd == "/proyecto"


# --- Matriz de equivalencia ----------------------------------------------------------------------------------

def claude_verdict(key: str) -> str:
    g = GOLDEN[key]
    if g["exit"] == 2:
        return "block"
    if g["stdout"]:
        return json.loads(g["stdout"])["hookSpecificOutput"]["permissionDecision"]
    return "pass"


def to_host(host: str, payload: dict) -> dict | None:
    tool, ti, cwd = payload.get("tool_name"), payload.get("tool_input", {}), payload.get("cwd", "")
    if tool == "Bash":
        command = ti.get("command", "")
        return {"codex": {"tool_name": "Bash", "tool_input": {"command": command}, "cwd": cwd, "session_id": "s",
                          "turn_id": "t", "tool_use_id": "u"},
                "antigravity": {"toolCall": {"name": "run_command", "args": {"CommandLine": command}, "id": "c"},
                                "cwd": cwd, "workspacePaths": [cwd], "conversationId": "s"},
                "opencode": {"tool": "shell", "input": {"command": command}, "sessionID": "s", "callID": "c",
                             "directory": cwd, "project": cwd}}[host]
    if tool == "Read":
        path = ti.get("file_path", "")
        return {"codex": None,
                "antigravity": {"toolCall": {"name": "read_file", "args": {"Path": path}, "id": "c"}, "cwd": cwd,
                                "workspacePaths": [cwd], "conversationId": "s"},
                "opencode": {"tool": "read", "input": {"path": path}, "sessionID": "s", "callID": "c",
                             "directory": cwd, "project": cwd}}[host]
    if tool in {"Write", "Edit"}:
        path = ti.get("file_path", "")
        new = ti.get("content", "") if tool == "Write" else ti.get("new_string", "")
        old = ti.get("old_string", "")
        if host == "codex":
            if not path:
                return None
            body = "\n".join("+" + line for line in new.split("\n"))
            text = (f"*** Begin Patch\n*** Add File: {path}\n{body}\n*** End Patch" if tool == "Write" else
                    "*** Begin Patch\n*** Update File: " + path + "\n" + "\n".join("-" + l for l in old.split("\n"))
                    + "\n" + body + "\n*** End Patch")
            return {"tool_name": "apply_patch", "tool_input": {"command": text}, "cwd": cwd, "session_id": "s",
                    "turn_id": "t", "tool_use_id": "u"}
        if host == "antigravity":
            args = {"Path": path, "Content": new} if tool == "Write" else {"Path": path, "Replacement": new, "Target": old}
            return {"toolCall": {"name": "write_file" if tool == "Write" else "edit_file", "args": args, "id": "c"},
                    "cwd": cwd, "workspacePaths": [cwd], "conversationId": "s"}
        ti2 = {"path": path, "content": new} if tool == "Write" else {"path": path, "newString": new, "oldString": old}
        return {"tool": tool.lower(), "input": ti2, "sessionID": "s", "callID": "c", "directory": cwd, "project": cwd}
    return None


# Diferencias legítimas con Claude Code: la memoria de Claude solo es proyecto en Claude Code
HOST_ONLY_CLAUDE = {"memory"}
EXPECTED_EMISSION = {
    "codex": {"block": "deny", "ask": "deny", "allow": "", "pass": ""},
    "antigravity": {"block": "deny", "ask": "force_ask", "allow": "auto_approve", "pass": "allow"},
    "opencode": {"block": "block", "ask": "ask", "allow": "allow", "pass": "pass"},
}
CASES = [(hook, case) for hook in HOOKS for case in load_cases(hook)]


def run_dispatch(host: str, payload: dict) -> tuple[int, str, str]:
    from support import _expand  # noqa: PLC0415

    return dispatch.run(host, json.dumps(_expand(payload, str(PROJECT_DIR))))


def emitted(host: str, out: str) -> str:
    data = contract(host, out)
    if host == "codex":
        return data.get("hookSpecificOutput", {}).get("permissionDecision", "") if data else ""
    return data["decision"] if host == "antigravity" else data["verdict"]


@pytest.mark.parametrize("host", ["codex", "antigravity", "opencode"])
@pytest.mark.parametrize("hook,case", CASES, ids=[f"{h}/{c['name']}" for h, c in CASES])
def test_equivalence(host: str, hook: str, case: dict) -> None:
    if any(word in case["name"] for word in HOST_ONLY_CLAUDE):
        pytest.skip("depende de la memoria de Claude Code")
    payload = to_host(host, case["payload"])
    if payload is None:
        pytest.skip(f"{host} no tiene esta tool")
    expected = claude_verdict(f"{hook}/{case['name']}")
    other = {"block_dangerous": "block_env_read", "block_env_read": "block_dangerous",
             "detect_secrets": "protect_files", "protect_files": "detect_secrets"}[hook]
    setup = materialize_setup_file(case)
    try:
        code, out, err = run_dispatch(host, payload)
    finally:
        if setup:
            setup.unlink(missing_ok=True)
    assert code == 0 and err == ""
    got = emitted(host, out)
    if got != EXPECTED_EMISSION[host][expected]:
        # dispatch corre también el otro hook del mismo tipo: solo puede endurecer (block/ask) la decisión
        assert hosts.RANK[{"deny": "block", "force_ask": "ask", "auto_approve": "allow", "allow": "pass",
                           "": "pass"}.get(got, got)] >= hosts.RANK[expected], f"{other} no debería relajar la decisión"


# --- Casos propios de cada host ------------------------------------------------------------------------------

def test_patch_parse_add_update_move_delete() -> None:
    text = ("*** Begin Patch\n*** Add File: a.py\n+x = 1\n*** Update File: b.py\n*** Move to: c.py\n@@ def f():\n-old\n+new\n"
            "*** Delete File: d.py\n*** End Patch")
    assert patch.parse(text) == [("a.py", "add", "x = 1", "", None), ("b.py", "update", "new", "old", "c.py"),
                                 ("d.py", "delete", "", "", None)]


def test_patch_without_begin_is_an_error() -> None:
    with pytest.raises(patch.PatchError):
        patch.parse("+solo una línea")


def codex_patch(text: str) -> dict:
    return {"tool_name": "apply_patch", "tool_input": {"command": text}, "cwd": str(PROJECT_DIR), "session_id": "s",
            "turn_id": "t", "tool_use_id": "u"}


def test_codex_multifile_patch_with_protected_file_is_denied() -> None:
    code, out, _ = run_dispatch("codex", codex_patch("*** Begin Patch\n*** Add File: ok.py\n+x = 1\n"
                                                     "*** Add File: .git/config\n+y\n*** End Patch"))
    assert emitted("codex", out) == "deny" and "path.protected" in out


def test_codex_patch_move_into_git_is_denied() -> None:
    code, out, _ = run_dispatch("codex", codex_patch("*** Begin Patch\n*** Update File: ok.py\n*** Move to: .git/hooks/x\n"
                                                     "+y\n*** End Patch"))
    assert emitted("codex", out) == "deny"


def test_codex_malformed_patch_is_scanned_for_secrets() -> None:
    secret = PLACEHOLDERS["{stripe_live_prefix}"] + "a1B2c3D4e5F6g7H8i9J0k1L2m3"
    code, out, _ = run_dispatch("codex", codex_patch(f"+key = '{secret}'"))
    assert emitted("codex", out) == "deny"


def test_antigravity_payload_without_toolcall_forces_ask() -> None:
    code, out, _ = dispatch.run("antigravity", json.dumps({"conversationId": "x"}))
    assert code == 0 and json.loads(out)["decision"] == "force_ask"


def test_antigravity_invalid_json_forces_ask() -> None:
    code, out, _ = dispatch.run("antigravity", "{no es json")
    assert json.loads(out)["decision"] == "force_ask"


def test_opencode_invalid_payload_asks() -> None:
    code, out, _ = dispatch.run("opencode", json.dumps({"x": 1}))
    assert json.loads(out)["verdict"] == "ask"


def test_dispatch_main_never_exits_nonzero_on_internal_error(monkeypatch, capsys) -> None:
    monkeypatch.setattr(dispatch, "run", lambda host, raw: 1 / 0)
    monkeypatch.setattr("sys.argv", ["dispatch.py", "--host", "antigravity"])
    monkeypatch.setattr("sys.stdin", io.StringIO("{}"))
    assert dispatch.main() == 0
    assert json.loads(capsys.readouterr().out)["decision"] == "force_ask"


def test_antigravity_documented_tool_names_are_accepted() -> None:
    raw = {"toolCall": {"name": "write_to_file", "args": {"TargetFile": "a.txt", "CodeContent": "x"}, "id": "c"},
           "cwd": "/p", "workspacePaths": ["/p"], "conversationId": "s"}
    event = hosts.normalize("antigravity", raw)
    assert event.kind == "write" and event.files[0].path == "a.txt" and event.files[0].new_text == "x"


def test_antigravity_multi_replace_chunks_are_conservative() -> None:
    raw = {"toolCall": {"name": "multi_replace_file_content", "args": {"TargetFile": "a.py", "ReplacementChunks": [
        {"ReplacementContent": "nuevo", "TargetContent": "viejo"}, "raro"]}, "id": "c"}, "workspacePaths": ["/p"]}
    event = hosts.normalize("antigravity", raw)
    assert "nuevo" in event.files[0].new_text and "raro" in event.files[0].new_text


def test_codex_shell_list_command_is_unwrapped() -> None:
    raw = {"tool_name": "Bash", "tool_input": {"command": ["bash", "-lc", "ls -la"]}, "cwd": "/p", "session_id": "s"}
    assert hosts.normalize("codex", raw).command == "ls -la"


def test_powershell_writes_are_extracted() -> None:
    cmd = "Set-Content -LiteralPath 'C:\\p\\a.txt' -Value 'hola' -NoNewline; Out-File .git\\config"
    writes = hosts.powershell_writes(cmd)
    assert [(w.path, w.new_text) for w in writes] == [("C:\\p\\a.txt", "hola"), (".git\\config", "")]


def test_powershell_write_into_git_is_denied_in_codex() -> None:
    payload = {"tool_name": "Bash", "tool_input": {"command": "Set-Content -LiteralPath .git/config -Value x"},
               "cwd": str(PROJECT_DIR), "session_id": "s", "turn_id": "t"}
    assert emitted("codex", run_dispatch("codex", payload)[1]) == "deny"


def test_non_claude_hosts_ignore_inherited_claude_project_dir(monkeypatch) -> None:
    """Codex hereda CLAUDE_PROJECT_DIR del proceso padre: el ancla es el cwd de la sesión de Codex."""
    elsewhere = Path.home() / "synapse-otro-proyecto"
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(elsewhere))
    payload = {"tool_name": "Bash", "tool_input": {"command": f"touch {elsewhere}/x"}, "cwd": str(PROJECT_DIR),
               "session_id": "s", "turn_id": "t"}
    _, out, _ = run_dispatch("codex", payload)
    assert "structure.outside_project" in out


# --- Aprobación de un solo uso (04 §3.3) ---------------------------------------------------------------------

class FakeTTY(io.StringIO):
    def isatty(self) -> bool:
        return True


def codex_bash(command: str, call: str = "u1") -> dict:
    return {"tool_name": "Bash", "tool_input": {"command": command}, "cwd": str(PROJECT_DIR), "session_id": "s",
            "turn_id": "t", "tool_use_id": call}


ASK = "git reset --hard HEAD~1"  # regex.ask (un force push a main es un bloqueo catastrófico, no un ask)


def approval_id(out: str) -> str:
    reason = json.loads(out)["hookSpecificOutput"]["permissionDecisionReason"]
    return reason.split(" approve ")[1].split()[0]


def test_ask_without_approval_is_denied_with_id() -> None:
    _, out, _ = run_dispatch("codex", codex_bash(ASK))
    approval = approval_id(out)
    assert (Path(os.environ["SYNAPSE_STATE_DIR"]) / "approvals" / "pending" / f"{approval}.json").exists()


def test_approval_lets_one_retry_pass_and_only_one() -> None:
    _, out, _ = run_dispatch("codex", codex_bash(ASK, "u1"))
    approval = approval_id(out)
    assert approvals.approve(approval, stdin=FakeTTY("y\n")) == 0
    assert run_dispatch("codex", codex_bash(ASK, "u2"))[1] == ""            # pasa
    assert run_dispatch("codex", codex_bash(ASK, "u2"))[1] == ""            # evento duplicado (mismo call_id)
    assert emitted("codex", run_dispatch("codex", codex_bash(ASK, "u3"))[1]) == "deny"  # ya se usó


def test_approve_requires_a_tty() -> None:
    _, out, _ = run_dispatch("codex", codex_bash(ASK))
    assert approvals.approve(approval_id(out), stdin=io.StringIO("y\n")) == 2


def test_expired_approval_is_not_used() -> None:
    _, out, _ = run_dispatch("codex", codex_bash(ASK))
    approval = approval_id(out)
    import time

    assert approvals.approve(approval, stdin=FakeTTY("y\n"), now=time.time() - approvals.APPROVED_TTL - 5) == 0
    assert emitted("codex", run_dispatch("codex", codex_bash(ASK, "u9"))[1]) == "deny"


def test_approval_never_turns_a_block_into_pass() -> None:
    _, out, _ = run_dispatch("codex", codex_bash("rm -rf /"))
    assert "approve" not in out and emitted("codex", out) == "deny"


def test_codex_denial_lets_the_agent_keep_working() -> None:
    # content-studio 012 (2026-11-21): un patch de docs con un archivo protegido dejó a Codex esperando al usuario
    _, out, _ = run_dispatch("codex", codex_bash(ASK, "keep"))
    reason = json.loads(out)["hookSpecificOutput"]["permissionDecisionReason"]
    assert "espera su respuesta" not in reason
    assert "sigue con el trabajo que no dependa" in reason
    assert "sin este archivo" in reason


def test_dispatch_main_writes_utf8_even_with_a_legacy_console(tmp_path: Path) -> None:
    # En Windows, Python escribe en cp1252 y Codex lo lee como UTF-8: "Synapse · …" llegaba como "Synapse � …"
    import subprocess
    import sys

    env = {**os.environ, "PYTHONIOENCODING": "cp1252", "PYTHONUTF8": "0"}
    payload = json.dumps(codex_patch("*** Begin Patch\n*** Add File: .codex/config.toml\n+x = 1\n*** End Patch"),
                         ensure_ascii=False).encode("utf-8")
    script = Path(dispatch.__file__)
    proc = subprocess.run([sys.executable, str(script), "--host", "codex"], input=payload, capture_output=True,
                          env=env, cwd=str(PROJECT_DIR), check=False)
    text = proc.stdout.decode("utf-8")  # falla si salió en cp1252
    assert proc.returncode == 0 and "Synapse ·" in text


def test_agent_cannot_run_approve() -> None:
    _, out, _ = run_dispatch("codex", codex_bash('python3 "/x/scripts/synapse.py" approve abcd1234'))
    assert "structure.self_approval" in out
