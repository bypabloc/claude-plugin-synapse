"""Aprobación de un solo uso para Codex (spec 001 · 02 §6, D-04).

Codex no tiene 'ask' en PreToolUse y un 'deny' a secas se repite en cada reintento. Synapse responde 'deny' con
un id; el usuario aprueba en su terminal (`python3 <plugin>/scripts/synapse.py approve <id>`, con TTY) y el
reintento del mismo comando pasa una sola vez. Un 'block' nunca se aprueba; el agente no puede ejecutar
'approve' (regla structure.self_approval de block_dangerous).

  <state_dir>/approvals/pending/<id>.json    vence a los 30 min
  <state_dir>/approvals/approved/<id>.json   vence a los 10 min de aprobado
  <state_dir>/approvals/consumed/<id>.json   auditoría, 24 h (repite 'pass' ante un evento duplicado: H-02)
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any

import common
import hosts

PENDING_TTL = 30 * 60
APPROVED_TTL = 10 * 60
CONSUMED_TTL = 24 * 3600
PLUGIN_ROOT = Path(__file__).resolve().parent.parent


def base_dir() -> Path:
    return common.state_dir() / "approvals"


def _dir(kind: str) -> Path:
    path = base_dir() / kind
    path.mkdir(parents=True, exist_ok=True)
    return path


def canonical_target(event: hosts.HookEvent) -> str:
    if event.kind == "shell":
        return re.sub(r"\s+", " ", event.command).strip()
    parts = [f"{c.path}|{c.move_to or ''}|{hashlib.sha256(c.new_text.encode()).hexdigest()}" for c in event.files]
    return "\n".join(sorted(parts)) or event.read_path


def approval_key(event: hosts.HookEvent, decision: hosts.Decision) -> str:
    raw = "\0".join([event.host, event.project_dir, decision.hook, decision.rule, canonical_target(event)])
    return hashlib.sha256(raw.encode()).hexdigest()


def _write(path: Path, data: dict[str, Any]) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)


def _read(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def purge(now: float | None = None) -> None:
    now = time.time() if now is None else now
    for kind, field, ttl in (("pending", "created_at", PENDING_TTL), ("approved", "approved_at", APPROVED_TTL),
                             ("consumed", "consumed_at", CONSUMED_TTL)):
        for path in _dir(kind).glob("*.json"):
            data = _read(path)
            if data is None or now - float(data.get(field, 0)) > ttl:
                path.unlink(missing_ok=True)


def command_hint(approval_id: str) -> str:
    script = PLUGIN_ROOT / "scripts" / "synapse.py"
    python = "python" if os.name == "nt" else "python3"
    return f'{python} "{script}" approve {approval_id}'


def denial_text(decision: hosts.Decision, approval_id: str) -> str:
    return (f"{decision.shown or decision.reason}\n"
            "Requiere confirmación del usuario. Codex no permite preguntar desde un hook, así que la aprobación es manual:\n"
            f"  ejecuta en TU terminal (fuera del agente):  {command_hint(approval_id)}\n"
            "  (vence en 30 min; aprueba una sola ejecución de este mismo comando)\n"
            "Agente: no intentes aprobarlo ni rodear la regla (ni con otra herramienta ni con otro comando que haga lo mismo). "
            "Si la acción incluía otros cambios, aplícalos aparte sin este archivo y sigue con el trabajo que no dependa "
            "de él. Deja anotado lo pendiente y, al cerrar el turno, pídele al usuario que ejecute el comando de arriba.")


def resolve(event: hosts.HookEvent, decision: hosts.Decision, now: float | None = None) -> hosts.Decision:
    """Para un 'ask' en Codex: 'pass' si hay una aprobación vigente (se consume), si no 'ask' con el texto de aprobación."""
    now = time.time() if now is None else now
    purge(now)
    key = approval_key(event, decision)
    approval_id = key[:8]
    approved = _dir("approved") / f"{approval_id}.json"
    consumed = _dir("consumed") / f"{approval_id}.json"

    replay = _read(consumed)
    if replay and replay.get("key") == key and event.call_id and replay.get("call_id") == event.call_id:
        common.log_step("approval", action="replayed", id=approval_id)
        return hosts.Decision("pass", decision.hook, "approval.replayed", "python", decision.reason, decision.target)

    data = _read(approved)
    if data and data.get("key") == key and now - float(data.get("approved_at", 0)) <= APPROVED_TTL:
        data.update(consumed_at=now, call_id=event.call_id)
        _write(consumed, data)
        approved.unlink(missing_ok=True)
        common.log_step("approval", action="consumed", id=approval_id)
        common.record_audit_log("ALLOW", decision.hook, event.tool, decision.target, "Aprobado por el usuario (un solo uso)",
                                decided_by="python", rule="approval.consumed")
        return hosts.Decision("pass", decision.hook, "approval.consumed", "python", decision.reason, decision.target)

    _write(_dir("pending") / f"{approval_id}.json", {
        "key": key, "id": approval_id, "host": event.host, "hook": decision.hook, "rule": decision.rule,
        "reason": decision.reason, "preview": common.redact(canonical_target(event))[:200], "cwd": event.cwd,
        "project_dir": event.project_dir, "session": event.session, "created_at": now})
    common.log_step("approval", action="pending", id=approval_id)
    return hosts.Decision("ask", decision.hook, decision.rule, decision.decided_by, decision.reason, decision.target,
                          denial_text(decision, approval_id), decision.context)


def approve(approval_id: str, *, confirm: bool = True, now: float | None = None, stdin: Any = None) -> int:
    """CLI del usuario: pending → approved. Exige una terminal interactiva (el agente no tiene TTY)."""
    now = time.time() if now is None else now
    stdin = sys.stdin if stdin is None else stdin
    purge(now)
    pending = _dir("pending") / f"{approval_id}.json"
    data = _read(pending)
    if data is None:
        print(f"✗ no hay una aprobación pendiente con id {approval_id} (¿venció?)", file=sys.stderr)
        return 1
    if confirm and not stdin.isatty():
        print("✗ la aprobación exige una terminal interactiva: ejecútala tú, no el agente", file=sys.stderr)
        return 2
    print(f"Synapse · {data['hook']} · regla {data['rule']}\n  motivo:  {data['reason']}\n  qué:     {data['preview']}\n"
          f"  dónde:   {data['cwd']}")
    if confirm:
        print("¿Aprobar UNA ejecución? [y/N] ", end="", flush=True)
        if stdin.readline().strip().lower() not in {"y", "s", "yes", "si", "sí"}:
            print("No se aprobó.")
            return 1
    data["approved_at"] = now
    _write(_dir("approved") / f"{approval_id}.json", data)
    pending.unlink(missing_ok=True)
    print(f"✓ aprobado por {APPROVED_TTL // 60} min: pide al agente que reintente")
    return 0


def pending_list(now: float | None = None) -> list[dict[str, Any]]:
    purge(now)
    rows = [d for d in (_read(p) for p in sorted(_dir("pending").glob("*.json"))) if d]
    return rows + [dict(d, approved=True) for d in (_read(p) for p in sorted(_dir("approved").glob("*.json"))) if d]
