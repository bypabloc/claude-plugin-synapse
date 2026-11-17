"""Switch de Laya: SYNAPSE_LAYA (global, pesa sobre todo) y SYNAPSE_LAYA_HOOKS (hooks específicos)."""

from __future__ import annotations

import sys

import pytest

import common


@pytest.fixture
def laya_installed(monkeypatch: pytest.MonkeyPatch) -> None:
    """Simula Laya instalada y sin --fallback: solo el switch decide."""
    monkeypatch.setattr(common, "_LAYA_AVAILABLE", True)
    monkeypatch.setattr(sys, "argv", [a for a in sys.argv if a != "--fallback"])
    monkeypatch.delenv("SYNAPSE_LAYA", raising=False)
    monkeypatch.delenv("SYNAPSE_LAYA_HOOKS", raising=False)


@pytest.mark.parametrize(
    ("master", "hooks", "hook", "expected"),
    [
        pytest.param(None, None, "block_dangerous", False, id="apagada_por_defecto"),
        pytest.param("on", None, "block_dangerous", True, id="on_global"),
        pytest.param("ON", None, "detect_secrets", True, id="on_mayusculas"),
        pytest.param("1", None, "protect_files", True, id="on_numerico"),
        pytest.param("off", None, "block_dangerous", False, id="off_global"),
        pytest.param("off", "block_dangerous", "block_dangerous", False, id="off_pesa_sobre_la_lista"),
        pytest.param("on", "block_dangerous", "block_dangerous", True, id="on_hook_listado"),
        pytest.param("on", "block_dangerous", "detect_secrets", False, id="on_hook_no_listado"),
        pytest.param(None, "block_dangerous, detect_secrets", "detect_secrets", True, id="solo_lista_hook_listado"),
        pytest.param(None, "block_dangerous", "protect_files", False, id="solo_lista_hook_no_listado"),
        pytest.param("tal_vez", None, "block_dangerous", False, id="valor_invalido_es_apagado"),
    ],
)
def test_switch(laya_installed: None, monkeypatch: pytest.MonkeyPatch, master: str | None, hooks: str | None, hook: str, expected: bool) -> None:
    if master is not None:
        monkeypatch.setenv("SYNAPSE_LAYA", master)
    if hooks is not None:
        monkeypatch.setenv("SYNAPSE_LAYA_HOOKS", hooks)
    assert common.should_use_laya(hook) is expected
    assert common.laya_switch(hook)[0] is expected


def test_fallback_flag_pesa_sobre_on(laya_installed: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SYNAPSE_LAYA", "on")
    monkeypatch.setattr(sys, "argv", [*sys.argv, "--fallback"])
    assert common.should_use_laya("block_dangerous") is False
    assert common.laya_skip_reason("block_dangerous") == "flag --fallback"


def test_sin_laya_instalada_no_se_consulta(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(common, "_LAYA_AVAILABLE", False)
    monkeypatch.setenv("SYNAPSE_LAYA", "on")
    assert common.should_use_laya("block_dangerous") is False


def test_hook_implicito_desde_la_traza(laya_installed: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SYNAPSE_LAYA_HOOKS", "detect_secrets")
    common.start_trace("detect_secrets", {})
    assert common.should_use_laya() is True
    common.start_trace("block_dangerous", {})
    assert common.should_use_laya() is False


def test_motivo_en_la_traza(laya_installed: None) -> None:
    assert "apagada por defecto" in common.laya_skip_reason("block_dangerous")
