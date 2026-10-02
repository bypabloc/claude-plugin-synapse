#!/usr/bin/env python3
"""PreToolUse hook para Edit y Write — Detección de secretos potenciada por Laya System 1.

Reemplaza: /home/bypabloc/projects/bypabloc/optical-soft/.claude/hooks/detect-secrets.py

Contexto y Propósito:
  1. Intercepta operaciones de modificación de archivos vía herramientas Edit (new_string) y Write (content).
  2. Detección en dos capas (defensa en profundidad):
     - Capa 1: Firmas regex de alta precisión (OpenAI sk-*, AWS AKIA*, GitHub ghp_*, Slack tokens,
       bloques de clave privada RSA/SSH/PGP y passwords en texto plano).
     - Capa 2: Evaluación semántica con Laya System 1 para detectar tokens no catalogados,
       firmas JWT y credenciales ofuscadas.
  3. Filtrado de falsos positivos: Autoriza inmediatamente placeholders evidentes
     ('REPLACE_ME', 'your-api-key-here', '<insert-key>', 'dummy', consultas a 'os.environ.get').
  4. Fallback automático a las firmas estáticas si no hay GPU o falla Laya.

Vínculos con la investigación en docs/research/laya/:
  - docs/research/laya/08-casos-de-uso-y-patrones-arquitecturales.md:
      Detección de fuga de secretos y sanitización de payloads de código en tiempo real.
  - docs/research/laya/03-guia-de-uso-y-primitivas.md:
      Clasificación multiclase con Laya para distinguir credenciales reales de placeholders.
  - docs/research/laya/10-tips-comunidad-antipatrones-y-limites-honestos.md:
      Filtrado de umbral de confianza estricto (confidence >= 0.70) para mitigar falsos positivos.

Ejemplos de Uso en CLI:
  # 1. Caso detección de secreto real (bloqueado):
  $ echo '{"tool_name": "Edit", "tool_input": {"new_string": "OPENAI_KEY = \"sk-proj-ab12cd34ef56gh78ij90kl12mnop34\""}}' | python src/detect_secrets.py --gpu
  -> 🚨 Possible secret detected! Type: OpenAI API Key format [exit 2]

  # 2. Caso detección semántica por Laya (JWT):
  $ echo '{"tool_name": "Edit", "tool_input": {"new_string": "JWT = \"eyJhbGciOiJIUzI1Ni...\""}}' | python src/detect_secrets.py --gpu
  -> 🚨 Possible secret detected via Laya System 1! Detected credential category: api_token [exit 2]

  # 3. Caso placeholder seguro (permitido):
  $ echo '{"tool_name": "Edit", "tool_input": {"new_string": "api_key = \"your-api-key-here\""}}' | python src/detect_secrets.py --gpu
  -> [salida limpia, exit 0]
"""

from __future__ import annotations

import os
import re
import sys

# Agregar directorio actual a sys.path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from common import (
    emit_block,
    get_laya_router,
    read_hook_input,
    record_audit_log,
    should_use_laya,
)

HIGH_CONFIDENCE_PATTERNS = [
    (r"-----BEGIN (?:RSA|OPENSSH|DSA|EC|PGP)?\s?PRIVATE KEY-----", "Private cryptographic key"),
    (r"sk-(?:proj-)?[a-zA-Z0-9_\-]{20,}", "OpenAI API Key format"),
    (r"ghp_[a-zA-Z0-9]{36}", "GitHub Personal Access Token"),
    (r"AKIA[0-9A-Z]{16}", "AWS Access Key ID"),
    (r"xox[baprs]-[0-9a-zA-Z]{10,48}", "Slack Token format"),
    (r"(?i)api[_-]?key\s*[:=]\s*[\"']([a-zA-Z0-9_\-]{24,})[\"']", "Generic API Key assignment"),
    (r"(?i)password\s*[:=]\s*[\"']([^\"']{8,})[\"']", "Plaintext password assignment"),
]

# Placeholders y valores mock conocidos delimitados por palabras
PLACEHOLDER_RE = re.compile(
    r"(?i)\b(replace_me|your[-_]?(?:api[-_]?)?key|dummy|placeholder|<insert|test[-_]?secret|xxx|sample[-_]?key)\b|os\.(?:environ|getenv)\b"
)

SECRET_QUESTIONS = {
    "secret_status": {
        "type": "choice",
        "instructions": "Determine if this text snippet contains genuine confidential secrets/credentials or if it is safe code/placeholders.",
        "criteria": {
            "confidential_secret": "contains genuine live credentials, private keys, high-entropy tokens, or hardcoded passwords",
            "safe_or_placeholder": "contains mock tokens, placeholder values (e.g. 'REPLACE_ME', 'your-key'), variable lookups, or standard code"
        }
    },
    "secret_type": {
        "type": "choice",
        "instructions": "Classify the credential type found in the text snippet.",
        "criteria": {
            "api_token": "API key, OAuth token, JWT, Bearer token, or cloud access key",
            "password_or_connection": "plaintext password or database connection string with password",
            "private_crypto_key": "RSA, SSH, or TLS private key block",
            "none": "no secrets present"
        }
    }
}


def evaluate_content_secrets(content: str, tool_name: str = "Edit/Write") -> None:
    """Evalúa si el contenido contiene secretos utilizando firmas y Laya System 1."""
    if not content or not content.strip():
        sys.exit(0)

    # Si contiene un placeholder o lectura de variable de entorno explícita, autorizar
    if PLACEHOLDER_RE.search(content):
        record_audit_log("ALLOW", "detect_secrets", tool_name, content[:60], "Placeholder seguro detectado")
        sys.exit(0)

    # 1. Chequeo preliminar con patrones de alta precisión
    found_pattern_desc: str | None = None
    for pattern, desc in HIGH_CONFIDENCE_PATTERNS:
        match = re.search(pattern, content)
        if match:
            found_pattern_desc = desc
            break

    if found_pattern_desc:
        emit_block(
            f"🚨 Possible secret detected!\n"
            f"Type: {found_pattern_desc}\n"
            "Please remove sensitive credentials or use environment variables instead.",
            exit_code=2,
            hook_name="detect_secrets",
            tool_name=tool_name,
            target=content[:60],
        )

    # 2. Si no es un patrón estático pero contiene asignaciones sospechosas, consultar a Laya System 1
    suspicious = bool(re.search(r"(?i)(token|key|secret|credential|auth|bearer|pass)", content))
    if suspicious and len(content) <= 4000 and should_use_laya():
        try:
            router = get_laya_router()
            res = router.predict(content, SECRET_QUESTIONS)
            answers = res["answers"]

            status = answers["secret_status"]["choice"]
            status_conf = answers["secret_status"]["answer_confidence"]
            secret_type = answers["secret_type"]["choice"]

            if status == "confidential_secret" and status_conf >= 0.70 and secret_type != "none":
                emit_block(
                    f"🚨 Possible secret detected via Laya System 1!\n"
                    f"Detected credential category: {secret_type} (confidence: {status_conf:.2f})\n"
                    "Please remove sensitive credentials or reference environment variables instead.",
                    exit_code=2,
                    hook_name="detect_secrets",
                    tool_name=tool_name,
                    target=content[:60],
                )
        except Exception:
            # Fallback transparente a verificación por firmas estáticas
            pass

    record_audit_log("ALLOW", "detect_secrets", tool_name, content[:60], "Contenido libre de secretos")
    sys.exit(0)


def main() -> None:
    data = read_hook_input()
    if not data:
        sys.exit(0)

    tool_name = data.get("tool_name", "")
    content = ""

    if tool_name == "Edit":
        content = data.get("tool_input", {}).get("new_string", "")
    elif tool_name == "Write":
        content = data.get("tool_input", {}).get("content", "")
    else:
        sys.exit(0)

    evaluate_content_secrets(content, tool_name)


if __name__ == "__main__":
    main()
