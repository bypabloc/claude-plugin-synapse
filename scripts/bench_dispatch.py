#!/usr/bin/env python3
"""Latencia de hooks/dispatch.py por llamada, como la paga el shim de OpenCode (spec 001 · T-26).

  python3 scripts/bench_dispatch.py [--runs 20]   → p50 y p95 en ms (proceso nuevo por llamada, sin Laya)
"""

from __future__ import annotations

import argparse
import json
import statistics
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PAYLOADS = {
    "shell": {"tool": "shell", "input": {"command": "git status --short"}},
    "write": {"tool": "write", "input": {"path": "src/app.py", "content": "print('hola')\n"}},
}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs", type=int, default=20)
    args = parser.parse_args()
    for name, payload in PAYLOADS.items():
        body = json.dumps({**payload, "sessionID": "bench", "callID": "c", "directory": str(ROOT), "project": str(ROOT)})
        samples = []
        for _ in range(args.runs):
            started = time.perf_counter()
            subprocess.run([sys.executable, str(ROOT / "hooks" / "dispatch.py"), "--host", "opencode"], input=body,
                           text=True, capture_output=True, check=True)
            samples.append((time.perf_counter() - started) * 1000)
        samples.sort()
        p95 = samples[max(0, int(len(samples) * 0.95) - 1)]
        print(f"{name}: p50 {statistics.median(samples):.0f} ms · p95 {p95:.0f} ms ({args.runs} llamadas)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
