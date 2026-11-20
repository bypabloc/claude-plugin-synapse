"""Hosts de Synapse (spec 001 · 02): evento interno, normalización por host y emisión estricta por host.

  claude       hooks por subprocess (hooks/hooks.json): cada script con su main(); la emisión de Claude sigue en
               common.emit_decision / emit_block, sin cambios.
  codex        adapters/codex/hooks.json → dispatch.py --host codex
  antigravity  hooks.json (raíz)        → dispatch.py --host antigravity (siempre exit 0 + JSON)
  opencode     adapters/opencode/index.ts (en proceso) → dispatch.py --host opencode (JSON neutral)

El host se declara (--host); nunca se deduce del entorno: Codex hereda variables CLAUDE_* del proceso padre.
"""

from __future__ import annotations

import json
import os
import re
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Iterator, Literal

import patch

Host = Literal["claude", "codex", "antigravity", "opencode"]
HOSTS: tuple[str, ...] = ("claude", "codex", "antigravity", "opencode")
Kind = Literal["shell", "read", "write", "other"]
Verdict = Literal["block", "ask", "allow", "pass"]
RANK = {"pass": 0, "allow": 1, "ask": 2, "block": 3}


class NormalizeError(ValueError):
    """El payload no tiene la forma esperada para el host declarado."""


class Decided(BaseException):
    """Corta un hook en modo collect. BaseException: run_main no debe registrarla como error."""


@dataclass(frozen=True)
class FileChange:
    path: str
    op: str  # write | edit | add | update | delete
    new_text: str = ""
    old_text: str = ""
    move_to: str | None = None


@dataclass(frozen=True)
class HookEvent:
    host: str
    kind: str
    tool: str
    command: str = ""
    read_path: str = ""
    files: tuple[FileChange, ...] = ()
    cwd: str = ""
    project_dir: str = ""
    session: str = ""
    call_id: str = ""
    parse_error: str | None = None
    raw: dict[str, Any] = field(default_factory=dict, compare=False, repr=False)


@dataclass(frozen=True)
class Decision:
    verdict: str
    hook: str
    rule: str
    decided_by: str
    reason: str = ""
    target: str = ""
    shown: str = ""  # motivo con atribución, tal como se muestra en pantalla
    context: str | None = None


# --- Contexto de la ejecución -------------------------------------------------------------------------------

_STATE: dict[str, Any] = {"host": "claude", "project_dir": None, "collector": None}


def set_context(host: str, project_dir: str | None) -> None:
    _STATE["host"] = host
    _STATE["project_dir"] = project_dir or None


def current_host() -> str:
    return str(_STATE["host"])


def project_dir_override() -> str | None:
    """Ancla de allowed_roots fuera de Claude Code (en Claude manda CLAUDE_PROJECT_DIR)."""
    return _STATE["project_dir"] if _STATE["host"] != "claude" else None


@contextmanager
def collecting(sink: list[Decision]) -> Iterator[None]:
    previous = _STATE["collector"]
    _STATE["collector"] = sink
    try:
        yield
    finally:
        _STATE["collector"] = previous


def collect(decision: Decision) -> bool:
    """Modo collect (dispatch): guarda la decisión y avisa que el hook debe cortarse. False en modo single."""
    sink = _STATE["collector"]
    if sink is None:
        return False
    sink.append(decision)
    return True


# --- Normalización -------------------------------------------------------------------------------------------

def _str(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _shell_text(command: Any) -> str:
    if isinstance(command, list):  # ["bash", "-lc", X] → X
        parts = [str(c) for c in command]
        if len(parts) >= 3 and parts[-2] in {"-lc", "-c"}:
            return parts[-1]
        import shlex
        return shlex.join(parts)
    return _str(command)


def _patch_files(text: str) -> tuple[tuple[FileChange, ...], str | None]:
    try:
        return tuple(FileChange(p, op, new, old, move) for p, op, new, old, move in patch.parse(text)), None
    except patch.PatchError as exc:
        # Patch malformado: se evalúa el texto completo como contenido (detect_secrets ve más, nunca menos)
        return (FileChange("<patch>", "update", new_text=text),), str(exc)


def normalize(host: str, raw: dict[str, Any]) -> HookEvent:
    if host not in HOSTS:
        raise NormalizeError(f"host desconocido: {host}")
    if not isinstance(raw, dict):
        raise NormalizeError("el payload no es un objeto JSON")
    return {"claude": _claude, "codex": _codex, "antigravity": _antigravity, "opencode": _opencode}[host](raw)


def _claude(raw: dict[str, Any]) -> HookEvent:
    tool = _str(raw.get("tool_name"))
    ti = raw.get("tool_input") if isinstance(raw.get("tool_input"), dict) else {}
    cwd = _str(raw.get("cwd"))
    base = dict(host="claude", tool=tool, cwd=cwd, session=_str(raw.get("session_id")),
                call_id=_str(raw.get("tool_use_id")), project_dir=os.environ.get("CLAUDE_PROJECT_DIR") or cwd, raw=raw)
    if tool == "Bash":
        return HookEvent(kind="shell", command=_str(ti.get("command")), **base)
    if tool == "Read":
        return HookEvent(kind="read", read_path=_str(ti.get("file_path")), **base)
    if tool == "Write":
        return HookEvent(kind="write", files=(FileChange(_str(ti.get("file_path")), "write", _str(ti.get("content"))),), **base)
    if tool == "Edit":
        change = FileChange(_str(ti.get("file_path")), "edit", _str(ti.get("new_string")), _str(ti.get("old_string")))
        return HookEvent(kind="write", files=(change,), **base)
    return HookEvent(kind="other", **base)


def _codex(raw: dict[str, Any]) -> HookEvent:
    tool = _str(raw.get("tool_name"))
    ti = raw.get("tool_input")
    if not tool or not isinstance(ti, dict):
        raise NormalizeError("payload de Codex sin tool_name o tool_input")
    cwd = _str(raw.get("cwd"))
    base = dict(host="codex", tool=tool, cwd=cwd, project_dir=cwd, session=_str(raw.get("session_id")),
                call_id=_str(raw.get("tool_use_id")), raw=raw)
    if tool == "Bash":
        return HookEvent(kind="shell", command=_shell_text(ti.get("command")), **base)
    if tool == "apply_patch":
        files, error = _patch_files(_shell_text(ti.get("command")))
        return HookEvent(kind="write", files=files, parse_error=error, **base)
    return HookEvent(kind="other", **base)


# agy 1.5.13 usa read_file / write_file / edit_file; la documentación, view_file / write_to_file / replace_file_content
AG_SHELL = {"run_command"}
AG_READ = {"read_file": "Path", "view_file": "AbsolutePath"}
AG_WRITE = {"write_file": ("Path", "Content"), "write_to_file": ("TargetFile", "CodeContent")}
AG_EDIT = {"edit_file": ("Path", "Replacement", "Target"), "replace_file_content": ("TargetFile", "ReplacementContent", "TargetContent")}
AG_MULTI = {"multi_replace_file_content": "TargetFile"}


def _chunk_text(chunk: Any) -> str:
    if isinstance(chunk, dict):
        texts = [v for k, v in chunk.items() if "Replacement" in k and isinstance(v, str)]
        if texts:
            return "\n".join(texts)
    return json.dumps(chunk, ensure_ascii=False)  # forma desconocida: todo el chunk (modo conservador)


def _antigravity(raw: dict[str, Any]) -> HookEvent:
    call = raw.get("toolCall")
    if not isinstance(call, dict) or not isinstance(call.get("args", {}), dict):
        raise NormalizeError("payload de Antigravity sin toolCall")
    tool, args = _str(call.get("name")), call.get("args") or {}
    workspaces = raw.get("workspacePaths") if isinstance(raw.get("workspacePaths"), list) else []
    root = _str(workspaces[0]) if workspaces else ""
    cwd = _str(args.get("Cwd")) or _str(raw.get("cwd")) or root
    base = dict(host="antigravity", tool=tool, cwd=cwd, project_dir=root or cwd,
                session=_str(raw.get("conversationId")), call_id=_str(call.get("id")), raw=raw)
    if tool in AG_SHELL:
        return HookEvent(kind="shell", command=_str(args.get("CommandLine")), **base)
    if tool in AG_READ:
        return HookEvent(kind="read", read_path=_str(args.get(AG_READ[tool])), **base)
    if tool in AG_WRITE:
        path_key, content_key = AG_WRITE[tool]
        return HookEvent(kind="write", files=(FileChange(_str(args.get(path_key)), "write", _str(args.get(content_key))),), **base)
    if tool in AG_EDIT:
        path_key, new_key, old_key = AG_EDIT[tool]
        change = FileChange(_str(args.get(path_key)), "edit", _str(args.get(new_key)), _str(args.get(old_key)))
        return HookEvent(kind="write", files=(change,), **base)
    if tool in AG_MULTI:
        chunks = args.get("ReplacementChunks") if isinstance(args.get("ReplacementChunks"), list) else []
        change = FileChange(_str(args.get(AG_MULTI[tool])), "edit", "\n".join(_chunk_text(c) for c in chunks))
        return HookEvent(kind="write", files=(change,), **base)
    return HookEvent(kind="other", **base)


def _opencode(raw: dict[str, Any]) -> HookEvent:
    tool, ti = _str(raw.get("tool")), raw.get("input")
    if not tool or not isinstance(ti, dict):
        raise NormalizeError("payload de OpenCode sin tool o input")
    directory = _str(raw.get("directory"))
    base = dict(host="opencode", tool=tool, project_dir=_str(raw.get("project")) or directory,
                session=_str(raw.get("sessionID")), call_id=_str(raw.get("callID")), raw=raw)
    if tool in {"shell", "bash"}:
        return HookEvent(kind="shell", command=_str(ti.get("command")), cwd=_str(ti.get("workdir")) or directory, **base)
    if tool == "read":
        return HookEvent(kind="read", read_path=_str(ti.get("path") or ti.get("filePath")), cwd=directory, **base)
    if tool == "write":
        change = FileChange(_str(ti.get("path") or ti.get("filePath")), "write", _str(ti.get("content")))
        return HookEvent(kind="write", files=(change,), cwd=directory, **base)
    if tool == "edit":
        change = FileChange(_str(ti.get("path") or ti.get("filePath")), "edit", _str(ti.get("newString")), _str(ti.get("oldString")))
        return HookEvent(kind="write", files=(change,), cwd=directory, **base)
    if tool in {"patch", "apply_patch"}:
        files, error = _patch_files(_str(ti.get("patchText")))
        return HookEvent(kind="write", files=files, parse_error=error, cwd=directory, **base)
    return HookEvent(kind="other", cwd=directory, **base)


_PS_WRITE_RE = re.compile(r"(?i)\b(Set-Content|Add-Content|Out-File)\b(?P<args>[^;|\n]*)")
_PS_ARG = r"""\s+(?:'((?:[^']|'')*)'|"((?:[^"\\]|\\.)*)"|(\S+))"""
_PS_PATH_RE = re.compile(r"(?i)-(?:LiteralPath|Path|FilePath)" + _PS_ARG)
_PS_VALUE_RE = re.compile(r"(?i)-(?:Value|InputObject)" + _PS_ARG)


def _ps_arg(match: re.Match[str] | None) -> str:
    if not match:
        return ""
    return next((g for g in match.groups() if g is not None), "").replace("''", "'")


def powershell_writes(command: str) -> tuple[FileChange, ...]:
    """Escrituras de archivo desde PowerShell (Set-Content, Add-Content, Out-File). Codex en Windows escribe así en
    vez de apply_patch (V-07): se evalúan como escrituras (protect_files y detect_secrets). Mínimo del spec 001."""
    changes: list[FileChange] = []
    for match in _PS_WRITE_RE.finditer(command):
        args = match.group("args")
        path = _ps_arg(_PS_PATH_RE.search(args))
        if not path:  # primer posicional
            positional = re.match(r"""\s+(?!-)(?:'([^']*)'|"([^"]*)"|(\S+))""", args)
            path = next((g for g in positional.groups() if g), "") if positional else ""
        if path:
            changes.append(FileChange(path, "write", _ps_arg(_PS_VALUE_RE.search(args))))
    return tuple(changes)


def detect(raw: dict[str, Any]) -> str:
    """Solo diagnóstico (synapse doctor): el host que sugiere la forma del payload."""
    if isinstance(raw.get("toolCall"), dict) or "conversationId" in raw:
        return "antigravity"
    if "turn_id" in raw:
        return "codex"
    if "sessionID" in raw and "input" in raw:
        return "opencode"
    return "claude"


# --- Combinación y emisión -----------------------------------------------------------------------------------

def combine(decisions: list[Decision]) -> Decision:
    """block > ask > allow > pass. Varios 'ask' se juntan en un motivo; los contextos se concatenan."""
    if not decisions:
        return Decision("pass", "dispatch", "sin_objeciones", "python")
    top = max(decisions, key=lambda d: RANK.get(d.verdict, 0))
    if top.verdict == "ask":
        asks = [d for d in decisions if d.verdict == "ask"]
        if len(asks) > 1:
            shown = "\n".join(f"- {d.shown or d.reason}" for d in asks)
            top = Decision("ask", top.hook, top.rule, top.decided_by, top.reason, top.target, shown, top.context)
    contexts = [d.context for d in decisions if d.context]
    if contexts and top.context != "\n".join(contexts):
        top = Decision(top.verdict, top.hook, top.rule, top.decided_by, top.reason, top.target, top.shown, "\n".join(contexts))
    return top


Emission = tuple[int, str, str]  # (exit, stdout, stderr)


def emit(host: str, decision: Decision) -> Emission:
    shown = decision.shown or decision.reason
    if host == "codex":
        if decision.verdict in {"block", "ask"}:  # ask llega aquí ya convertido en deny con su texto de aprobación
            return 0, json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                                          "permissionDecisionReason": shown}}, ensure_ascii=False), ""
        return 0, "", ""  # allow/pass: Codex rechaza un allow sin updatedInput; additionalContext pendiente de V-02
    if host == "antigravity":
        verdict = {"block": "deny", "ask": "force_ask", "allow": "auto_approve", "pass": "allow"}[decision.verdict]
        payload: dict[str, str] = {"decision": verdict}
        reason = shown if decision.verdict != "pass" else ""
        if decision.context:
            reason = f"{reason}\n{decision.context}".strip()
        if reason:
            payload["reason"] = reason
        return 0, json.dumps(payload, ensure_ascii=False), ""
    if host == "opencode":
        return 0, json.dumps({"verdict": decision.verdict, "reason": shown, "hook": decision.hook,
                              "rule": decision.rule, "target": decision.target}, ensure_ascii=False), ""
    raise ValueError(f"emit no aplica a {host}: Claude usa common.emit_decision / emit_block")


def failsafe(host: str, reason: str) -> Emission:
    """Salida ante un error interno o un payload irreconocible (D-05)."""
    if host == "antigravity":
        return 0, json.dumps({"decision": "force_ask", "reason": reason}, ensure_ascii=False), ""
    if host == "opencode":
        return 0, json.dumps({"verdict": "ask", "reason": reason, "hook": "dispatch", "rule": "hook.error", "target": ""}), ""
    return 0, "", ""  # codex: igual que Claude, error no bloqueante (queda en la traza)
