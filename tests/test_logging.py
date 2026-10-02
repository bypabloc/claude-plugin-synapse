#!/usr/bin/env python3
"""Pruebas unitarias de auditoría y almacenamiento de logs en carpetas dedicadas.

Verifica:
  1. Redirección y persistencia de logs en directorio específico mediante SYNAPSE_LOG_DIR.
  2. Formato estructurado y trazable de cada entrada de auditoría (timestamp, acción, modo, hook, target, motivo).
  3. Ejecución en subproceso con persistencia de logs durante invocaciones reales de hooks.
  4. Garantía de aislamiento: los logs no contaminan directorios globales durante las pruebas.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

# Agregar directorio hooks a sys.path
PROJECT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
HOOKS_DIR = os.path.join(PROJECT_DIR, "hooks")
if HOOKS_DIR not in sys.path:
    sys.path.insert(0, HOOKS_DIR)

import common


class TestHookLogging(unittest.TestCase):
    """Pruebas del sistema de logging y auditoría de Synapse."""

    def setUp(self) -> None:
        self.project_dir = PROJECT_DIR
        # Carpeta específica para pruebas de logs dentro de ./tmp/ (cumple regla de rutas temporales del proyecto)
        self.test_log_dir = os.path.join(self.project_dir, "tmp", "test_audit_logs")
        if os.path.exists(self.test_log_dir):
            shutil.rmtree(self.test_log_dir)
        os.makedirs(self.test_log_dir, exist_ok=True)
        self.original_env = os.environ.get("SYNAPSE_LOG_DIR")
        os.environ["SYNAPSE_LOG_DIR"] = self.test_log_dir

    def tearDown(self) -> None:
        if self.original_env is not None:
            os.environ["SYNAPSE_LOG_DIR"] = self.original_env
        else:
            os.environ.pop("SYNAPSE_LOG_DIR", None)

        if os.path.exists(self.test_log_dir):
            shutil.rmtree(self.test_log_dir)

    def test_log_path_resolution_with_env(self) -> None:
        """Verifica que get_log_paths resuelva con prioridad absoluta el directorio configurado."""
        paths = common.get_log_paths()
        self.assertEqual(len(paths), 1)
        expected_log_file = Path(self.test_log_dir) / "security_hooks.log"
        self.assertEqual(paths[0], expected_log_file)

    def test_record_audit_log_format_and_persistence(self) -> None:
        """Verifica el formato estructurado y la escritura en el archivo de log dedicado."""
        common.record_audit_log(
            action="BLOCKED",
            hook_name="test_guard",
            tool_name="Bash",
            target="rm -rf /",
            reason="Comando catastrófico bloqueado preventivamente",
        )

        log_file = Path(self.test_log_dir) / "security_hooks.log"
        self.assertTrue(log_file.is_file(), "El archivo de log dedicado no fue creado")

        content = log_file.read_text(encoding="utf-8")
        self.assertIn("[BLOCKED]", content)
        self.assertIn("[test_guard       ]", content)
        self.assertIn("Tool: Bash", content)
        self.assertIn("Target: 'rm -rf /'", content)
        self.assertIn("Reason: Comando catastrófico bloqueado preventivamente", content)

    def test_subprocess_hook_execution_logs_to_specific_folder(self) -> None:
        """Verifica que un subproceso de hook registre su auditoría en el directorio especificado."""
        script_path = os.path.join(HOOKS_DIR, "block_dangerous.py")
        payload = {
            "tool_name": "Bash",
            "tool_input": {"command": "rm -rf /"},
            "cwd": self.project_dir,
        }

        env = os.environ.copy()
        env["SYNAPSE_LOG_DIR"] = self.test_log_dir

        proc = subprocess.run(
            [sys.executable, script_path, "--fallback"],
            input=json.dumps(payload),
            text=True,
            capture_output=True,
            env=env,
            cwd=self.project_dir,
        )

        self.assertEqual(proc.returncode, 2)

        log_file = Path(self.test_log_dir) / "security_hooks.log"
        self.assertTrue(log_file.is_file(), "El hook en subproceso no escribió en el directorio dedicado")

        lines = log_file.read_text(encoding="utf-8").strip().splitlines()
        self.assertGreaterEqual(len(lines), 1)
        last_line = lines[-1]
        self.assertIn("[BLOCKED]", last_line)
        self.assertIn("block_dangerous", last_line)
        self.assertIn("rm -rf /", last_line)


if __name__ == "__main__":
    unittest.main()
