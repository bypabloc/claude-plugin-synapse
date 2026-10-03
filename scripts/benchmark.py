#!/usr/bin/env python3
"""Benchmark de Synapse: calidad, carga de Laya, latencia de los hooks y uso de CPU/GPU.

Se puede ejecutar cuando se quiera desde la raíz del proyecto (no depende de una sesión de Claude Code):

  $ python3 scripts/benchmark.py                  # todo: traza real, banco de evaluación, latencia
  $ python3 scripts/benchmark.py --runs 30        # más repeticiones por caso de latencia
  $ python3 scripts/benchmark.py --trace ~/.claude/personal/logs/synapse/2026-10-03.jsonl
  $ python3 scripts/benchmark.py --skip-latency   # solo métricas offline (no levanta el daemon)

Secciones:
  1. Tráfico real: cuántas consultas a Laya de la traza ya no ocurren porque las resuelve el código.
  2. Banco de evaluación (tmp/eval, ver scripts/laya_eval.py): FPR en comandos reales y recall en
     ShellRisk-Bench, antes y después de la capa determinista, con las inferencias cacheadas del perfil
     de producción. Mide si saltarse Laya en comandos de solo lectura pierde comandos riesgosos.
  3. Latencia por hook ejecutado como lo hace Claude Code (un proceso por llamada), en fallback y con el
     daemon de Laya: arranque en frío, en caliente y en ráfaga concurrente.
  4. CPU y GPU durante cada fase: % de uso y temperatura (promedio y máximo).

Los resultados también se guardan en tmp/benchmark/resultados-AAAAMMDD-HHMMSS.json.
"""

from __future__ import annotations

import argparse
import glob
import json
import math
import os
import re
import statistics
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
HOOKS = ROOT / "hooks"
sys.path.insert(0, str(HOOKS))

import block_dangerous as bd  # noqa: E402
import block_env_read as ber  # noqa: E402
import common  # noqa: E402
import laya_daemon  # noqa: E402
import protect_files as pf  # noqa: E402

OUT_DIR = ROOT / "tmp" / "benchmark"
EVAL_DIR = ROOT / "tmp" / "eval"


# ---------------------------------------------------------------- hardware: % y temperatura


def _cpu_times() -> tuple[int, int]:
    fields = [int(v) for v in Path("/proc/stat").read_text().splitlines()[0].split()[1:]]
    idle = fields[3] + fields[4]
    return sum(fields) - idle, sum(fields)


def cpu_temperature() -> float | None:
    """°C de la CPU si el sistema lo expone. WSL2 normalmente no: se intenta sysfs, hwmon y WMI de Windows."""
    for path in glob.glob("/sys/class/thermal/thermal_zone*/temp") + glob.glob("/sys/class/hwmon/hwmon*/temp*_input"):
        try:
            return int(Path(path).read_text()) / 1000
        except (OSError, ValueError):
            continue
    return None


def _wmi_cpu_temperature() -> float | None:
    try:
        out = subprocess.run(
            ["powershell.exe", "-NoProfile", "-Command",
             "(Get-CimInstance -Namespace root/wmi -ClassName MSAcpi_ThermalZoneTemperature).CurrentTemperature"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, errors="replace", timeout=10,
        ).stdout.split()
        return int(out[0]) / 10 - 273.15 if out and out[0].isdigit() else None
    except (OSError, subprocess.SubprocessError):
        return None


def gpu_sample() -> dict[str, float] | None:
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=utilization.gpu,temperature.gpu,memory.used,power.draw", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5,
        ).stdout.strip().splitlines()[0]
        util, temp, mem, power = (float(v) if v.strip() not in {"[N/A]", "N/A"} else math.nan for v in out.split(","))
        return {"util": util, "temp": temp, "mem_mib": mem, "power_w": power}
    except (OSError, subprocess.SubprocessError, IndexError, ValueError):
        return None


class HardwareMonitor:
    """Muestrea CPU y GPU cada `interval` segundos mientras dura un bloque `with`."""

    def __init__(self, interval: float = 0.5):
        self.interval = interval
        self.samples: list[dict[str, float]] = []
        self._stop = threading.Event()
        self._wmi_temp: float | None = None

    def __enter__(self) -> HardwareMonitor:
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *_: object) -> None:
        self._stop.set()
        self._thread.join()

    def _run(self) -> None:
        last = _cpu_times()
        while not self._stop.wait(self.interval):
            last = self._sample(last)
        self._sample(last)  # fases más cortas que el intervalo (ráfaga) también quedan medidas

    def _sample(self, last: tuple[int, int]) -> tuple[int, int]:
        now = _cpu_times()
        sample = {"cpu": 100 * (now[0] - last[0]) / max(1, now[1] - last[1])}
        temp = cpu_temperature()
        if temp is not None:
            sample["cpu_temp"] = temp
        gpu = gpu_sample()
        if gpu:
            sample.update({f"gpu_{k}": v for k, v in gpu.items()})
        self.samples.append(sample)
        return now

    def summary(self) -> dict[str, Any]:
        def stat(key: str) -> dict[str, float] | None:
            values = [s[key] for s in self.samples if key in s and not math.isnan(s[key])]
            return {"avg": round(statistics.mean(values), 1), "max": round(max(values), 1)} if values else None

        return {k: stat(k) for k in ("cpu", "cpu_temp", "gpu_util", "gpu_temp", "gpu_mem_mib", "gpu_power_w")}


# ---------------------------------------------------------------- 1. tráfico real


def _old_laya_runs(records: list[dict]) -> dict[str, dict]:
    runs: dict[str, dict] = {}
    for r in records:
        run = runs.setdefault(r["run"], {"hook": r["hook"], "laya": False})
        if r["step"] == "input":
            run["input"] = r
        if r["step"] == "laya" and r.get("result") != "skipped":
            run["laya"] = True
    return {k: v for k, v in runs.items() if v["laya"] and "input" in v}


def deterministic_hit(cmd: str) -> bool:
    """Bloqueo o confirmación por regla (catastrófico, ask, escribir desde Bash): Laya no llega a correr."""
    return any(re.search(p, cmd) for p in bd.CATASTROPHIC_PATTERNS + bd.ASK_PATTERNS) or bool(bd.agent_authored_write(cmd))


def would_call_laya_now(hook: str, inp: dict) -> bool | None:
    """Si el código actual seguiría consultando a Laya con esta entrada (None: la traza no guarda el contenido)."""
    if hook == "block_dangerous":
        cmd, cwd = inp.get("command", ""), inp.get("cwd") or None
        # La traza no guarda CLAUDE_PROJECT_DIR: se aproxima con el entorno git del cwd (si aún existe)
        roots = common._roots_for(os.path.realpath(cwd)) if cwd and os.path.isdir(cwd) else ()
        return not (bd.is_routine(cmd) or bd.is_read_only(cmd, cwd if roots else None, roots) or deterministic_hit(cmd))
    if hook == "protect_files":
        path = inp.get("file_path", "")
        return not pf.CREDENTIAL_FILE_RE.search(path) and bool(pf.SENSITIVE_NAME_RE.search(os.path.basename(path)))
    if hook == "block_env_read":
        cmd = inp.get("command", "")
        mentions, body = ber.env_mentions(cmd), ber.embedded_env_refs(cmd)
        if not mentions and not body:
            return False
        return not (mentions and not body and all(ber._is_safe_env_use(m) for m in mentions))
    return None


def real_traffic(paths: list[Path]) -> list[dict]:
    records = [r for p in paths for r in map(common.parse_log_line, p.read_text(errors="replace").splitlines()) if r]
    runs = _old_laya_runs(records)
    rows = []
    for hook in ("block_dangerous", "protect_files", "block_env_read", "detect_secrets"):
        hook_runs = [r for r in runs.values() if r["hook"] == hook]
        verdicts = [would_call_laya_now(hook, r["input"]) for r in hook_runs]
        known = [v for v in verdicts if v is not None]
        rows.append({
            "hook": hook,
            "laya_antes": len(hook_runs),
            "laya_ahora": sum(known) if known else None,
            "evitadas_pct": round(100 * (1 - sum(known) / len(known)), 1) if known else None,
        })
    return rows


# ---------------------------------------------------------------- 2. banco de evaluación


def eval_bench() -> dict[str, Any] | None:
    dataset = EVAL_DIR / "dataset.jsonl"
    policy = bd.load_laya_policy()
    profile = policy["profiles"]["finetuned"]
    cache = EVAL_DIR / "cache" / f"{profile['variant']}.jsonl"
    if not dataset.exists() or not cache.exists():
        return None
    scores = {r["id"]: r["probs"] for r in map(json.loads, open(cache))}
    w, thr = profile["weights"], profile["threshold"]

    def laya_flag(probs: dict) -> bool:
        feats = [math.log(max(probs[q][o], 1e-6)) for q in sorted(probs) for o in sorted(probs[q])] + [1.0]
        return 1 / (1 + math.exp(-sum(a * b for a, b in zip(w, feats)))) >= thr

    rows = [json.loads(line) for line in open(dataset)]
    ev = [r for r in rows if r["split"] == "eval" and r["id"] in scores]
    # FPR como en laya_eval: 'ask' de Laya sobre comandos reales que las reglas no resuelven ya
    stats = {"antes": {"tp": 0, "fp_real": 0, "laya": 0}, "despues": {"tp": 0, "fp_real": 0, "laya": 0}}
    lost: list[str] = []
    n_real = n_risky = 0
    started = time.perf_counter()
    for r in ev:
        cmd = r["command"]
        det = deterministic_hit(cmd)
        skip = bd.is_routine(cmd) or bd.is_read_only(cmd)
        flag = laya_flag(scores[r["id"]])
        before = det or flag
        after = det or (not skip and flag)
        for name, flagged, called in (("antes", before, not det), ("despues", after, not det and not skip)):
            stats[name]["laya"] += called
            if r["label"]:
                stats[name]["tp"] += flagged
            elif r["source"] == "real" and not det:
                stats[name]["fp_real"] += flagged
        n_risky += r["label"]
        n_real += r["source"] == "real" and not r["label"] and not det
        if r["label"] and before and not after:
            lost.append(cmd[:120])
    gate_ms = 1000 * (time.perf_counter() - started) / max(1, len(ev))
    return {
        "variante": profile["variant"],
        "n_eval": len(ev),
        "n_real": n_real,
        "n_riesgosos": n_risky,
        "gate_ms_por_comando": round(gate_ms, 3),
        **{name: {
            "recall": round(s["tp"] / max(1, n_risky), 4),
            "fpr_real": round(s["fp_real"] / max(1, n_real), 4),
            "consultas_laya": s["laya"],
        } for name, s in stats.items()},
        "riesgosos_perdidos": lost,
    }


# ---------------------------------------------------------------- 3. latencia


FAKE_TOKEN = "auth_token = '" + "f3K9xQ2mL7pR" + "4tW8zB1nV6cY'"
CASES: list[tuple[str, str, str, dict]] = [
    ("block_dangerous", "solo lectura", "Bash", {"command": "rg -n 'def ' hooks | head -20 && git log --oneline -5"}),
    ("block_dangerous", "ambiguo (Laya)", "Bash", {"command": "python3 scripts/migrate.py --dry-run"}),
    ("block_env_read", "os.environ", "Bash", {"command": "rg -n 'os.environ' hooks"}),
    ("block_env_read", ".env ambiguo (Laya)", "Bash", {"command": "python3 tools/show.py .env.local"}),
    ("protect_files", "código fuente", "Write", {"file_path": "src/services/payment.py", "content": "x = 1\n"}),
    ("protect_files", "nombre sensible (Laya)", "Write", {"file_path": "src/config/secret_settings.py", "content": "x"}),
    ("detect_secrets", "sin literales", "Write", {"file_path": "a.py", "content": "def refresh_token(key):\n    return auth(key)\n"}),
    ("detect_secrets", "literal candidato (Laya)", "Write", {"file_path": "a.py", "content": FAKE_TOKEN + "\n"}),
]


def _pct(values: list[float], p: float) -> float:
    values = sorted(values)
    return round(values[min(len(values) - 1, int(p * len(values)))], 1)


def _invoke(hook: str, tool: str, tool_input: dict, flag: str, env: dict[str, str]) -> tuple[float, int]:
    payload = {"session_id": "benchmark", "tool_name": tool, "tool_input": tool_input, "cwd": str(ROOT)}
    started = time.perf_counter()
    proc = subprocess.run(["python3", str(HOOKS / f"{hook}.py"), flag], input=json.dumps(payload), text=True, capture_output=True, env=env, timeout=120)
    return 1000 * (time.perf_counter() - started), proc.returncode


def latency(runs: int, burst: int) -> dict[str, Any]:
    stamp = datetime.now().strftime("%H%M%S")
    work = OUT_DIR / f"run-{stamp}"
    env = {**os.environ, "CLAUDE_CONFIG_DIR": str(work / "claude"), "CLAUDE_PROJECT_DIR": str(ROOT),
           "SYNAPSE_RUNTIME_DIR": str(OUT_DIR / "rt")}
    env.pop("SYNAPSE_LOG_DIR", None)
    os.environ["SYNAPSE_RUNTIME_DIR"] = env["SYNAPSE_RUNTIME_DIR"]
    results: dict[str, Any] = {"casos": [], "fases": {}}

    with HardwareMonitor() as hw:
        time.sleep(2)
    results["fases"]["reposo"] = hw.summary()

    with HardwareMonitor() as hw:
        started = time.perf_counter()
        hook, _, tool, tool_input = CASES[1]
        _invoke(hook, tool, tool_input, "--gpu", env)
        results["arranque_en_frio_ms"] = round(1000 * (time.perf_counter() - started), 1)
    results["fases"]["arranque_en_frio"] = hw.summary()

    for flag, phase in (("--fallback", "fallback"), ("--gpu", "laya")):
        with HardwareMonitor() as hw:
            for hook, label, tool, tool_input in CASES:
                walls = [_invoke(hook, tool, tool_input, flag, env)[0] for _ in range(runs)]
                results["casos"].append({"modo": phase, "hook": hook, "caso": label, "p50_ms": _pct(walls, 0.5),
                                         "p95_ms": _pct(walls, 0.95), "max_ms": round(max(walls), 1)})
        results["fases"][phase] = hw.summary()

    with HardwareMonitor() as hw:
        calls = [CASES[i % len(CASES)] for i in range(burst)]
        started = time.perf_counter()
        with ThreadPoolExecutor(max_workers=burst) as pool:
            walls = list(pool.map(lambda c: _invoke(c[0], c[2], c[3], "--gpu", env)[0], calls))
        results["rafaga"] = {"concurrentes": burst, "total_ms": round(1000 * (time.perf_counter() - started), 1),
                             "p50_ms": _pct(walls, 0.5), "max_ms": round(max(walls), 1)}
    results["fases"]["rafaga"] = hw.summary()

    laya_ms = [r["laya_ms"] for f in (work / "claude" / "logs" / "synapse").glob("*.jsonl")
               for r in map(common.parse_log_line, f.read_text().splitlines()) if r and "laya_ms" in r][1:]  # sin el arranque en frío
    results["laya_inferencia_ms"] = {"n": len(laya_ms), "p50": _pct(laya_ms, 0.5), "p95": _pct(laya_ms, 0.95)} if laya_ms else None
    try:
        laya_daemon.send(laya_daemon.socket_path("gpu"), {"op": "shutdown"}, timeout=5)
    except OSError:
        pass
    return results


# ---------------------------------------------------------------- salida


def table(headers: list[str], rows: list[list[Any]]) -> str:
    cells = [[("—" if v is None else str(v)) for v in row] for row in rows]
    return "\n".join(["| " + " | ".join(headers) + " |", "|" + "|".join(" --- " for _ in headers) + "|"] + ["| " + " | ".join(r) + " |" for r in cells])


def hw_cell(stat: dict[str, float] | None, unit: str) -> str:
    return "—" if not stat else f"{stat['avg']}{unit} (máx {stat['max']}{unit})"


def default_traces() -> list[Path]:
    files = sorted(common.get_log_dir().glob("*.jsonl"))
    return files[-2:]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--trace", nargs="*", type=Path, help="trazas JSONL de Synapse (default: últimos 2 días)")
    parser.add_argument("--runs", type=int, default=15, help="repeticiones por caso de latencia")
    parser.add_argument("--burst", type=int, default=8, help="hooks concurrentes en la ráfaga")
    parser.add_argument("--skip-latency", action="store_true")
    args = parser.parse_args()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    report: dict[str, Any] = {"fecha": datetime.now().isoformat(timespec="seconds")}

    traces = [p.expanduser() for p in (args.trace or default_traces())]
    print(f"\n## 1. Tráfico real ({', '.join(p.name for p in traces) or 'sin trazas'})\n")
    if traces:
        report["trafico_real"] = real_traffic(traces)
        print(table(["hook", "consultas a Laya (antes)", "seguirían consultando", "evitadas"],
                    [[r["hook"], r["laya_antes"], r["laya_ahora"], None if r["evitadas_pct"] is None else f"{r['evitadas_pct']}%"] for r in report["trafico_real"]]))
        print("\n(detect_secrets: la traza no guarda el contenido escrito, no se puede re-evaluar)")

    print("\n## 2. Banco de evaluación (comandos reales + ShellRisk-Bench)\n")
    bench = eval_bench()
    report["banco"] = bench
    if bench:
        print(table(["", "recall riesgosos", "FPR comandos reales", "consultas a Laya"],
                    [[name, f"{bench[name]['recall']:.2%}", f"{bench[name]['fpr_real']:.2%}", bench[name]["consultas_laya"]] for name in ("antes", "despues")]))
        print(f"\n{bench['n_eval']} comandos de evaluación ({bench['n_real']} reales, {bench['n_riesgosos']} riesgosos), "
              f"variante {bench['variante']}; capa determinista: {bench['gate_ms_por_comando']} ms por comando.")
        print(f"Riesgosos que Laya marcaba y la capa determinista ahora deja pasar: {len(bench['riesgosos_perdidos'])}")
        for cmd in bench["riesgosos_perdidos"][:10]:
            print(f"  - {cmd}")
    else:
        print("sin datos: corre `python3 scripts/laya_eval.py dataset` y `score` antes")

    if not args.skip_latency:
        print(f"\n## 3. Latencia por hook (un proceso por llamada, {args.runs} repeticiones)\n")
        lat = latency(args.runs, args.burst)
        report["latencia"] = lat
        print(table(["modo", "hook", "caso", "p50 ms", "p95 ms", "máx ms"],
                    [[c["modo"], c["hook"], c["caso"], c["p50_ms"], c["p95_ms"], c["max_ms"]] for c in lat["casos"]]))
        print(f"\nArranque en frío del daemon (primer hook): {lat['arranque_en_frio_ms']} ms")
        if lat["laya_inferencia_ms"]:
            li = lat["laya_inferencia_ms"]
            print(f"Inferencia de Laya dentro del hook: p50 {li['p50']} ms, p95 {li['p95']} ms (n={li['n']})")
        r = lat["rafaga"]
        print(f"Ráfaga de {r['concurrentes']} hooks concurrentes: total {r['total_ms']} ms, p50 {r['p50_ms']} ms, máx {r['max_ms']} ms")

        print("\n## 4. CPU y GPU por fase (promedio y máximo)\n")
        cpu_temp_note = None
        if all(not f["cpu_temp"] for f in lat["fases"].values()):
            wmi = _wmi_cpu_temperature()
            cpu_temp_note = f"{wmi:.1f} °C (WMI, una lectura)" if wmi else "no disponible: WSL2 no expone sensores y WMI exige administrador"
        print(table(["fase", "CPU %", "CPU °C", "GPU %", "GPU °C", "VRAM MiB", "GPU W"],
                    [[phase, hw_cell(f["cpu"], "%"), hw_cell(f["cpu_temp"], "°") if f["cpu_temp"] else cpu_temp_note,
                      hw_cell(f["gpu_util"], "%"), hw_cell(f["gpu_temp"], "°"), hw_cell(f["gpu_mem_mib"], ""), hw_cell(f["gpu_power_w"], "")]
                     for phase, f in lat["fases"].items()]))

    out = OUT_DIR / f"resultados-{datetime.now():%Y%m%d-%H%M%S}.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=1))
    print(f"\nResultados: {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
