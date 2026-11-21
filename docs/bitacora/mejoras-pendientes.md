# Mejoras pendientes de Synapse

Ideas detectadas que todavía no se implementaron, con su motivo. Al implementarlas, pasan a [versiones/](versiones/).

| Idea | Motivo | Origen |
| :--- | :--- | :--- |
| Reglas completas de PowerShell | Hoy solo hay un mínimo; Codex en Windows usa PowerShell para muchos comandos | Spec 001, T-94 |
| Servidor `dispatch` persistente | Cada tool lanza un proceso de Python; medir con `scripts/bench_dispatch.py` antes de decidir | Spec 001, T-26 y T-94 |
| UTF-8 en los hooks de Claude Code en Windows | 1.7.1 lo arregla solo en `dispatch.py`; los scripts por hook de Claude conservan su salida (golden) | 1.7.1 |
| Revisar `AGENTS.override.md` en `ASK_PATTERNS` | También es una instrucción en Markdown, como `.agents/rules/*.md` | 1.7.1 |
| `test_concurrent_writers_never_corrupt_lines` desde WSL sobre `/mnt/d` | Falla siempre en ese montaje (9P), antes y después de 1.7.1; en Windows y en Linux nativo pasa | 2026-11-21 |
