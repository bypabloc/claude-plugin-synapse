"""Configuración pytest: modo de motor (--laya), aislamiento de logs y limpieza de repos git efímeros."""

from __future__ import annotations

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
    """common.should_use_laya() lee sys.argv: se inyecta el flag para las pruebas in-process."""
    import common

    sys.argv.append(laya_flag)
    common._ROUTER_INSTANCE = None
    yield
    sys.argv.remove(laya_flag)
    shutil.rmtree(GIT_REPOS_DIR, ignore_errors=True)


@pytest.fixture(autouse=True)
def isolated_logs(tmp_path, monkeypatch):
    log_dir = tmp_path / "logs"
    monkeypatch.setenv("SYNAPSE_LOG_DIR", str(log_dir))
    return log_dir
