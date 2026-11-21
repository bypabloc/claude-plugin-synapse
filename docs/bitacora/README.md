# Bitácora de Synapse

Registro versionado de cómo evoluciona el plugin: qué se cambió en cada versión y por qué, cómo se vigila en
sesiones reales y qué se aprendió. Los commits de 1.0.0 a 1.7.0 son del espejo `agent-plugin-synapse`, que conserva
la historia previa al monorepo; desde 1.7.1 son del monorepo.

| Archivo | Qué guarda |
| :--- | :--- |
| [versiones/](versiones/) | Un archivo por versión: síntoma, causa, cambio, prueba que lo cubre y lección. |
| [vigilancia.md](vigilancia.md) | Cómo se vigila el plugin en sesiones reales y qué mirar en cada revisión. |
| [aprendizajes.md](aprendizajes.md) | Lecciones de diseño y de operación que valen más allá de una versión. |
| [mejoras-pendientes.md](mejoras-pendientes.md) | Ideas detectadas que todavía no se implementaron, con su motivo. |

## Versiones

| Versión | Fecha | Commit | Resumen |
| :--- | :--- | :--- | :--- |
| [1.7.1](versiones/1.7.1.md) | 2026-11-21 | `bb08bf7` | Codex no se detiene por una regla `.md` y deja de mostrar mojibake en Windows |
| [1.7.0](versiones/1.7.0.md) | 2026-11-20 | `d6d09a7` | Núcleo multi-agente con adaptadores de Codex, Antigravity y OpenCode; golden 1.6.1 idéntico |
| [1.6.1](versiones/1.6.1.md) | 2026-11-16 | `477c7bd` | El script de `sed -i`/`perl -i` ya no cuenta como la ruta escrita |
| [1.6.0](versiones/1.6.0.md) | 2026-11-16 | `42c9e2a` | Laya apagada por defecto y reglas de rutina aprendidas de la traza de klinikae |
| [1.5.0](versiones/1.5.0.md) | 2026-10-31 | `8859f7e` | Contraseñas exactas permitidas por proyecto, force push por subcomando y `cd -` |
| [1.4.1](versiones/1.4.1.md) | 2026-10-11 | `0737c97` | Filtros de jq/rg y literales de fixture dejan de pedir confirmación |
| [1.4.0](versiones/1.4.0.md) | 2026-10-08 | `a2d79b2` | `uv lock\|sync` y `bun\|pnpm install` sin paquetes se deciden sin Laya |
| [1.3.1](versiones/1.3.1.md) | 2026-10-08 | `024a67b` | Las llamadas a función dejan de ser candidatas a secreto |
| [1.3.0](versiones/1.3.0.md) | 2026-10-07 | `9b314fe` | Borrar contenido versionado y limpio se aprueba, sin huecos de symlink ni compuestos |
| [1.2.0](versiones/1.2.0.md) | 2026-10-03 | `24c450b` | Daemon de Laya (de 6 s a 70 ms), compuerta determinista y atribución de cada decisión |
| [1.1.0](versiones/1.1.0.md) | 2026-10-02 | `e1febe1` | Laya afinada (FPR 0.29 %, recall 78 %) y bloqueo de escrituras desde Bash |
| [1.0.2](versiones/1.0.2.md) | 2026-10-02 | `61fa976` | Reglas deterministas de alcance y exfiltración; Laya solo puede pedir `ask` |
| [1.0.1](versiones/1.0.1.md) | 2026-10-02 | `8bf2593` | Laya no bloquea lo que llama seguro; los archivos sin versionar son desechables |
| [1.0.0](versiones/1.0.0.md) | 2026-10-01 | `da97e8d` | Cuatro hooks PreToolUse con Laya y respaldo determinista, como plugin |

## Cómo añadir una versión

1. `python scripts/bump.py synapse X.Y.Z` desde la raíz del monorepo.
2. Copia la plantilla en `versiones/X.Y.Z.md`. El síntoma se escribe tal como se vio (proyecto, host, regla, hora),
   no la solución.
3. Nombra el test o el caso de `tests/cases/*.json` que lo reproduce. Debe haber fallado antes del cambio.
4. Añade la fila a la tabla de arriba y, si la lección sirve más allá de esta versión, súmala a
   [aprendizajes.md](aprendizajes.md).

```markdown
# X.Y.Z — <título corto>

- **Fecha**: AAAA-MM-DD · **Commit**: `abc1234` · **Tipo**: fix | feat
- **Visto en**: <proyecto, host, entorno y sesión, o "diseño">

## Síntoma
## Causa
## Cambio
## Prueba
## Lección
```
