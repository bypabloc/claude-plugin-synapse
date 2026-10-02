#!/usr/bin/env python3
"""Pruebas unitarias de integridad estructural del manifiesto y componentes de Synapse."""

import json
import os
import unittest


class TestSynapsePluginManifest(unittest.TestCase):
    def setUp(self):
        self.plugin_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        self.manifest_path = os.path.join(self.plugin_root, ".claude-plugin", "plugin.json")
        self.marketplace_path = os.path.join(self.plugin_root, "marketplace-entry.json")
        self.hooks_path = os.path.join(self.plugin_root, "hooks", "hooks.json")

    def test_plugin_manifest_valid(self):
        self.assertTrue(os.path.isfile(self.manifest_path), "Falta .claude-plugin/plugin.json")
        with open(self.manifest_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual(data["name"], "synapse")
        self.assertIn("version", data)
        self.assertIn("description", data)
        self.assertIn("author", data)
        self.assertEqual(data["license"], "MIT")

    def test_marketplace_entry_valid(self):
        self.assertTrue(os.path.isfile(self.marketplace_path), "Falta marketplace-entry.json")
        with open(self.marketplace_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual(data["name"], "synapse")
        self.assertEqual(data["category"], "security")

    def test_hooks_configuration_valid(self):
        self.assertTrue(os.path.isfile(self.hooks_path), "Falta hooks/hooks.json")
        with open(self.hooks_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        self.assertIn("hooks", data)
        self.assertIn("PreToolUse", data["hooks"])

    def test_hook_scripts_exist_and_executable(self):
        scripts = [
            "common.py",
            "block_dangerous.py",
            "block_env_read.py",
            "detect_secrets.py",
            "protect_files.py",
        ]
        for script in scripts:
            script_path = os.path.join(self.plugin_root, "hooks", script)
            self.assertTrue(os.path.isfile(script_path), f"Falta {script_path}")
            if script != "common.py":
                self.assertTrue(os.access(script_path, os.X_OK), f"{script} no tiene permisos de ejecución")

    def test_rules_and_skills_exist(self):
        skill_path = os.path.join(self.plugin_root, "skills", "synapse-status", "SKILL.md")
        self.assertTrue(os.path.isfile(skill_path), "Falta skills/synapse-status/SKILL.md")
        rule_path = os.path.join(self.plugin_root, "rules", "security-guardrails.md")
        self.assertTrue(os.path.isfile(rule_path), "Falta rules/security-guardrails.md")

    def test_documentation_files_exist(self):
        readme_path = os.path.join(self.plugin_root, "README.md")
        agents_path = os.path.join(self.plugin_root, "AGENTS.md")
        self.assertTrue(os.path.isfile(readme_path), "Falta README.md en la raíz del plugin")
        self.assertTrue(os.path.isfile(agents_path), "Falta AGENTS.md en la raíz del plugin")
        self.assertGreater(os.path.getsize(readme_path), 500, "README.md está vacío o incompleto")
        self.assertGreater(os.path.getsize(agents_path), 500, "AGENTS.md está vacío o incompleto")


if __name__ == "__main__":
    unittest.main()
