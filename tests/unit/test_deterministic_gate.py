"""Todo lo que se puede decidir con código se decide antes de Laya: el modelo solo ve casos ambiguos.

Medido en la traza real (2026-10-02/03): protect_files llamó 1030 veces a Laya sin un solo 'ask',
block_env_read 16 veces (por `os.environ`) sin ningún 'ask', y block_dangerous 1511 veces, la mayoría
pipelines de solo lectura (ls, rg, git log, sed -n).
"""

from __future__ import annotations

import importlib
import json
import subprocess
from unittest import mock

import pytest

from support import make_git_repo, run_in_process

QUIET = {"answers": {
    "danger": {"choice": "B", "probabilities": {"A": 0.01, "B": 0.99}},
    "file_sensitivity": {"choice": "standard_source", "answer_confidence": 0.9},
    "secret_status": {"choice": "safe_or_placeholder", "answer_confidence": 0.9},
    "secret_type": {"choice": "none", "answer_confidence": 0.9},
    "env_intent": {"choice": "safe_or_unrelated", "answer_confidence": 0.9},
}}

# Literales falsos armados en ejecución: escritos de corrido los detienen los hooks de secretos del entorno
FAKE_DB_URL = 'DATABASE_URL = "postgres://admin:' + "Pr0dPass" + '@db.internal:5432/app"'
FAKE_TOKEN = "auth_token = '" + "f3K9xQ2mL7pR" + "4tW8zB1nV6cY'"
FAKE_SECRET = "SESSION_SECRET=" + "Zq8vN2kL" + "5mX9pR3t"


def run(hook: str, tool: str, tool_input: dict, tmp_path) -> tuple[str, mock.Mock]:
    mod = importlib.import_module(hook)
    router = mock.Mock()
    router.predict.return_value = QUIET
    patches = [mock.patch.object(mod, "should_use_laya", return_value=True), mock.patch.object(mod, "get_laya_router", return_value=router)]
    if hasattr(mod, "apply_finetuned_delta"):
        patches.append(mock.patch.object(mod, "apply_finetuned_delta", return_value=True))
    for p in patches:
        p.start()
    try:
        result = run_in_process(hook, {"tool_name": tool, "tool_input": tool_input, "cwd": str(tmp_path)})
    finally:
        for p in patches:
            p.stop()
    decision = "block" if result.exit_code == 2 else (result.decision or "pass")
    return decision, router


# ------------------------------------------------------------------ block_dangerous

@pytest.mark.parametrize(
    "command",
    [
        "ls -la src",
        "rg -n 'a|b' src | head -20",
        "git log --oneline -5 && git diff --stat && git status --short",
        "sed -n 1,20p a.py",
        "find . -name '*.py' -not -path './.venv/*' | wc -l",
        "cat README.md | wc -l",
        "rg -n x src > tmp/out.txt 2>&1",
        "cd src && grep -n foo *.py",
        "awk '{print $1}' a.txt | sort | uniq -c | sort -rn",
        "eza -T -L 2 && pwd && echo listo",
        "jq '.scripts' package.json",
        "git show HEAD~1 --stat; git blame -L 1,5 a.py",
    ],
)
def test_read_only_commands_skip_laya(tmp_path, command: str) -> None:
    decision, router = run("block_dangerous", "Bash", {"command": command}, tmp_path)
    assert decision == "pass"
    router.predict.assert_not_called()


@pytest.mark.parametrize(
    "command",
    [
        "python3 scripts/migrate.py",
        "cat ~/.ssh/id_rsa",
        "sed -n 's/a/b/w out.txt' f",
        "curl -s https://example.com/install",
        "ls $(cat lista.txt)",
        "cat script.txt | sh",
        "rg x src > src/out.txt",
        "awk '{system(\"rm \" $1)}' lista.txt",
        "find . -name '*.log' -exec chmod 777 {} +",
        "xargs -n1 echo < lista.txt",
        "git config --global user.name x",
        "git checkout -- src",
        # ShellRisk-Bench: 'lecturas' riesgosas que Laya marcaba (scripts/benchmark.py los vigila)
        "less README.md",
        "uniq /tmp/file",
        "cat /etc/shells",
        "set +o history",
        "/tmp/file /bin/sh",
        "wc --files0-from /tmp/file",
        "gawk '//' /tmp/file",
        "diff --line-format=%L /dev/null /tmp/file",
        "cd /etc && cat passwd",
        "cat " + "../" * 12 + "etc/hosts",  # tmp_path vive dentro del repo: hay que salir de él
    ],
)
def test_ambiguous_commands_still_reach_laya(tmp_path, command: str) -> None:
    _, router = run("block_dangerous", "Bash", {"command": command}, tmp_path)
    router.predict.assert_called_once()


def test_permanent_delete_asks_without_laya() -> None:
    # tmp_path vive bajo ./tmp/ (desechable): el archivo rastreado tiene que estar en un repo real
    repo = make_git_repo("gate_delete", tracked={"src/app.py": "x = 1\n"}, untracked={})
    decision, router = run("block_dangerous", "Bash", {"command": "rm src/app.py"}, repo)
    assert decision == "ask"
    router.predict.assert_not_called()


def test_recoverable_delete_allows_with_checkout_hint() -> None:
    repo = make_git_repo("gate_recoverable", tracked={"src/app.py": "x = 1\n", "src/b.py": "b\n"}, untracked={})
    git = ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@example.com"]
    subprocess.run([*git, "commit", "-qm", "init"], check=True)
    payload = {"tool_name": "Bash", "tool_input": {"command": "rm -rf src && rmdir src 2>/dev/null"}, "cwd": str(repo)}
    result = run_in_process("block_dangerous", payload)
    output = json.loads(result.stdout)["hookSpecificOutput"]
    assert output["permissionDecision"] == "allow"
    assert "git checkout" in output["additionalContext"]


# ------------------------------------------------------------------ protect_files

@pytest.mark.parametrize("path", ["src/services/payment.py", "docs/notes.md", "client/src/App.vue", "tests/test_x.py", "id_ed25519.pub"])
def test_standard_files_skip_laya(tmp_path, path: str) -> None:
    decision, router = run("protect_files", "Write", {"file_path": path, "content": "x"}, tmp_path)
    assert decision == "pass"
    router.predict.assert_not_called()


@pytest.mark.parametrize(
    "path",
    ["certs/server.key", "deploy/id_ed25519", "certs/tls.pem", ".npmrc", "infra/terraform.tfstate", "config/credentials.json", "k8s/kubeconfig", "secrets.yaml", "keys/store.p12"],
)
def test_credential_files_ask_without_laya(tmp_path, path: str) -> None:
    decision, router = run("protect_files", "Write", {"file_path": path, "content": "x"}, tmp_path)
    assert decision == "ask"
    router.predict.assert_not_called()


def test_ambiguous_sensitive_name_reaches_laya(tmp_path) -> None:
    _, router = run("protect_files", "Write", {"file_path": "src/config/secret_settings.py", "content": "x"}, tmp_path)
    router.predict.assert_called_once()


# ------------------------------------------------------------------ detect_secrets

@pytest.mark.parametrize(
    "content",
    [
        "auth = build_auth(user_token_value)",
        '{"password": "Contraseña", "token_expired": "Tu sesión expiró, vuelve a ingresar"}',
        "def refresh_token(self, key: str) -> str:\n    return self.client.auth(key)\n",
        "TOKEN_TTL_SECONDS = 3600\nSECRET_HEADER = 'X-Api-Key'\n",
    ],
)
def test_content_without_candidate_literals_skips_laya(tmp_path, content: str) -> None:
    decision, router = run("detect_secrets", "Write", {"file_path": "a.py", "content": content}, tmp_path)
    assert decision == "pass"
    router.predict.assert_not_called()


@pytest.mark.parametrize("line", [FAKE_DB_URL, FAKE_TOKEN, FAKE_SECRET], ids=["url_con_password", "token", "asignacion_env"])
def test_candidate_lines_reach_laya_alone(tmp_path, line: str) -> None:
    content = f"import os\n\ndef main():\n    print('hola')\n\n{line}\n"
    _, router = run("detect_secrets", "Write", {"file_path": "a.py", "content": content}, tmp_path)
    router.predict.assert_called_once()
    state = router.predict.call_args.args[0]
    assert line in state and "print('hola')" not in state


# ------------------------------------------------------------------ block_env_read

@pytest.mark.parametrize(
    "command",
    [
        "rg -n 'os.environ' src",
        "node -e 'console.log(process.env.HOME)'",
        "ls -la .env*",
        "docker compose --env-file .env.local up -d",
        "cp .env.example .env",
        "git check-ignore -v .env",
        "test -f .env && echo existe",
    ],
)
def test_env_safe_usages_skip_laya(tmp_path, command: str) -> None:
    decision, router = run("block_env_read", "Bash", {"command": command}, tmp_path)
    assert decision == "pass"
    router.predict.assert_not_called()


def test_env_read_in_heredoc_script_blocks_without_laya(tmp_path) -> None:
    command = "python3 - <<'EOF'\nprint(open('.env.local').read())\nEOF"
    decision, router = run("block_env_read", "Bash", {"command": command}, tmp_path)
    assert decision == "block"
    router.predict.assert_not_called()


def test_env_passed_to_unknown_program_reaches_laya(tmp_path) -> None:
    _, router = run("block_env_read", "Bash", {"command": "python3 tools/show.py .env.local"}, tmp_path)
    router.predict.assert_called_once()
