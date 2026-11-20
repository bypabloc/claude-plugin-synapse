#!/usr/bin/env python3
"""CLI de usuario de Synapse (spec 001 · 02 §10).

  synapse.py approve <id>          aprueba UNA ejecución de un 'ask' de Codex (exige una terminal interactiva)
  synapse.py approve --list        aprobaciones pendientes y vigentes
  synapse.py doctor [--host H] [--payload archivo.json]   intérprete, manifiestos, Laya, logs y CLI de cada host
  synapse.py log [args…]           traza legible (scripts/synapse_log.py; --host codex|antigravity|opencode)
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "hooks"))

import approvals  # noqa: E402
import common  # noqa: E402
import hosts  # noqa: E402
import laya_daemon  # noqa: E402

MIN_VERSIONS = {"claude": "2.1.287", "codex": "0.171.0", "agy": "1.5.4", "opencode": "2.3.0"}
HOST_CLI = {"claude": "claude", "codex": "codex", "antigravity": "agy", "opencode": "opencode"}
MANIFESTS = {
    "claude": [".claude-plugin/plugin.json", "hooks/hooks.json"],
    "codex": [".codex-plugin/plugin.json", "adapters/codex/hooks.json"],
    "antigravity": [".antigravity-plugin/plugin.json", "hooks.json", "hooks/run-hook.cmd"],
    "opencode": ["package.json", "adapters/opencode/index.ts"],
}


def _version(text: str) -> tuple[int, ...]:
    match = re.search(r"(\d+)\.(\d+)\.(\d+)", text)
    return tuple(int(p) for p in match.groups()) if match else ()


def _line(ok: bool | None, text: str) -> bool:
    print(f"{'✓' if ok else ('!' if ok is None else '✗')} {text}")
    return ok is not False


def doctor(host: str | None, payload: str | None) -> int:
    ok = _line(sys.version_info >= (3, 10), f"Python {sys.version.split()[0]} (mínimo 3.10)")
    launcher = ROOT / "hooks" / "run-hook.cmd"
    ok &= _line(launcher.exists() and (os.name == "nt" or os.access(launcher, os.X_OK)), f"lanzador {launcher.name} ejecutable")
    for name in ([host] if host else list(MANIFESTS)):
        missing = [m for m in MANIFESTS.get(name, []) if not (ROOT / m).exists()]
        ok &= _line(not missing, f"{name}: manifiestos {'completos' if not missing else 'faltan ' + ', '.join(missing)}")
        cli = MIN_VERSIONS[HOST_CLI[name]] if HOST_CLI[name] in MIN_VERSIONS else None
        binary = shutil.which(HOST_CLI[name])
        if not binary:
            _line(None, f"{name}: `{HOST_CLI[name]}` no está en el PATH")
            continue
        try:
            out = subprocess.run([binary, "--version"], capture_output=True, text=True, timeout=20).stdout
        except (OSError, subprocess.SubprocessError):
            out = ""
        found = _version(out)
        ok &= _line(bool(found) and found >= _version(cli or "0.0.0"),
                    f"{name}: {HOST_CLI[name]} {'.'.join(map(str, found)) or '?'} (mínimo {cli})")
    delta = ROOT / "models" / "laya-block-dangerous-delta.safetensors"
    if delta.exists():
        head = delta.read_bytes()[:40]
        pointer = head.startswith(b"version https://git-lfs")
        _line(None if pointer else True, "delta de Laya: " + ("puntero LFS (corre `git lfs pull` en la carpeta del plugin; "
                                                               "Laya usa el perfil zeroshot)" if pointer else "contenido real"))
    _line(None, f"Laya: {common.laya_skip_reason() if not common.should_use_laya() else 'encendida'}; "
                f"daemon {'activo' if laya_daemon.socket_path('gpu').exists() else 'detenido'}")
    for label, directory in (("Claude Code", common.claude_log_dir()), ("otros hosts", common.state_dir() / "logs")):
        try:
            directory.mkdir(parents=True, exist_ok=True)
            writable = os.access(directory, os.W_OK)
        except OSError:
            writable = False
        ok &= _line(writable, f"logs de {label}: {directory}")
    if payload:
        raw = json.loads(Path(payload).read_text(encoding="utf-8"))
        guessed = hosts.detect(raw.get("stdin", raw))
        ok &= _line(host is None or guessed == host, f"el payload parece de {guessed}" + (f" (declarado: {host})" if host else ""))
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["log"]:
        import synapse_log  # noqa: PLC0415

        sys.argv = ["synapse_log.py", *argv[1:]]
        synapse_log.main()
        return 0
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    ap = sub.add_parser("approve", help="aprobar un 'ask' de Codex")
    ap.add_argument("id", nargs="?")
    ap.add_argument("--list", action="store_true")
    dp = sub.add_parser("doctor", help="diagnóstico de la instalación")
    dp.add_argument("--host", choices=list(MANIFESTS))
    dp.add_argument("--payload")
    args = parser.parse_args(argv)
    if args.cmd == "approve":
        if args.list or not args.id:
            rows = approvals.pending_list()
            for row in rows:
                state = "aprobada" if row.get("approved") else "pendiente"
                print(f"{row['id']}  {state:9}  {row['hook']} · {row['rule']}  {row['preview'][:80]}")
            if not rows:
                print("No hay aprobaciones pendientes.")
            return 0
        return approvals.approve(args.id)
    return doctor(args.host, args.payload)


if __name__ == "__main__":
    sys.path.insert(0, str(ROOT / "scripts"))
    sys.exit(main())
