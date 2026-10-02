---
name: synapse-status
description: Inspecciona el estado de los guardrails de seguridad Synapse, modo de hardware (GPU/CPU/Fallback) y registros de auditoría.
disable-model-invocation: false
---

# Synapse Security Status Check

Cuando el usuario invoque este skill o solicite verificar el estado de Synapse:

1. **Verificar Estado de Hardware e Inferencia**:
   - Comprobar si CUDA/GPU está disponible mediante Python.
   - Indicar si el motor opera en modo GPU (~25ms), CPU (~700ms) o Fallback determinístico (<1ms).

2. **Verificar Archivos de Auditoría**:
   - Inspeccionar las últimas 10 líneas de `~/.claude/logs/security_hooks.log` o `./logs/security_hooks.log` si existen.
   - Resumir las decisiones recientes: acciones aprobadas (`ALLOW`), consultas al usuario (`ASK`) y bloqueos (`BLOCKED`).

3. **Verificar Políticas Activas**:
   - Auto-aprobación incondicional de temporales `./tmp/**`.
   - Bloqueo de lectura directa de `.env*` con sugerencia `source`.
   - Detección de secretos (OpenAI, AWS, GitHub, JWT, passwords).
   - Protección de lockfiles (`package-lock.json`, `uv.lock`, etc.) y `.git/`.
