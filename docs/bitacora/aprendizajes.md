# Aprendizajes de Synapse

Lecciones que valen más allá de una versión. Cada una nombra dónde se aprendió.

## Diseño de reglas

1. Lo que una regla puede decidir no se deja al modelo: Laya solo ve lo ambiguo (1.2.0, 1.4.0, 1.6.0).
2. Un clasificador con falsos positivos solo puede escalar a `ask`, nunca bloquear (1.0.1, 1.0.2).
3. Cada `block` o `ask` nombra quién decidió y con qué regla. Sin eso no se puede depurar un falso positivo (1.2.0).
4. La traza real es el banco de pruebas: cada falso positivo se fija en un caso, junto a un valor parecido que debe
   seguir llegando a Laya (1.4.1, 1.6.0).
5. Si el modelo da un score alto con evidencia neutra, reacciona al vocabulario: hace falta una regla, no subir
   el umbral (1.1.0).
6. La tokenización de shell conserva las comillas y no cruza separadores de subcomando. Si no, se analiza un
   comando distinto del que se ejecuta (1.5.0).
7. Cuando un argumento es un programa (`jq`, `sed`, `awk`) y no una ruta, se corrige donde se extraen las rutas, no
   con parches en cada consumidor (1.4.1, 1.6.1).
8. Un auto-allow debe probar que todo lo afectado es recuperable o desechable en el repo del proyecto. Las fugas
   habituales son los symlinks, los repos padre y los comandos compuestos (1.3.0).

## Operación en hosts reales

9. En Codex, un `ask` detiene al agente hasta que vuelve el usuario, aunque lleve horas trabajando solo. Una regla
   que en Claude Code cuesta un clic en Codex cuesta horas, y el texto del `deny` debe decir cómo seguir (1.7.1).
10. Las instrucciones en Markdown (`AGENTS.md`, `CLAUDE.md`, `.agents/rules/*.md`) no son permisos. Proteger lo
    que el agente edita a diario genera esperas sin aumentar la seguridad (1.7.1).
11. Windows no habla UTF-8 por defecto: todo lo que vuelve al host se escribe en UTF-8 explícito (1.7.1).
12. Un hook declarado a mano en `~/.codex/hooks.json` que apunta a un clon borrado falla en silencio en cada tool.
    La vigilancia revisa que cada script de hook exista (2026-11-21).
