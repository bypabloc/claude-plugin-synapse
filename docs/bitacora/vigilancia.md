# Vigilancia de Synapse en sesiones reales

Los tests fijan el contrato. Los problemas que importan aparecen en sesiones largas de verdad: un `ask` que detiene a
Codex durante horas, un falso positivo que se repite, un host que muestra texto roto. Esta revisión la corre la skill
`vigilar-plugins` del monorepo (ver [`docs/vigilancia/`](../../../../docs/vigilancia/README.md)).

## Fuentes

| Fuente | Dónde | Para qué |
| :--- | :--- | :--- |
| Traza de Claude Code | `${CLAUDE_CONFIG_DIR:-~/.claude}/logs/synapse/AAAA-MM-DD.jsonl` | Decisiones en Claude Code |
| Traza de otros hosts | Windows `%LOCALAPPDATA%\synapse\logs\`; Linux y WSL `~/.local/state/synapse/logs/` | Codex, Antigravity, OpenCode (campo `host`) |
| Aprobaciones | `<estado>/approvals/pending/` | Un `ask` de Codex esperando al usuario |
| Transcript de Codex | `~/.codex/sessions/AAAA/MM/DD/rollout-*.jsonl` | Qué hizo el agente después de un `deny` |
| Hooks de Codex | `~/.codex/hooks.json`, `.codex/hooks.json` del proyecto, `[hooks.state]` de `config.toml` | Hooks duplicados o huérfanos |
| Versión instalada | `~/.codex/plugins/cache/bypabloc-agents/synapse/<v>/`, `claude plugin list` | Comparar con la del monorepo |

Vista legible: `python scripts/synapse.py log --since 2h --decision ask` (o `--host codex`). Diagnóstico:
`python scripts/synapse.py doctor --host codex`.

## Revisión, paso a paso

1. **Decisiones `ask` y `block` desde la revisión anterior**, agrupadas por `rule` y `host`.
2. **Cada `ask` en Codex es caro**: busca en el transcript si el agente se detuvo después (último mensaje con
   `approve <id>` y sin más tool calls). Un `ask` que detuvo a un agente con trabajo pendiente es un hallazgo.
3. **Falsos positivos**: un `ask` o `block` sobre algo que el proyecto hace a diario (scripts versionados, docs,
   reglas `.md`). Si se repite en la traza, es candidato a `is_dev_routine`, a `routine_commands` o a una excepción.
4. **Texto roto** (`�`, `Ã³`) en el transcript del host: es un problema de codificación, no de la traza.
5. **Hooks huérfanos**: entradas de `hooks.json` que apuntan a rutas que ya no existen (por ejemplo, las del antiguo
   `claude-plugins`). Codex las ejecuta en cada tool y fallan en silencio.
6. **Versión**: antes de llamar bug a un síntoma, compara la versión instalada con la del monorepo y revisa
   [versiones/](versiones/).

## Síntomas y cómo leerlos

| Síntoma | Patrón | Qué comprobar |
| :--- | :--- | :--- |
| Agente detenido | `ask` en Codex y el transcript termina pidiendo `approve` | ¿La regla merece detener al agente? ¿El texto le dijo cómo seguir? (1.7.1) |
| `ask` repetido sobre lo mismo | Misma `rule` y `target` muchas veces en un día | Excepción determinista o `routine_commands` |
| Mojibake | `�` en el mensaje del host | `dispatch._utf8_streams` (1.7.1); el lanzador `run-hook.cmd` |
| Bloqueo fuera del proyecto injustificado | `structure.outside_project` dentro de un worktree hermano | `allowed_roots` y la carpeta `<repo>-worktrees` |
| Sin traza para un host | El host corre pero no hay líneas con su `host` | Hook sin confiar (`/hooks` en Codex) o manifiesto viejo |

## Cuando hay un bug

Lo resuelve la skill `reparar-plugin`: primero un test que falle, después el arreglo, la versión con
`scripts/bump.py synapse X.Y.Z` y un archivo en [versiones/](versiones/).
