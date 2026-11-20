# Plan de Implementación y Verificaciones Pendientes

> Orden de trabajo propuesto, criterios de cierre por fase, estrategia de tests y protocolo para confirmar empíricamente los datos que la documentación de cada host no especifica.

---

## 1. Fases

```text
 F0  Endurecimiento inmediato (sin refactor)       ── Cursor import deja de fallar abierto
  │
 F1  Núcleo neutral: hosts.py + patch.py + rutas  ── Claude Code sin cambios de comportamiento
  │
  ├── F2  Codex          (comparte hooks/hooks.json)
  ├── F3  Copilot        (comparte hooks/hooks.json)
  ├── F4  Herdr          (reglas en block_dangerous, sin I/O nuevo)
  │
 F5  Cursor               (manifiesto + eventos propios)
 F6  Antigravity          (CLI; IDE/App tras verificar)
 F7  OpenCode             (shim TS, V2 primero)
 F8  Gemini CLI           (opcional: destino heredado)
```

| Fase | Alcance | Criterio de cierre |
| :--- | :--- | :--- |
| **F0** | Aceptar `Shell` como alias de `Bash`; `cwd` vacío → raíz del proyecto; plugin root desde `__file__` si falta la variable | Tests nuevos con payload estilo Cursor-import pasan; suite actual verde |
| **F1** | `hooks/hosts.py` (normalize/emit por host), `hooks/patch.py`, cadena de rutas de [09 §5](./09-propuesta-de-arquitectura.md#5-rutas-neutrales), campo `host` y `emitted` en la traza, `SYNAPSE_ASK_FALLBACK` | Cero diferencias de decisión en `tests/cases/*.json` para Claude; `python3 -m pytest` y `--laya gpu` verdes |
| **F2** | Codex: autodetección por `turn_id`; emisión `deny` JSON, `allow` vacío, `ask` degradado; matcher `mcp__.*` para lecturas; archivos protegidos `.codex/**`; regla como skill | E2E contra `codex` real (≥0.125) con los payloads capturados |
| **F3** | Copilot: autodetección; salida plana; mapeo de argumentos nativos; `timeoutSec` | E2E contra `copilot` CLI real |
| **F4** | Herdr: desenvolver `pane run` / `agent start`; `ask` para `send-*`, `agent prompt`, `plugin install`, socket; generalizar a `tmux send-keys`, `codex exec`, `claude -p` | Casos en `tests/cases/block_dangerous.json` |
| **F5** | Cursor: `.cursor-plugin/plugin.json`, hooks con `failClosed: true`, regla `.mdc` | E2E en `cursor-agent` y en el IDE |
| **F6** | Antigravity: `.antigravity-plugin/plugin.json`, `hooks.json` raíz, `trigger: always_on`, emisión exit 0 siempre | `agy plugin validate --strict`; E2E en `agy` |
| **F7** | OpenCode: paquete `adapters/opencode` con `setup()` V2 (y `server()` V1 si se decide) | Prueba en OpenCode 2.x con `throw` y `permission.assert` |
| **F8** | Gemini CLI: extensión en `adapters/gemini/` | Opcional |

### 1.1 Justificación del Orden

- **F0 primero** porque corrige una falla abierta existente: cualquier usuario de Cursor con Synapse instalado en Claude Code ya está expuesto (ver [06 §2](./06-cursor.md#2-importación-de-hooks-y-plugins-de-claude)).
- **Codex y Copilot antes que Cursor** porque comparten `hooks/hooks.json` y el manifiesto de Claude: el costo de empaquetado es casi nulo.
- **Herdr en paralelo** porque es una mejora de reglas que beneficia a Claude Code hoy, sin depender de F1.
- **Antigravity después** por la incertidumbre de si sus hooks corren en IDE/App.
- **OpenCode al final** porque exige otro lenguaje (TS), otro ciclo de publicación (npm) y la API V2 es reciente.

---

## 2. Estrategia de Tests

### 2.1 Matriz de Equivalencia

Los casos de `tests/cases/<hook>.json` ya describen *qué* debe decidirse. Se agrega un generador que, a partir de cada caso canónico (formato Claude), sintetiza el payload equivalente para cada host y verifica:

1. **La decisión interna es idéntica** en todos los hosts (`block`/`ask`/`allow`/`pass` y `rule`).
2. **La emisión respeta el contrato del host** (código de salida, forma del JSON, degradación de `ask`).

```text
tests/cases/block_dangerous.json  ──┐
                                    ├─▶ support.payload_for(host, case) ─▶ hook --host H
tests/hosts/<host>.json (mapeos) ───┘                                       │
                                                                            ▼
                                         assert decisión == canónica && emisión ∈ contrato(H)
```

Casos exclusivos por host que no tienen equivalente canónico:

- `apply_patch` con varios archivos (uno protegido, uno con secreto, uno con `Move to`).
- Lecturas MCP en Codex (`mcp__filesystem__read_file`).
- `read_many_files.include[]` con globs en Gemini.
- `multi_replace_file_content.ReplacementChunks` en Antigravity.
- Payload de Cursor-import (`tool_name: "Shell"`, `cwd: ""`).

### 2.2 Contrato de Emisión por Host

Un test por host que valida la salida contra el schema publicado cuando existe (Codex publica schemas generados en `codex-rs/hooks/schema/generated`; `additionalProperties: false` obliga a no emitir campos extra).

### 2.3 E2E Real

Igual que `tests/e2e/test_plugin_hooks.py` corre el pipeline desde `hooks.json`, un e2e por host lee el manifiesto de ese host y ejecuta los comandos declarados. Los E2E contra binarios reales (`codex`, `copilot`, `agy`, `cursor-agent`, `opencode`) se marcan con `pytest.mark.<host>` y se omiten si el binario no está instalado.

---

## 3. Protocolo de Captura de Payloads Reales

Varias decisiones del adaptador dependen de campos que la documentación no especifica. Antes de cerrar cada fase:

1. Instalar en el host un hook "sonda" que solo copie el stdin y el entorno filtrado (sin valores de variables sensibles) a `./tmp/probe/<host>/<tool>-<n>.json` y responda "sin decisión".
2. Provocar una llamada de cada tool relevante: shell, lectura, escritura nueva, edición, patch multi-archivo, MCP.
3. Versionar los payloads (sanitizados) como fixtures en `tests/hosts/fixtures/<host>/`.
4. Ajustar las tablas de [09 §3](./09-propuesta-de-arquitectura.md#3-el-contrato-interno) con lo observado.

---

## 4. Verificaciones Pendientes (consolidado)

| # | Host | Pregunta | Impacto si sale mal |
| :--- | :--- | :--- | :--- |
| 1 | Codex | ¿unified exec (`exec_command`) dispara `PreToolUse` `Bash`? ¿llega `workdir`? | Comandos sin inspeccionar / cwd incorrecto |
| 2 | Codex | ¿`additionalContext` sin `permissionDecision` es válido? | Se pierde `git_recoverable_delete` |
| 3 | Codex | ¿`exit 2` bloquea en Linux en la versión actual? | Usar solo JSON `deny` (ya propuesto) |
| 4 | Codex | ¿Un plugin puede aportar reglas o `AGENTS.md`? | La regla queda como skill |
| 5 | Copilot | Argumentos nativos de `view`, `create`, `edit`, `str_replace_editor` | `detect_secrets`/`protect_files` ciegos |
| 6 | Copilot | ¿Ignora `hookSpecificOutput`? | `ask`/`allow` perdidos sin salida plana |
| 7 | Copilot | ¿`CLAUDE_PLUGIN_ROOT` en hooks del CLI? | El comando no encuentra el script |
| 8 | Cursor | `tool_input` real de `Write` en `preToolUse` | `detect_secrets` ciego |
| 9 | Cursor | ¿`cursor-agent` ejecuta hooks de plugins? | Cobertura solo en IDE |
| 10 | Cursor | ¿`.cursor-plugin/plugin.json` acepta ruta de hooks propia? | Define si se necesita `adapters/cursor/` como raíz |
| 11 | Antigravity | ¿Los hooks corren en IDE y App? | Cobertura solo en `agy` |
| 12 | Antigravity | Variables de entorno, cwd y timeout del hook | Resolución de raíces |
| 13 | Antigravity | Campos de `ReplacementChunks` | Multi-edit sin escanear |
| 14 | Antigravity | ¿Qué hace `agy plugin import claude` con Synapse? | Podría ahorrar parte de F6 o introducir una falla abierta como la de Cursor |
| 15 | OpenCode | ¿V1 `permission.ask` se arregló? | `ask` en V1 |
| 16 | OpenCode | Latencia de `spawn` de Python por llamada | Experiencia de uso |
| 17 | Gemini CLI | ¿`decision:"ask"` tiene efecto en runtime? | `ask` perdido |
| 18 | Herdr | ¿`send_keys` funciona contra un agente bloqueado? | Un agente aprueba el `ask` de otro |

---

## 5. Riesgos Transversales

- **Confianza por hash (Codex) y huella (Gemini):** cada release de Synapse obliga al usuario a re-aprobar los hooks. Hay que comunicarlo en el CHANGELOG y considerar releases menos frecuentes de los scripts.
- **Plugins con hooks fuera de directorios públicos:** Codex no admite plugins con hooks en su directorio público. La distribución sería por marketplace propio (`bypabloc/claude-plugins` ya cumple el formato `.claude-plugin/marketplace.json`, que Codex y Copilot leen).
- **Laya y timeouts:** Copilot falla abierto por timeout (30 s); Antigravity usa 30 s; Gemini 60 s. El daemon caliente (~30 ms) no es problema; el arranque en frío (~5 s) tampoco, salvo en CPU lenta. Declarar timeouts explícitos en cada manifiesto.
- **Deriva de APIs:** Antigravity, OpenCode V2 y los hooks de Copilot en VS Code cambiaron en 2026. Fijar versiones mínimas en el README de cada adaptador y re-verificar en cada release mayor.
