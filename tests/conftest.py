"""Configuración pytest: modo de motor (--laya), aislamiento de logs y limpieza de repos git efímeros."""

from __future__ import annotations

import os
import shutil
import sys

import pytest

from support import GIT_REPOS_DIR, HOOKS_DIR

if str(HOOKS_DIR) not in sys.path:
    sys.path.insert(0, str(HOOKS_DIR))

LAYA_FLAGS = {"off": "--fallback", "cpu": "--cpu", "gpu": "--gpu"}


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--laya",
        choices=sorted(LAYA_FLAGS),
        default="off",
        help="Motor de los hooks: off = fallback determinístico (default), cpu/gpu = Laya System 1",
    )


@pytest.fixture(scope="session")
def laya_flag(request: pytest.FixtureRequest) -> str:
    return LAYA_FLAGS[request.config.getoption("--laya")]


@pytest.fixture(scope="session", autouse=True)
def engine_mode(laya_flag: str):
    """common.should_use_laya() lee sys.argv: se inyecta el flag para las pruebas in-process.

    El daemon de Laya de las pruebas vive en un runtime propio (ruta corta: límite de los sockets Unix)
    y se apaga al terminar, para no dejar VRAM ocupada ni mezclarse con el de la sesión real.
    """
    import common
    import laya_daemon

    sys.argv.append(laya_flag)
    common.allowed_roots.cache_clear()
    os.environ["SYNAPSE_RUNTIME_DIR"] = str(GIT_REPOS_DIR / "rt")
    yield
    for mode in ("gpu", "cpu"):
        try:
            laya_daemon.send(laya_daemon.socket_path(mode), {"op": "shutdown"}, timeout=5)
        except OSError:
            pass
    os.environ.pop("SYNAPSE_RUNTIME_DIR", None)
    sys.argv.remove(laya_flag)
    shutil.rmtree(GIT_REPOS_DIR, ignore_errors=True)


@pytest.fixture(autouse=True)
def isolated_logs(tmp_path, monkeypatch):
    log_dir = tmp_path / "logs"
    monkeypatch.setenv("SYNAPSE_LOG_DIR", str(log_dir))
    # Dentro de una sesión de Claude Code esta variable apunta al repo real: cada test define su proyecto
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    return log_dir
