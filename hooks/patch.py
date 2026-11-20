"""Parser del formato `*** Begin Patch` de Codex (`apply_patch`) y OpenCode (`patch.patchText`) (spec 001 · 02 §4.3).

Cada archivo tocado es un FileChange: Add File (contenido = líneas '+'), Update File (líneas '+' y '-', con
Move to opcional) y Delete File. Las líneas '@@' y de contexto se ignoran. Un patch sin '*** Begin Patch' es
un error de parseo: el llamador decide qué hacer (dispatch lo evalúa como texto completo).
"""

from __future__ import annotations

from dataclasses import dataclass, field


class PatchError(ValueError):
    pass


@dataclass
class _Draft:
    path: str
    op: str
    added: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    move_to: str | None = None


def parse(text: str) -> list[tuple[str, str, str, str, str | None]]:
    """Devuelve (path, op, new_text, old_text, move_to) por archivo, en orden de aparición."""
    lines = text.replace("\r\n", "\n").split("\n")
    start = next((i for i, line in enumerate(lines) if line.strip() == "*** Begin Patch"), None)
    if start is None:
        raise PatchError("falta '*** Begin Patch'")
    drafts: list[_Draft] = []
    current: _Draft | None = None
    for line in lines[start + 1 :]:
        if line.strip() == "*** End Patch":
            break
        header = _header(line)
        if header is not None:
            kind, value = header
            if kind == "move":
                if current is None or current.op != "update":
                    raise PatchError("'*** Move to:' fuera de un Update File")
                current.move_to = value
                continue
            current = _Draft(value, kind)
            drafts.append(current)
            continue
        if current is None:
            continue
        if line.startswith("+") and current.op in {"add", "update"}:
            current.added.append(line[1:])
        elif line.startswith("-") and current.op == "update":
            current.removed.append(line[1:])
    if not drafts:
        raise PatchError("el patch no toca ningún archivo")
    return [(d.path, d.op, "\n".join(d.added), "\n".join(d.removed), d.move_to) for d in drafts]


def _header(line: str) -> tuple[str, str] | None:
    for prefix, kind in (("*** Add File:", "add"), ("*** Update File:", "update"),
                         ("*** Delete File:", "delete"), ("*** Move to:", "move")):
        if line.startswith(prefix):
            value = line[len(prefix):].strip()
            if not value:
                raise PatchError(f"'{prefix}' sin ruta")
            return kind, value
    return None
