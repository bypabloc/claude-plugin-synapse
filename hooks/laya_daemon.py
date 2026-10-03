#!/usr/bin/env python3
"""Daemon de Laya System 1: mantiene el checkpoint cargado y atiende a los hooks por un socket Unix.

Por qué existe:
  Cada hook es un proceso nuevo. Cargar Laya en cada uno costaba ~7 s (import de torch 1.7 s + checkpoint
  4 s + warmup CUDA 1 s) y `preload=True` ponía 4.7 GB en la GPU por proceso: con 4-8 hooks simultáneos la
  traza real llegó a 112 s por comando. Con el modelo residente, una inferencia cuesta ~30 ms.

Costo de mantenerlo vivo:
  Solo VRAM (~1.7 GB del checkpoint inglés; el multilingüe se carga si algún hook lo necesita). Sin peticiones
  la GPU no computa: el consumo es el mismo que en reposo. Tras SYNAPSE_LAYA_IDLE_SECONDS sin uso (20 min por
  defecto) el daemon termina y libera la VRAM; el siguiente hook lo vuelve a levantar.

Protocolo: una línea JSON por conexión.
  {"op": "predict", "state", "questions", "model", "delta"} -> {"ok": true, "result": <salida de Router.predict>}
  {"op": "ping"} | {"op": "shutdown"}                       -> {"ok": true, ...}
  Error                                                      -> {"ok": false, "error": "..."}

`delta` es la ruta de un delta afinado (safetensors) que se aplica solo durante esa petición y se revierte
después: el resto de los hooks sigue viendo el checkpoint base.

Uso manual:
  $ python3 hooks/laya_daemon.py serve gpu     # normalmente lo arranca el primer hook que lo necesita
  $ python3 hooks/laya_daemon.py stop gpu
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import socket
import socketserver
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterator

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
IDLE_SECONDS = float(os.environ.get("SYNAPSE_LAYA_IDLE_SECONDS", "1200"))
STARTUP_SECONDS = float(os.environ.get("SYNAPSE_LAYA_STARTUP_SECONDS", "40"))
REQUEST_SECONDS = 45.0  # incluye esperar la carga inicial del checkpoint (~6 s) y la cola de otros hooks
MAX_REQUEST_BYTES = 1 << 20
MAX_SOCKET_PATH = 104  # sun_path: 108 bytes con el NUL final

Predictor = Callable[[dict[str, Any]], dict[str, Any]]


def runtime_dir() -> Path:
    base = os.environ.get("SYNAPSE_RUNTIME_DIR") or os.environ.get("XDG_RUNTIME_DIR") or str(Path.home() / ".cache")
    return Path(base) / "synapse"


def socket_path(mode: str) -> Path:
    """Un daemon por instalación del plugin y modo: otra versión trae otro delta y otra política."""
    key = hashlib.sha1(f"{PLUGIN_ROOT}|{mode}".encode()).hexdigest()[:12]
    path = runtime_dir() / f"laya-{key}.sock"
    if len(str(path)) >= MAX_SOCKET_PATH:  # bind() falla con rutas de más de ~108 bytes
        path = Path.home() / ".cache" / "synapse" / path.name
    return path


# ------------------------------------------------------------------ cliente (lo usan los hooks)


def send(path: Path, payload: dict[str, Any], timeout: float = REQUEST_SECONDS) -> dict[str, Any]:
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as conn:
        conn.settimeout(timeout)
        conn.connect(str(path))
        conn.sendall(json.dumps(payload).encode() + b"\n")
        chunks = []
        while not chunks or not chunks[-1].endswith(b"\n"):
            chunk = conn.recv(65536)
            if not chunk:
                break
            chunks.append(chunk)
    response = json.loads(b"".join(chunks))
    if not response.get("ok"):
        raise RuntimeError(response.get("error", "error desconocido del daemon de Laya"))
    return response


def spawn(mode: str) -> subprocess.Popen[bytes]:
    """Arranca el daemon desacoplado de la sesión. Si ya hay uno arrancando, el nuevo sale por el lock."""
    log_dir = runtime_dir()
    log_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    with open(log_dir / "laya-daemon.log", "ab") as log:
        return subprocess.Popen(
            [sys.executable, str(Path(__file__).resolve()), "serve", mode],
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=log,
            start_new_session=True,
            close_fds=True,
        )


def _lock_held(path: Path) -> bool:
    """True si algún daemon tiene el lock de esta ruta (está atendiendo o arrancando)."""
    try:
        with open(f"{path}.lock") as lock_file:
            fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return False
    except BlockingIOError:
        return True
    except OSError:
        return False


def request(payload: dict[str, Any], mode: str, *, startup_timeout: float = STARTUP_SECONDS) -> dict[str, Any]:
    """Envía una petición; si no hay daemon lo arranca una vez y espera a que escuche.

    Si el daemon lanzado terminó y nadie tiene el lock, falló al arrancar: se abandona de inmediato en vez de
    hacer esperar el timeout completo a cada hook.
    """
    path = socket_path(mode)
    try:
        return send(path, payload)
    except (FileNotFoundError, ConnectionRefusedError):
        pass
    proc = spawn(mode)
    deadline = time.monotonic() + startup_timeout
    while True:
        try:
            return send(path, payload)
        except (FileNotFoundError, ConnectionRefusedError):
            if proc is not None and proc.poll() is not None and not _lock_held(path):
                raise RuntimeError(
                    f"el daemon de Laya terminó al arrancar (código {proc.returncode}); ver {runtime_dir() / 'laya-daemon.log'}"
                ) from None
            if time.monotonic() > deadline:
                raise TimeoutError(f"el daemon de Laya no respondió en {startup_timeout:.0f} s") from None
            time.sleep(0.05)


# ------------------------------------------------------------------ servidor


class _Handler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        try:
            response = self.server.dispatch(json.loads(self.rfile.readline(MAX_REQUEST_BYTES)))  # type: ignore[attr-defined]
        except Exception as exc:
            response = {"ok": False, "error": f"{type(exc).__name__}: {exc}"[:500]}
        self.wfile.write(json.dumps(response, default=_to_json).encode() + b"\n")


def _to_json(value: Any) -> Any:
    return value.item() if hasattr(value, "item") else str(value)


class LayaServer(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True
    # El default (5) rechaza conexiones en ráfagas: 4 hooks por tool_use y varias sesiones a la vez
    request_queue_size = 64

    def __init__(self, path: Path, factory: Callable[[], Predictor], idle_seconds: float):
        super().__init__(str(path), _Handler)
        self.idle_seconds = idle_seconds
        self.last_used = time.monotonic()
        self.lock = threading.Lock()  # una inferencia a la vez: la GPU no gana nada con concurrencia aquí
        self.predictor: Predictor | None = None
        self.init_error: str | None = None
        self._ready = threading.Event()
        threading.Thread(target=self._load, args=(factory,), daemon=True).start()
        threading.Thread(target=self._watch_idle, daemon=True).start()

    def _load(self, factory: Callable[[], Predictor]) -> None:
        with self.lock:
            try:
                self.predictor = factory()
            except Exception as exc:
                self.init_error = f"{type(exc).__name__}: {exc}"
            finally:
                self._ready.set()

    def _watch_idle(self) -> None:
        while True:
            time.sleep(min(5.0, max(0.05, self.idle_seconds / 4)))
            if time.monotonic() - self.last_used > self.idle_seconds and not self.lock.locked():
                _log(f"apagado por inactividad ({self.idle_seconds:.0f} s sin uso)")
                self.shutdown()
                return

    def dispatch(self, req: dict[str, Any]) -> dict[str, Any]:
        self.last_used = time.monotonic()
        op = req.get("op")
        if op == "ping":
            return {"ok": True, "pid": os.getpid(), "ready": self._ready.is_set(), "error": self.init_error}
        if op == "shutdown":
            _log("apagado a pedido (op shutdown)")
            threading.Thread(target=self.shutdown, daemon=True).start()
            return {"ok": True}
        if op != "predict":
            return {"ok": False, "error": f"operación desconocida: {op!r}"}
        self._ready.wait(REQUEST_SECONDS)
        with self.lock:
            if self.predictor is None:
                return {"ok": False, "error": self.init_error or "el modelo aún no está cargado"}
            result = self.predictor(req)
        self.last_used = time.monotonic()
        return {"ok": True, **result}


def pending_socket_path(path: Path) -> Path:
    """Ruta temporal del socket hasta que escucha: mismo directorio (rename atómico) y nunca más larga que la final."""
    return path.with_name(f".{os.getpid() % 100000}.s")


def _log(message: str) -> None:
    print(f"{time.strftime('%Y-%m-%dT%H:%M:%S')} pid {os.getpid()} {message}", file=sys.stderr, flush=True)


def serve(path: Path, factory: Callable[[], Predictor], idle_seconds: float = IDLE_SECONDS) -> None:
    """Atiende hasta quedar inactivo. Si otro daemon ya tiene el lock de esta ruta, sale sin hacer nada."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with open(f"{path}.lock", "w") as lock_file:
        try:
            fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        _log(f"escuchando en {path} (inactividad máxima {idle_seconds:.0f} s)")
        path.unlink(missing_ok=True)  # socket huérfano de un daemon que murió: con el lock tomado es seguro
        pending = pending_socket_path(path)
        pending.unlink(missing_ok=True)
        old_umask = os.umask(0o177)  # socket 0600: solo el usuario puede pedir inferencias
        try:
            server = LayaServer(pending, factory, idle_seconds)
        finally:
            os.umask(old_umask)
        # bind() crea el archivo antes de listen(): publicarlo recién ahora evita ConnectionRefused a los clientes
        os.rename(pending, path)
        try:
            server.serve_forever(poll_interval=0.2)
        finally:
            server.server_close()
            path.unlink(missing_ok=True)
            _log("terminado")


# ------------------------------------------------------------------ backend real


WARMUP_QUESTION = {"warmup": {"type": "choice", "instructions": "Is this text empty?", "criteria": {"A": "yes", "B": "no"}}}


class LayaBackend:
    """Router de Laya residente. Solo el daemon (y los scripts de evaluación) importan torch."""

    def __init__(self, mode: str):
        self.router = local_router(mode)
        self.router.load("english")
        self.router.predict("warmup", WARMUP_QUESTION, model="english")  # el primer forward en CUDA cuesta ~1 s
        self._deltas: dict[str, dict[str, Any]] = {}

    def __call__(self, req: dict[str, Any]) -> dict[str, Any]:
        model, delta = req.get("model"), req.get("delta")
        with self._delta_applied(model or "english", delta):
            result = self.router.predict(req["state"], req["questions"], model=model)
        return {"result": result, "device": str(self.router.device)}

    @contextmanager
    def _delta_applied(self, model: str, delta: str | None) -> Iterator[None]:
        if not delta:
            yield
            return
        import torch
        from safetensors.torch import load_file

        state = self.router.load(model).model.state_dict()
        if delta not in self._deltas:
            tuned = {k: v for k, v in load_file(delta, device=str(next(iter(state.values())).device)).items() if k in state}
            self._deltas[delta] = {k: v.to(state[k].dtype) for k, v in tuned.items()}
        tuned = self._deltas[delta]
        base = {k: state[k].detach().clone() for k in tuned}
        with torch.no_grad():
            for k, v in tuned.items():
                state[k].copy_(v)
        try:
            yield
        finally:
            with torch.no_grad():
                for k, v in base.items():
                    state[k].copy_(v)


def local_router(mode: str = "gpu") -> Any:
    """Router de Laya en este proceso. 'gpu' exige CUDA (sin GPU los hooks operan en fallback)."""
    os.environ.setdefault("USE_TF", "0")
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
    import torch
    from laya import Router

    if mode == "cpu":
        cores = os.cpu_count() or 4
        torch.set_num_threads(max(1, cores // 2 if cores > 4 else cores))
        return Router(device="cpu")
    if not torch.cuda.is_available():
        raise RuntimeError("sin GPU CUDA disponible: los hooks operan en modo fallback (usa --cpu para forzar CPU)")
    return Router(device="cuda")


def main(argv: list[str]) -> int:
    action = argv[0] if argv else "serve"
    mode = argv[1] if len(argv) > 1 else "gpu"
    if action == "serve":
        serve(socket_path(mode), lambda: LayaBackend(mode))
        return 0
    if action == "stop":
        try:
            send(socket_path(mode), {"op": "shutdown"}, timeout=5)
        except (FileNotFoundError, ConnectionRefusedError):
            pass
        return 0
    print(f"uso: {Path(__file__).name} serve|stop [gpu|cpu]", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
