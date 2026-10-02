#!/usr/bin/env python3
"""Suite de pruebas automatizada y exhaustiva para los 4 hooks de seguridad potenciados por Laya.

Valida:
  1. src/block_dangerous.py (10 casos: auto-aprobación de temporales, borrado permanente, fork bombs, rm -rf /, force push)
  2. src/block_env_read.py (7 casos: Read tool, cat/head/awk sobre .env, source permitido, heredoc write)
  3. src/detect_secrets.py (6 casos: OpenAI keys, AWS keys, passwords, placeholders, os.environ, código seguro)
  4. src/protect_files.py (9 casos: .git, lockfiles uv/npm, .venv, .env, plantillas .env.example, settings 'ask')

Contexto y Modos de Ejecución:
  - In-Process (Fast): Ejecución de 32 casos en ~700ms (GPU) o ~4ms (Fallback) reutilizando el router singleton.
  - Subprocess (--subprocess): Ejecución en procesos aislados simulando llamadas reales de Claude Code.
  - Soporte de dispositivos: --gpu (CUDA), --cpu (CPU con hilos optimizados), --fallback (reglas originales).

Vínculos con la investigación en docs/research/laya/:
  - docs/research/laya/07-optimizacion-y-rendimiento.md:
      Metodología de benchmark y latencia comparativa in-process vs subproceso.
  - docs/research/laya/09-recetario-de-ejemplos-practicos.md:
      Casos de prueba sintéticos para validación de guardrails de seguridad.

Ejemplos de Ejecución:
  $ python src/test_hooks.py --gpu                  # 32 pruebas en GPU (<800ms)
  $ python src/test_hooks.py --cpu                  # 32 pruebas en CPU
  $ python src/test_hooks.py --fallback             # 32 pruebas en modo fallback sin Laya (<5ms)
  $ python src/test_hooks.py --gpu --hook dangerous # Solo pruebas del hook dangerous
"""

from __future__ import annotations

import argparse
import io
import json
import os
import subprocess
import sys
import time
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass
from pathlib import Path


@dataclass
class TestCase:
    name: str
    hook_script: str
    payload: dict
    expected_exit: int
    expected_decision: str | None = None  # "allow" | "ask" | None
    expected_in_stderr: str | None = None
    setup_file: tuple[str, str] | None = None  # (path, content)


def run_test_in_process(case: TestCase, project_dir: str) -> tuple[bool, str, float]:
    """Ejecuta la prueba in-process redirigiendo stdin/stdout/stderr y capturando SystemExit."""
    temp_created: str | None = None
    if case.setup_file:
        file_path, content = case.setup_file
        full_path = os.path.join(project_dir, file_path)
        os.makedirs(os.path.dirname(full_path), exist_ok=True)
        Path(full_path).write_text(content)
        temp_created = full_path

    # Mapeo de script a función main
    module_name = case.hook_script.replace(".py", "")
    mod = sys.modules.get(module_name)
    if mod is None:
        import importlib
        mod = importlib.import_module(module_name)

    input_data = json.dumps(case.payload)
    old_stdin = sys.stdin
    stdout_buf = io.StringIO()
    stderr_buf = io.StringIO()

    start_t = time.perf_counter()
    exit_code = 0
    try:
        sys.stdin = io.StringIO(input_data)
        with redirect_stdout(stdout_buf), redirect_stderr(stderr_buf):
            try:
                mod.main()
            except SystemExit as exc:
                exit_code = exc.code if isinstance(exc.code, int) else (0 if exc.code is None else 1)
    finally:
        sys.stdin = old_stdin
        if temp_created and os.path.exists(temp_created):
            try:
                os.remove(temp_created)
            except OSError:
                pass

    elapsed = (time.perf_counter() - start_t) * 1000
    stdout_str = stdout_buf.getvalue()
    stderr_str = stderr_buf.getvalue()

    # 1. Verificar exit code
    if exit_code != case.expected_exit:
        msg = f"Exit code mismatch: esperado {case.expected_exit}, obtenido {exit_code}. Stderr: {stderr_str.strip()}"
        return False, msg, elapsed

    # 2. Verificar decisión JSON en stdout si aplica
    if case.expected_decision is not None:
        try:
            out = json.loads(stdout_str)
            decision = out.get("hookSpecificOutput", {}).get("permissionDecision")
            if decision != case.expected_decision:
                msg = f"Decision mismatch: esperada '{case.expected_decision}', obtenida '{decision}'"
                return False, msg, elapsed
        except Exception as exc:
            msg = f"Error parseando stdout JSON: {exc}. Salida cruda: {stdout_str}"
            return False, msg, elapsed

    # 3. Verificar patrón en stderr si aplica
    if case.expected_in_stderr is not None:
        if case.expected_in_stderr.lower() not in stderr_str.lower():
            msg = f"Stderr mismatch: se esperaba '{case.expected_in_stderr}' en: '{stderr_str.strip()}'"
            return False, msg, elapsed

    return True, "OK", elapsed


def run_test_subprocess(case: TestCase, device_flag: str, project_dir: str) -> tuple[bool, str, float]:
    """Ejecuta la prueba en un subproceso independiente."""
    script_path = os.path.join(project_dir, "hooks", case.hook_script)
    python_bin = sys.executable

    temp_created: str | None = None
    if case.setup_file:
        file_path, content = case.setup_file
        full_path = os.path.join(project_dir, file_path)
        os.makedirs(os.path.dirname(full_path), exist_ok=True)
        Path(full_path).write_text(content)
        temp_created = full_path

    cmd = [python_bin, script_path, device_flag]
    input_data = json.dumps(case.payload)

    start_t = time.perf_counter()
    try:
        proc = subprocess.run(
            cmd,
            input=input_data,
            text=True,
            capture_output=True,
            timeout=30,
            cwd=project_dir,
        )
    except subprocess.TimeoutExpired:
        return False, "TIMEOUT (30s)", 30.0
    finally:
        if temp_created and os.path.exists(temp_created):
            try:
                os.remove(temp_created)
            except OSError:
                pass

    elapsed = (time.perf_counter() - start_t) * 1000

    if proc.returncode != case.expected_exit:
        msg = f"Exit code mismatch: esperado {case.expected_exit}, obtenido {proc.returncode}. Stderr: {proc.stderr.strip()}"
        return False, msg, elapsed

    if case.expected_decision is not None:
        try:
            out = json.loads(proc.stdout)
            decision = out.get("hookSpecificOutput", {}).get("permissionDecision")
            if decision != case.expected_decision:
                msg = f"Decision mismatch: esperada '{case.expected_decision}', obtenida '{decision}'"
                return False, msg, elapsed
        except Exception as exc:
            msg = f"Error parseando stdout JSON: {exc}. Salida cruda: {proc.stdout}"
            return False, msg, elapsed

    if case.expected_in_stderr is not None:
        if case.expected_in_stderr.lower() not in proc.stderr.lower():
            msg = f"Stderr mismatch: se esperaba '{case.expected_in_stderr}' en: '{proc.stderr.strip()}'"
            return False, msg, elapsed

    return True, "OK", elapsed


def build_test_cases(project_dir: str) -> list[TestCase]:
    tmp_env_content = (
        "# Archivo de prueba de entorno\n"
        "DATABASE_URL=postgres://user:secret@localhost:5432/mydb\n"
        "DEBUG=true\n"
        "PORT=8080\n"
        "API_SECRET=my-super-secret-pass-key-1234\n"
    )

    return [
        # =====================================================================
        # 1. block_dangerous.py
        # =====================================================================
        TestCase(
            name="dangerous_auto_allow_tmp_file",
            hook_script="block_dangerous.py",
            payload={"tool_name": "Bash", "tool_input": {"command": "rm -f ./tmp/cache_item.json"}, "cwd": project_dir},
            expected_exit=0,
            expected_decision="allow",
        ),
        TestCase(
            name="dangerous_auto_allow_tmp_dir",
            hook_script="block_dangerous.py",
            payload={"tool_name": "Bash", "tool_input": {"command": "rm -rf ./tmp/build_cache/"}, "cwd": project_dir},
            expected_exit=0,
            expected_decision="allow",
        ),
        TestCase(
            name="dangerous_auto_allow_build_artifact",
            hook_script="block_dangerous.py",
            payload={"tool_name": "Bash", "tool_input": {"command": "rm -f dist/app.bundle.js"}, "cwd": project_dir},
            expected_exit=0,
            expected_decision="allow",
        ),
        TestCase(
            name="dangerous_auto_allow_session_scratchpad",
            hook_script="block_dangerous.py",
            payload={"tool_name": "Bash", "tool_input": {"command": "rm -rf /tmp/claude-session-123/out.log"}, "cwd": project_dir},
            expected_exit=0,
            expected_decision="allow",
        ),
        TestCase(
            name="dangerous_block_system_tmp",
            hook_script="block_dangerous.py",
            payload={"tool_name": "Bash", "tool_input": {"command": "rm -f /tmp/os_system_file.log"}, "cwd": project_dir},
            expected_exit=2,
            expected_in_stderr="sistema operativo",
        ),
        TestCase(
            name="dangerous_block_root_delete",
            hook_script="block_dangerous.py",
            payload={"tool_name": "Bash", "tool_input": {"command": "rm -rf /"}, "cwd": project_dir},
            expected_exit=2,
            expected_in_stderr="catastrófico",
        ),
        TestCase(
            name="dangerous_block_fork_bomb",
            hook_script="block_dangerous.py",
            payload={"tool_name": "Bash", "tool_input": {"command": ":(){ :|:& };:"}, "cwd": project_dir},
            expected_exit=2,
            expected_in_stderr="catastrófico",
        ),
        TestCase(
            name="dangerous_block_force_push",
            hook_script="block_dangerous.py",
            payload={"tool_name": "Bash", "tool_input": {"command": "git push origin --force master"}, "cwd": project_dir},
            expected_exit=2,
            expected_in_stderr="catastrófico",
        ),
        TestCase(
            name="dangerous_ask_permanent_file_deletion",
            hook_script="block_dangerous.py",
            payload={"tool_name": "Bash", "tool_input": {"command": "rm src/common.py"}, "cwd": project_dir},
            expected_exit=0,
            expected_decision="ask",
        ),
        TestCase(
            name="dangerous_allow_safe_command",
            hook_script="block_dangerous.py",
            payload={"tool_name": "Bash", "tool_input": {"command": "pytest -v tests/"}, "cwd": project_dir},
            expected_exit=0,
        ),

        # =====================================================================
        # 2. block_env_read.py
        # =====================================================================
        TestCase(
            name="env_block_read_tool",
            hook_script="block_env_read.py",
            payload={"tool_name": "Read", "tool_input": {"file_path": "./tmp/test.env"}, "cwd": project_dir},
            expected_exit=2,
            expected_in_stderr="DATABASE_URL=<URL",
            setup_file=("./tmp/test.env", tmp_env_content),
        ),
        TestCase(
            name="env_allow_read_template",
            hook_script="block_env_read.py",
            payload={"tool_name": "Read", "tool_input": {"file_path": "./conf/.env.example"}, "cwd": project_dir},
            expected_exit=0,
        ),
        TestCase(
            name="env_block_bash_cat",
            hook_script="block_env_read.py",
            payload={"tool_name": "Bash", "tool_input": {"command": "cat ./tmp/test.env"}, "cwd": project_dir},
            expected_exit=2,
            expected_in_stderr="DEBUG=<booleano>",
            setup_file=("./tmp/test.env", tmp_env_content),
        ),
        TestCase(
            name="env_block_bash_head",
            hook_script="block_env_read.py",
            payload={"tool_name": "Bash", "tool_input": {"command": "head -n 20 ./tmp/test.env"}, "cwd": project_dir},
            expected_exit=2,
            expected_in_stderr="PORT=<numerico",
            setup_file=("./tmp/test.env", tmp_env_content),
        ),
        TestCase(
            name="env_allow_source_command",
            hook_script="block_env_read.py",
            payload={"tool_name": "Bash", "tool_input": {"command": "set -a; source ./tmp/test.env; set +a"}, "cwd": project_dir},
            expected_exit=0,
            setup_file=("./tmp/test.env", tmp_env_content),
        ),
        TestCase(
            name="env_allow_heredoc_write",
            hook_script="block_env_read.py",
            payload={"tool_name": "Bash", "tool_input": {"command": "cat > ./tmp/test.env << 'EOF'\nFOO=bar\nEOF"}, "cwd": project_dir},
            expected_exit=0,
        ),
        TestCase(
            name="env_block_bash_awk",
            hook_script="block_env_read.py",
            payload={"tool_name": "Bash", "tool_input": {"command": "awk '{print $0}' ./tmp/test.env"}, "cwd": project_dir},
            expected_exit=2,
            setup_file=("./tmp/test.env", tmp_env_content),
        ),

        # =====================================================================
        # 3. detect_secrets.py
        # =====================================================================
        TestCase(
            name="secrets_block_openai_key",
            hook_script="detect_secrets.py",
            payload={"tool_name": "Edit", "tool_input": {"new_string": "OPENAI_KEY = 'sk-proj-ab12cd34ef56gh78ij90kl12mnop34'"}},
            expected_exit=2,
            expected_in_stderr="possible secret detected",
        ),
        TestCase(
            name="secrets_block_aws_key",
            hook_script="detect_secrets.py",
            payload={"tool_name": "Write", "tool_input": {"content": "export AWS_ACCESS_KEY_ID=AKIAIOSFODNN7EXAMPLE\n"}},
            expected_exit=2,
            expected_in_stderr="possible secret detected",
        ),
        TestCase(
            name="secrets_block_hardcoded_password",
            hook_script="detect_secrets.py",
            payload={"tool_name": "Edit", "tool_input": {"new_string": "password = 'SuperSecretProdPassword999!'"}},
            expected_exit=2,
            expected_in_stderr="possible secret detected",
        ),
        TestCase(
            name="secrets_allow_placeholder_key",
            hook_script="detect_secrets.py",
            payload={"tool_name": "Edit", "tool_input": {"new_string": "api_key = 'your-api-key-here'"}},
            expected_exit=0,
        ),
        TestCase(
            name="secrets_allow_env_getter",
            hook_script="detect_secrets.py",
            payload={"tool_name": "Edit", "tool_input": {"new_string": "api_key = os.environ.get('API_KEY')"}},
            expected_exit=0,
        ),
        TestCase(
            name="secrets_allow_safe_code",
            hook_script="detect_secrets.py",
            payload={"tool_name": "Write", "tool_input": {"content": "def calculate_subtotal(amount: float) -> float:\n    return amount * 1.19\n"}},
            expected_exit=0,
        ),

        # =====================================================================
        # 4. protect_files.py
        # =====================================================================
        TestCase(
            name="protect_block_git_dir",
            hook_script="protect_files.py",
            payload={"tool_name": "Write", "tool_input": {"file_path": ".git/config"}, "cwd": project_dir},
            expected_exit=2,
            expected_in_stderr="cannot modify protected",
        ),
        TestCase(
            name="protect_block_lockfile_package",
            hook_script="protect_files.py",
            payload={"tool_name": "Edit", "tool_input": {"file_path": "package-lock.json"}, "cwd": project_dir},
            expected_exit=2,
            expected_in_stderr="cannot modify protected",
        ),
        TestCase(
            name="protect_block_lockfile_uv",
            hook_script="protect_files.py",
            payload={"tool_name": "Edit", "tool_input": {"file_path": "uv.lock"}, "cwd": project_dir},
            expected_exit=2,
            expected_in_stderr="cannot modify protected",
        ),
        TestCase(
            name="protect_block_env_file",
            hook_script="protect_files.py",
            payload={"tool_name": "Write", "tool_input": {"file_path": ".env"}, "cwd": project_dir},
            expected_exit=2,
            expected_in_stderr="cannot modify protected",
        ),
        TestCase(
            name="protect_block_venv_internal",
            hook_script="protect_files.py",
            payload={"tool_name": "Edit", "tool_input": {"file_path": ".venv/bin/activate"}, "cwd": project_dir},
            expected_exit=2,
            expected_in_stderr="cannot modify protected",
        ),
        TestCase(
            name="protect_allow_env_example_template",
            hook_script="protect_files.py",
            payload={"tool_name": "Write", "tool_input": {"file_path": ".env.example"}, "cwd": project_dir},
            expected_exit=0,
        ),
        TestCase(
            name="protect_ask_claude_settings",
            hook_script="protect_files.py",
            payload={"tool_name": "Edit", "tool_input": {"file_path": ".claude/settings.json"}, "cwd": project_dir},
            expected_exit=0,
            expected_decision="ask",
        ),
        TestCase(
            name="protect_ask_claude_hooks",
            hook_script="protect_files.py",
            payload={"tool_name": "Edit", "tool_input": {"file_path": ".claude/hooks/block-dangerous.py"}, "cwd": project_dir},
            expected_exit=0,
            expected_decision="ask",
        ),
        TestCase(
            name="protect_allow_standard_source",
            hook_script="protect_files.py",
            payload={"tool_name": "Write", "tool_input": {"file_path": "src/services/payment.py"}, "cwd": project_dir},
            expected_exit=0,
        ),
    ]


def main() -> None:
    parser = argparse.ArgumentParser(description="Test runner for Laya security hooks")
    parser.add_argument("--gpu", action="store_true", help="Run tests using GPU (CUDA)")
    parser.add_argument("--cpu", action="store_true", help="Run tests using CPU")
    parser.add_argument("--fallback", action="store_true", help="Run tests using deterministic fallback rules (no Laya)")
    parser.add_argument("--hook", choices=["dangerous", "env", "secrets", "protect"], help="Filter tests by hook")
    parser.add_argument("--subprocess", action="store_true", help="Run each test in an independent subprocess")
    args = parser.parse_args()

    if args.fallback:
        device_flag = "--fallback"
    elif args.cpu:
        device_flag = "--cpu"
    else:
        device_flag = "--gpu"

    project_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    hooks_dir = os.path.join(project_dir, "hooks")
    if hooks_dir not in sys.path:
        sys.path.insert(0, hooks_dir)

    # Configurar argv para que common.get_configured_device() y should_use_laya() lo lean
    sys.argv = [sys.argv[0], device_flag]

    # Pre-cargar router para pruebas in-process solo si se utiliza Laya
    if not args.subprocess and not args.fallback:
        import common
        common._ROUTER_INSTANCE = None  # Reset para asegurar el dispositivo correcto
        _ = common.get_laya_router()

    # Asegurar que ./tmp existe dentro del proyecto
    os.makedirs(os.path.join(project_dir, "tmp"), exist_ok=True)

    all_cases = build_test_cases(project_dir)

    hook_map = {
        "dangerous": "block_dangerous.py",
        "env": "block_env_read.py",
        "secrets": "detect_secrets.py",
        "protect": "protect_files.py",
    }
    if args.hook:
        target_script = hook_map[args.hook]
        cases = [c for c in all_cases if c.hook_script == target_script]
    else:
        cases = all_cases

    runner_mode = "SUBPROCESS" if args.subprocess else "IN-PROCESS (FAST)"
    print("=" * 80)
    print(f"EJECUTANDO SUITE DE PRUEBAS DE HOOKS DE SEGURIDAD LAYA")
    print(f"Modo: {device_flag.upper()} ({runner_mode}) | Casos seleccionados: {len(cases)}")
    print("=" * 80)

    passed = 0
    failed = 0
    total_time = 0.0

    for i, case in enumerate(cases, 1):
        if args.subprocess:
            ok, msg, elapsed = run_test_subprocess(case, device_flag, project_dir)
        else:
            ok, msg, elapsed = run_test_in_process(case, project_dir)

        total_time += elapsed
        status_tag = "\033[92mPASS\033[0m" if ok else "\033[91mFAIL\033[0m"
        print(f"[{i:02d}/{len(cases):02d}] {case.hook_script:20s} :: {case.name:36s} -> {status_tag} ({elapsed:6.1f} ms)")
        if not ok:
            print(f"     Motivo de fallo: {msg}")
            failed += 1
        else:
            passed += 1

    print("-" * 80)
    print(f"RESULTADOS FINALES ({device_flag.upper()}):")
    print(f"Total: {len(cases)} | Aprobados: {passed} | Fallidos: {failed} | Tiempo total: {total_time:.1f} ms")
    print("=" * 80)

    if failed > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
