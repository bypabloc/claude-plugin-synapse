#!/usr/bin/env python3
"""Entrada única de Synapse para los hosts distintos de Claude Code (spec 001 · 02 §3.2).

  python3 hooks/dispatch.py --host codex|antigravity|opencode   (payload del host por stdin)

Normaliza el payload, corre en este proceso los hooks que aplican al tipo de acción, combina sus decisiones
(block > ask > allow > pass) y emite una sola salida con el contrato del host. En Codex, un 'ask' pasa por la
aprobación de un solo uso. Nada sale con código distinto de 0: en Antigravity eso equivale a permitir (D-05).
"""

from __future__ import annotations

import dataclasses
import json
import os
import sys
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

HOOKS_BY_KIND = {
    "shell": ("block_dangerous", "block_env_read"),
    "read": ("block_env_read",),
    "write": ("protect_files", "detect_secrets"),
    "other": (),
}


def _host(argv: list[str]) -> str:
    if "--host" in argv and argv.index("--host") + 1 < len(argv):
        return argv[argv.index("--host") + 1]
    return ""


def _trace_payload(event: Any) -> dict[str, Any]:
    """Lo que start_trace espera (forma de Claude) a partir de un HookEvent; de las escrituras, solo el tamaño."""
    tool_input: dict[str, Any] = {}
    if event.kind == "shell":
        tool_input["command"] = event.command
    elif event.kind == "read":
        tool_input["file_path"] = event.read_path
    elif event.kind == "write":
        tool_input["file_path"] = ", ".join(f.path for f in event.files)
        tool_input["content"] = "x" * sum(len(f.new_text) for f in event.files)
    return {"session_id": event.session, "tool_name": event.tool, "cwd": event.cwd, "tool_input": tool_input}


def run(host: str, raw_text: str) -> tuple[int, str, str]:
    import importlib

    import common
    import hosts

    try:
        raw = json.loads(raw_text) if raw_text.strip() else {}
        event = hosts.normalize(host, raw)
    except (ValueError, hosts.NormalizeError) as exc:
        hosts.set_context(host, None)
        common.record_audit_log("ALLOW", "dispatch", "", reason=f"payload no reconocido: {exc}", rule="normalize.error")
        return hosts.failsafe(host, f"Synapse · payload de {host} no reconocido ({exc}): confirma manualmente. Revisa synapse doctor.")

    hosts.set_context(host, event.project_dir)
    decisions: list[hosts.Decision] = []
    evaluated: list[str] = []
    plan = [(name, event) for name in HOOKS_BY_KIND.get(event.kind, ())]
    writes = hosts.powershell_writes(event.command) if event.kind == "shell" else ()
    if writes:  # Set-Content / Out-File: además del shell, se evalúan como escrituras
        as_write = dataclasses.replace(event, kind="write", files=writes)
        plan += [(name, as_write) for name in HOOKS_BY_KIND["write"]]
    for name, current in plan:
        module = importlib.import_module(name)
        common.start_trace(name, _trace_payload(current))
        if current.parse_error:
            common.log_step("patch.parse_error", error=current.parse_error)
        evaluated.append(name)
        with hosts.collecting(decisions):
            try:
                common.run_main(lambda: module.evaluate(current))
            except hosts.Decided:
                pass
            except SystemExit:
                pass  # 'sin objeciones': el hook ya registró su decisión
        if decisions and decisions[-1].verdict == "block":
            break

    decision = hosts.combine(decisions)
    common.log_step("dispatch", hooks=evaluated, verdicts=[d.verdict for d in decisions], decision=decision.verdict)

    if host == "codex" and decision.verdict == "ask":
        import approvals

        decision = approvals.resolve(event, decision)
        if decision.verdict == "pass":
            return 0, "", ""
    return hosts.emit(host, decision)


def main() -> int:
    host = _host(sys.argv[1:])
    try:
        raw_text = sys.stdin.read()
        code, out, err = run(host, raw_text)
    except BaseException as exc:  # noqa: BLE001 — nada debe fallar abierto por un error interno (D-05)
        try:
            import hosts

            code, out, err = hosts.failsafe(host, f"Synapse · error interno ({type(exc).__name__}): confirma manualmente. "
                                                  "Revisa synapse doctor.")
        except BaseException:
            code, out, err = 0, ('{"decision":"force_ask","reason":"Synapse · error interno"}' if host == "antigravity" else ""), ""
    if out:
        sys.stdout.write(out)
    if err:
        sys.stderr.write(err)
    return code


if __name__ == "__main__":
    sys.exit(main())
