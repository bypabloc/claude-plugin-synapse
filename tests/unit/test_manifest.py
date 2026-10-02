"""Integridad estructural del plugin: manifiestos, hooks.json, scripts, skills y docs."""

import json
import os

import pytest

from support import PROJECT_DIR


def _json(rel: str) -> dict:
    return json.loads((PROJECT_DIR / rel).read_text(encoding="utf-8"))


def test_plugin_manifest() -> None:
    data = _json(".claude-plugin/plugin.json")
    assert data["name"] == "synapse"
    assert data["license"] == "MIT"
    for key in ("version", "description", "author"):
        assert key in data


def test_marketplace_entry() -> None:
    data = _json("marketplace-entry.json")
    assert (data["name"], data["category"]) == ("synapse", "security")


def test_hooks_json_points_to_existing_executable_scripts() -> None:
    commands = [
        h["command"]
        for group in _json("hooks/hooks.json")["hooks"]["PreToolUse"]
        for h in group["hooks"]
    ]
    assert commands
    for cmd in commands:
        script = cmd.split("${CLAUDE_PLUGIN_ROOT}/")[1].strip('"')
        path = PROJECT_DIR / script
        assert path.is_file(), cmd
        assert os.access(path, os.X_OK), f"{script} no es ejecutable"


@pytest.mark.parametrize(
    "rel",
    ["skills/synapse-status/SKILL.md", "rules/security-guardrails.md", "README.md", "AGENTS.md"],
)
def test_component_files_exist(rel: str) -> None:
    assert (PROJECT_DIR / rel).is_file()
    assert (PROJECT_DIR / rel).stat().st_size > 0
