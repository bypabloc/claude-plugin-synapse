#!/usr/bin/env python3
"""Aprende de la traza de Synapse de un proyecto: qué se preguntó, qué bloqueó, qué costó Laya y qué es rutina.

  $ python3 scripts/learn_from_log.py --project ~/projects/x               # resumen por hook, regla y motor
  $ python3 scripts/learn_from_log.py --project ~/projects/x --replay      # + reevaluación con el código actual
  $ python3 scripts/learn_from_log.py --project ~/projects/x --log-dir ~/.claude/personal/logs/synapse

El resumen incluye:
  - Decisiones por hook / decisión / motor (python | laya) / regla.
  - Costo de Laya en block_dangerous: consultas, latencia p50/p95 y tiempo total, contra el del código.
  - Cada 'ask' de Laya (para revisar si fue un falso positivo).
  - Con --replay: los 'ask'/'block' y los comandos que llegaron a Laya, reevaluados con las reglas actuales
    (Laya apagada): qué cambiaría y qué seguiría llegando a Laya si se enciende.
  - Sugerencia de `routine_commands` para .claude/synapse-commands.json: los programas que más llegan a Laya
    hoy. Es una sugerencia: revisarla antes de agregarla (el archivo pide confirmación al editarlo).

La traza se lee de ${CLAUDE_CONFIG_DIR:-~/.claude}/logs/synapse (o --log-dir). Nada se escribe en ella: el
replay usa un directorio de traza descartable en ./tmp/learn/.
"""

from __future__ import annotations

import argparse
import collections
import io
import json
import os
import re
import shlex
import shutil
import sys
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "hooks"))

import common  # noqa: E402

# Programas que nunca se sugieren como rutina: pueden borrar, escalar privilegios o salir a la red
NEVER_ROUTINE = {
    "rm", "rmdir", "shred", "unlink", "dd", "mkfs", "sudo", "doas", "su", "curl", "wget", "nc", "ncat", "ssh",
    "scp", "rsync", "sh", "bash", "zsh", "eval", "exec", "chmod", "chown", "kill", "pkill", "docker", "kubectl",
    "aws", "terraform", "psql", "mysql", "mv", "cp", "tee", "sed", "perl", "awk", "python", "python3", "node",
    "bun", "npx", "bunx", "uv", "pip", "npm", "pnpm", "yarn", "git", "gh", "claude", "for", "while", "until",
    "if", "then", "do", "done", "fi", "echo", "printf", "cd", "export", "timeout", "set", "else", "elif", "case",
    "esac", "exit", "continue", "break", "unset", "wait", "source", "xargs", "find", "env", "script", "uvx",
    "mkdir",
}


PROGRAM_RE = re.compile(r"[a-z][a-z0-9._-]*")


def load_runs(log_dir: Path, project: str) -> dict[str, dict[str, Any]]:
    """run -> {input, steps} de las ejecuciones cuyo cwd cae dentro del proyecto (incluye <proyecto>-worktrees)."""
    prefix = project.rstrip("/")
    runs: dict[str, dict[str, Any]] = {}
    for path in sorted(log_dir.glob("*.jsonl")):
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                row = common.parse_log_line(line)
                if not row:
                    continue
                rid = row.get("run")
                if row.get("step") == "input":
                    if str(row.get("cwd", "")).startswith(prefix):
                        runs[rid] = {"input": row, "steps": []}
                elif rid in runs:
                    runs[rid]["steps"].append(row)
    return runs


def decision_of(run: dict[str, Any]) -> dict[str, Any] | None:
    return next((s for s in run["steps"] if s.get("step") == "decision"), None)


def percentile(values: list[float], q: float) -> float:
    return sorted(values)[min(len(values) - 1, int(len(values) * q))] if values else 0.0


def summarize(runs: dict[str, dict[str, Any]]) -> dict[str, Any]:
    by_rule: collections.Counter[tuple[str, str, str, str]] = collections.Counter()
    ms: dict[str, list[float]] = {"laya": [], "python": []}
    laya_asks: list[dict[str, Any]] = []
    consulted = 0
    for run in runs.values():
        d = decision_of(run)
        if not d:
            continue
        hook = run["input"].get("hook", "?")
        by_rule[(hook, d.get("decision", "?"), d.get("decided_by", "?"), d.get("rule", "?"))] += 1
        if any(s.get("step") == "laya" and s.get("result") in ("ok", "flagged") for s in run["steps"]):
            consulted += 1
        if hook == "block_dangerous" and isinstance(d.get("ms"), (int, float)):
            ms.setdefault(d.get("decided_by", "python"), []).append(float(d["ms"]))
        if d.get("decision") == "ask" and d.get("decided_by") == "laya":
            target = run["input"].get("command") or run["input"].get("file_path") or ""
            laya_asks.append({"hook": hook, "target": target, "evidence": d.get("evidence")})
    latency = {
        k: {"n": len(v), "p50_ms": round(percentile(v, 0.5), 1), "p95_ms": round(percentile(v, 0.95), 1), "total_s": round(sum(v) / 1000)}
        for k, v in ms.items() if v
    }
    return {"runs": len(runs), "laya_consulted": consulted, "by_rule": by_rule, "latency": latency, "laya_asks": laya_asks}


def replay(runs: dict[str, dict[str, Any]], project: str, limit: int) -> list[dict[str, Any]]:
    """Reevalúa con el código actual (sin Laya) los comandos que preguntaron, bloquearon o llegaron a Laya."""
    import block_dangerous as bd

    log_dir = ROOT / "tmp" / "learn" / "trace"
    log_dir.mkdir(parents=True, exist_ok=True)
    saved_env = {k: os.environ.get(k) for k in ("SYNAPSE_LOG_DIR", "CLAUDE_PROJECT_DIR")}
    saved_switch = bd.should_use_laya
    os.environ["SYNAPSE_LOG_DIR"] = str(log_dir)
    os.environ["CLAUDE_PROJECT_DIR"] = project
    reached: list[bool] = []

    def fake_should_use_laya(hook: str | None = None) -> bool:
        reached.append(True)
        return False

    bd.should_use_laya = fake_should_use_laya  # type: ignore[assignment]
    try:
        return _replay_runs(bd, runs, project, limit, reached)
    finally:
        bd.should_use_laya = saved_switch  # type: ignore[assignment]
        for key, value in saved_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _replay_runs(bd: Any, runs: dict[str, dict[str, Any]], project: str, limit: int, reached: list[bool]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    results: list[dict[str, Any]] = []
    for run in runs.values():
        if run["input"].get("hook") != "block_dangerous":
            continue
        d = decision_of(run) or {}
        was_laya = any(s.get("step") == "laya" and s.get("result") in ("ok", "flagged") for s in run["steps"])
        command = str(run["input"].get("command", ""))
        if command in seen or not (d.get("decision") in ("ask", "block") or was_laya):
            continue
        seen.add(command)
        cwd = str(run["input"].get("cwd") or project)
        cwd_missing = not os.path.isdir(cwd)
        if cwd_missing:
            cwd = project  # worktree ya borrado: las rutas relativas se resuelven distinto, se reporta aparte
        reached.clear()
        out, err = io.StringIO(), io.StringIO()
        code = 0
        with redirect_stdout(out), redirect_stderr(err):
            try:
                common.start_trace("block_dangerous", {"tool_name": "Bash", "tool_input": {"command": command}, "cwd": cwd})
                bd.evaluate_command_safety(command, cwd)
            except SystemExit as exc:
                code = exc.code if isinstance(exc.code, int) else 0
            except Exception as exc:  # noqa: BLE001 - el replay no debe cortarse por un comando raro
                err.write(repr(exc))
                code = -1
        now = "block" if code == 2 else ("error" if code == -1 else (json.loads(out.getvalue())["hookSpecificOutput"]["permissionDecision"] if out.getvalue() else "pass"))
        before = d.get("decision", "?") if not (d.get("decision") == "pass" and was_laya) else "pass(laya)"
        if d.get("decision") == "ask" and d.get("decided_by") == "laya":
            before = "ask(laya)"
        after = "pass(laya)" if now == "pass" and reached else now
        results.append({"command": command, "before": before, "rule_before": d.get("rule"), "after": after, "cwd_missing": cwd_missing})
        if limit and len(results) >= limit:
            break
    return results


def heads_reaching_laya(commands: list[str]) -> collections.Counter[str]:
    """Programa (+ sub-comando) de cada sub-comando no rutinario de los comandos que siguen llegando a Laya."""
    import block_dangerous as bd

    heads: collections.Counter[str] = collections.Counter()
    for command in commands:
        shell = "\n".join(bd.split_heredocs(command)[0])  # los cuerpos de heredoc no son comandos
        for sc in common.extract_bash_tokens(shell):
            try:
                tokens = bd._strip_wrappers(shlex.split(sc.lstrip("("), posix=True))
            except ValueError:
                continue
            if not tokens or not PROGRAM_RE.fullmatch(tokens[0]) or tokens[0] in NEVER_ROUTINE or not shutil.which(tokens[0]):
                continue
            if tokens[0] in bd.READ_ONLY_CMDS or tokens[0] in common.COMMAND_WRAPPERS or bd.is_dev_subcommand(tokens, None, (), []):
                continue
            sub = tokens[1] if len(tokens) > 1 and PROGRAM_RE.fullmatch(tokens[1]) else ""
            heads[f"{tokens[0]} {sub}".strip()] += 1
    return heads


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--project", required=True, help="Raíz del proyecto (prefijo del cwd en la traza)")
    ap.add_argument("--log-dir", default=str(common.get_log_dir()), help="Carpeta de la traza JSONL")
    ap.add_argument("--replay", action="store_true", help="Reevaluar con las reglas actuales")
    ap.add_argument("--limit", type=int, default=0, help="Máximo de comandos a reevaluar (0 = todos)")
    ap.add_argument("--min-count", type=int, default=5, help="Mínimo de apariciones para sugerir una rutina")
    ap.add_argument("--json", action="store_true", help="Salida JSON")
    args = ap.parse_args()

    project = os.path.realpath(os.path.expanduser(args.project))
    log_dir = Path(os.path.expanduser(args.log_dir))
    if log_dir.parts[-2:] == ("logs", "synapse"):
        # La memoria de Claude del proyecto (raíz permitida) vive en el mismo CLAUDE_CONFIG_DIR que la traza
        os.environ["CLAUDE_CONFIG_DIR"] = str(log_dir.parent.parent)
    runs = load_runs(log_dir, project)
    summary = summarize(runs)
    report: dict[str, Any] = {
        "project": project,
        "runs": summary["runs"],
        "laya_consulted": summary["laya_consulted"],
        "latency_block_dangerous": summary["latency"],
        "decisions": [{"hook": h, "decision": d, "decided_by": b, "rule": r, "count": n} for (h, d, b, r), n in summary["by_rule"].most_common()],
        "laya_asks": summary["laya_asks"],
    }
    if args.replay:
        all_results = replay(runs, project, args.limit)
        results = [r for r in all_results if not r["cwd_missing"]]
        changes = collections.Counter((r["before"], r["after"]) for r in results)
        still_laya = [r["command"] for r in results if r["after"] == "pass(laya)"]
        suggestions = [h for h, n in heads_reaching_laya(still_laya).most_common(30) if n >= args.min_count]
        report["replay"] = {
            "evaluated": len(results),
            "skipped_cwd_missing": len(all_results) - len(results),
            "transitions": [{"before": b, "after": a, "count": n} for (b, a), n in changes.most_common()],
            "changed": [r for r in results if r["before"] != r["after"]],
            "still_reaching_laya": len(still_laya),
        }
        report["suggested_synapse_commands"] = {"routine_commands": suggestions}

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=1))
        return
    print(f"Proyecto: {project}\nEjecuciones: {report['runs']} · Laya consultada: {report['laya_consulted']}")
    for engine, stats in report["latency_block_dangerous"].items():
        print(f"  block_dangerous ({engine}): n={stats['n']} p50={stats['p50_ms']} ms p95={stats['p95_ms']} ms total={stats['total_s']} s")
    print("\nDecisiones (hook · decisión · motor · regla):")
    for row in report["decisions"]:
        if row["decision"] != "pass":
            print(f"  {row['count']:>6}  {row['hook']} · {row['decision']} · {row['decided_by']} · {row['rule']}")
    print(f"\n'ask' de Laya ({len(report['laya_asks'])}):")
    for ask in report["laya_asks"]:
        print(f"  [{ask['hook']}] {ask['target'][:160]}")
    if args.replay:
        rep = report["replay"]
        print(f"\nReplay con las reglas actuales ({rep['evaluated']} comandos; {rep['skipped_cwd_missing']} omitidos porque su cwd ya no existe):")
        for t in rep["transitions"]:
            print(f"  {t['count']:>6}  {t['before']} -> {t['after']}")
        print(f"  Seguirían llegando a Laya (si se enciende): {rep['still_reaching_laya']}")
        print("\nSugerencia para .claude/synapse-commands.json (revisar antes de usar):")
        print(json.dumps(report["suggested_synapse_commands"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
