"""laya_daemon: protocolo del socket, inferencia serializada, apagado por inactividad y arranque bajo demanda.

Sin GPU: el servidor real corre en un hilo con un predictor simulado. El modelo real se prueba en
tests/e2e/test_laya_daemon_gpu.py (pytest --laya gpu).
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

import laya_daemon
from support import GIT_REPOS_DIR


@pytest.fixture
def sock(request) -> Path:
    # Ruta corta: los sockets Unix admiten ~108 caracteres
    path = GIT_REPOS_DIR / "rt" / f"{request.node.name[:20]}.sock"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.unlink(missing_ok=True)
    return path


class FakePredictor:
    def __init__(self, delay: float = 0.0):
        self.delay = delay
        self.active = 0
        self.max_active = 0
        self.calls = 0
        self._mutex = threading.Lock()

    def __call__(self, req: dict) -> dict:
        with self._mutex:
            self.active += 1
            self.calls += 1
            self.max_active = max(self.max_active, self.active)
        time.sleep(self.delay)
        with self._mutex:
            self.active -= 1
        return {"result": {"answers": {"echo": req["state"]}}, "device": "fake"}


def start(sock: Path, factory, idle: float = 60.0) -> threading.Thread:
    thread = threading.Thread(target=laya_daemon.serve, args=(sock, factory, idle), daemon=True)
    thread.start()
    deadline = time.monotonic() + 5
    while not sock.exists():
        assert time.monotonic() < deadline, "el servidor no creó el socket"
        time.sleep(0.01)
    return thread


def stop(sock: Path, thread: threading.Thread) -> None:
    laya_daemon.send(sock, {"op": "shutdown"})
    thread.join(5)


def predict(sock: Path, state: str) -> dict:
    return laya_daemon.send(sock, {"op": "predict", "state": state, "questions": {}})


def test_roundtrip(sock) -> None:
    thread = start(sock, FakePredictor)
    try:
        assert predict(sock, "ls")["result"] == {"answers": {"echo": "ls"}}
        assert laya_daemon.send(sock, {"op": "ping"})["ok"] is True
    finally:
        stop(sock, thread)
    assert not sock.exists()


def test_inference_is_serialized(sock) -> None:
    fake = FakePredictor(delay=0.01)
    thread = start(sock, lambda: fake)
    try:
        results: list[dict] = []
        # 32 a la vez: con el backlog por defecto (5) algunas conexiones se rechazaban
        workers = [threading.Thread(target=lambda i=i: results.append(predict(sock, f"c{i}"))) for i in range(32)]
        for w in workers:
            w.start()
        for w in workers:
            w.join(10)
        assert len(results) == 32 and all(r["ok"] for r in results)
        assert fake.calls == 32 and fake.max_active == 1
    finally:
        stop(sock, thread)


def test_idle_shutdown_frees_socket(sock) -> None:
    thread = start(sock, FakePredictor, idle=0.3)
    thread.join(5)
    assert not thread.is_alive() and not sock.exists()


def test_second_daemon_exits_while_first_holds_lock(sock) -> None:
    thread = start(sock, FakePredictor)
    try:
        second = threading.Thread(target=laya_daemon.serve, args=(sock, FakePredictor, 60.0), daemon=True)
        second.start()
        second.join(5)
        assert not second.is_alive()
        assert predict(sock, "x")["ok"] is True
    finally:
        stop(sock, thread)


def test_backend_failure_is_reported(sock) -> None:
    def broken():
        raise RuntimeError("sin GPU CUDA")

    thread = start(sock, broken)
    try:
        with pytest.raises(RuntimeError, match="sin GPU CUDA"):
            predict(sock, "x")
    finally:
        stop(sock, thread)


def test_request_spawns_daemon_once_when_missing(sock, monkeypatch) -> None:
    spawned: list[threading.Thread] = []
    monkeypatch.setattr(laya_daemon, "socket_path", lambda mode: sock)
    monkeypatch.setattr(laya_daemon, "spawn", lambda mode: spawned.append(start(sock, FakePredictor)))
    try:
        assert laya_daemon.request({"op": "predict", "state": "a", "questions": {}}, "gpu")["result"]["answers"]["echo"] == "a"
        assert laya_daemon.request({"op": "predict", "state": "b", "questions": {}}, "gpu")["ok"] is True
        assert len(spawned) == 1
    finally:
        stop(sock, spawned[0])


def test_request_times_out_when_daemon_never_starts(sock, monkeypatch) -> None:
    monkeypatch.setattr(laya_daemon, "socket_path", lambda mode: sock)
    monkeypatch.setattr(laya_daemon, "spawn", lambda mode: None)
    with pytest.raises(TimeoutError):
        laya_daemon.request({"op": "ping"}, "gpu", startup_timeout=0.3)


def test_request_fails_fast_when_daemon_dies_on_startup(sock, monkeypatch) -> None:
    # Antes: un daemon que moría al arrancar (p. ej. ruta de socket demasiado larga) hacía esperar 40 s a cada hook
    class Dead:
        returncode = 1

        def poll(self) -> int:
            return 1

    monkeypatch.setattr(laya_daemon, "socket_path", lambda mode: sock)
    monkeypatch.setattr(laya_daemon, "spawn", lambda mode: Dead())
    started = time.monotonic()
    with pytest.raises(RuntimeError, match="terminó al arrancar"):
        laya_daemon.request({"op": "ping"}, "gpu", startup_timeout=30)
    assert time.monotonic() - started < 2


def test_pending_socket_name_is_never_longer_than_final(monkeypatch) -> None:
    monkeypatch.setenv("SYNAPSE_RUNTIME_DIR", "/" + "x" * 70)
    path = laya_daemon.socket_path("gpu")
    assert len(str(laya_daemon.pending_socket_path(path))) <= len(str(path)) < 108


def test_socket_path_depends_on_plugin_root_and_mode(monkeypatch) -> None:
    short = GIT_REPOS_DIR / "rt"
    monkeypatch.setenv("SYNAPSE_RUNTIME_DIR", str(short))
    gpu, cpu = laya_daemon.socket_path("gpu"), laya_daemon.socket_path("cpu")
    assert gpu != cpu and gpu.parent == short / "synapse"


def test_socket_path_stays_bindable_with_long_runtime_dir(monkeypatch) -> None:
    monkeypatch.setenv("SYNAPSE_RUNTIME_DIR", "/" + "x" * 120)
    assert len(str(laya_daemon.socket_path("gpu"))) < 108


def test_hooks_client_does_not_import_torch() -> None:
    import subprocess
    import sys

    from support import HOOKS_DIR

    code = f"import sys; sys.path.insert(0, {str(HOOKS_DIR)!r}); import common, block_dangerous; print('torch' in sys.modules)"
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True).stdout.strip()
    assert out == "False"
