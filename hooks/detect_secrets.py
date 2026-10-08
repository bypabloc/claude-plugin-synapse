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
  $ echo '{"tool_name": "Edit", "tool_input": {"new_string": "OPENAI_KEY = \"sk-proj-ab12cd34ef56gh78ij90kl12mnop34\""}}' | python hooks/detect_secrets.py --gpu
  -> 🚨 Possible secret detected! Type: OpenAI API Key format [exit 2]

  # 2. Caso detección semántica por Laya (JWT):
  $ echo '{"tool_name": "Edit", "tool_input": {"new_string": "JWT = \"eyJhbGciOiJIUzI1Ni...\""}}' | python hooks/detect_secrets.py --gpu
  -> 🚨 Possible secret detected via Laya System 1! Detected credential category: api_token [exit 2]

  # 3. Caso placeholder seguro (permitido):
  $ echo '{"tool_name": "Edit", "tool_input": {"new_string": "api_key = \"your-api-key-here\""}}' | python hooks/detect_secrets.py --gpu
  -> [salida limpia, exit 0]
"""

from __future__ import annotations

import math
import os
import re
import sys

# Agregar directorio actual a sys.path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from common import (
    emit_block,
    emit_decision,
    get_laya_router,
    log_step,
    read_hook_input,
    record_audit_log,
    run_main,
    should_use_laya,
)

HIGH_CONFIDENCE_PATTERNS = [
    (r"-----BEGIN (?:RSA|OPENSSH|DSA|EC|PGP)?\s?PRIVATE KEY-----", "Private cryptographic key"),
    (r"sk-(?:proj-)?[a-zA-Z0-9_\-]{20,}", "OpenAI API Key format"),
    (r"sk-ant-(?:api\d{2}-)?[a-zA-Z0-9_\-]{20,}", "Anthropic API Key format"),
    (r"AIza[0-9A-Za-z\-_]{35}", "Google API / Gemini Key format"),
    (r"gh[pousr]_[a-zA-Z0-9]{36,255}", "GitHub Access Token"),
    (r"AKIA[0-9A-Z]{16}", "AWS Access Key ID"),
    (r"xox[baprs]-[0-9a-zA-Z]{10,48}", "Slack Token format"),
    (r"(?:sk|rk)_(?:live|test)_[0-9a-zA-Z]{24,}", "Stripe API Key format"),
    (r"(?i)api[_-]?key\s*[:=]\s*[\"']([a-zA-Z0-9_\-]{24,})[\"']", "Generic API Key assignment"),
    (r"(?i)password\s*[:=]\s*[\"']([^\"']{8,})[\"']", "Plaintext password assignment"),
]

# Placeholders y valores mock conocidos delimitados por palabras
PLACEHOLDER_RE = re.compile(
    r"(?i)\b(replace_me|your[-_]?(?:api[-_]?)?key|dummy|placeholder|<insert|test[-_]?(?:secret|key|token)|xxx|sample[-_]?key|example[-_]?(?:key|token|secret)|my[-_]?secret)\b|os\.(?:environ|getenv)\b|process\.env\b"
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


SENSITIVE_WORD_RE = re.compile(r"(?i)(token|key|secret|credential|auth|bearer|pass)")
URL_WITH_PASSWORD_RE = re.compile(r"[a-zA-Z][a-zA-Z0-9+.-]*://[^\s/:@'\"]+:[^\s/@'\"]+@")
# Valores literales: entre comillas, o tras = / : sin comillas (FOO=valor en .env, YAML)
LITERAL_RE = re.compile(r"""(['"`])([^'"`\s]{8,})\1|[=:]\s*([^\s'"`,;)\]}]{12,})""")
IDENTIFIER_RE = re.compile(r"[a-z]+([_-][a-z0-9]+)+|[A-Z][A-Z0-9_]+")
# Llamadas a función (secrets.token_bytes(32), hashlib.sha256(...)): código, no un valor
CALL_EXPR_RE = re.compile(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*\(")
LAYA_MAX_SECRET_CHARS = 4000


def _shannon(value: str) -> float:
    counts = {c: value.count(c) for c in set(value)}
    return -sum(n / len(value) * math.log2(n / len(value)) for n in counts.values())


def looks_like_secret(value: str, near_keyword: bool) -> bool:
    """Literal con forma de credencial: mezcla letras y dígitos, sin espacios, con entropía alta.

    Rutas, URLs sin credenciales, nombres con puntos (a.b.c, versiones), identificadores snake/kebab,
    CONSTANTES, llamadas a función y texto natural no califican. En una línea con palabra sensible
    basta con 8 caracteres; sin ella se exigen 16.
    """
    if "/" in value or value.startswith(("http", "$", "{", "<", "%")) or value.count(".") >= 2:
        return False
    if CALL_EXPR_RE.match(value):
        return False
    if not (re.search(r"\d", value) and re.search(r"[A-Za-z]", value)) or IDENTIFIER_RE.fullmatch(value):
        return False
    min_len = 8 if near_keyword else 16
    return len(value) >= min_len and _shannon(value) >= (2.5 if near_keyword else 3.0)


def candidate_lines(content: str) -> list[str]:
    """Líneas con algo que podría ser una credencial real; sin ninguna, Laya no tiene nada que evaluar."""
    found = []
    for line in content.splitlines():
        if URL_WITH_PASSWORD_RE.search(line):
            found.append(line.strip())
            continue
        near = bool(SENSITIVE_WORD_RE.search(line))
        values = [m.group(2) or m.group(3) for m in LITERAL_RE.finditer(line)]
        if any(looks_like_secret(v, near) for v in values if v):
            found.append(line.strip())
    return found


def evaluate_content_secrets(content: str, tool_name: str = "Edit/Write") -> None:
    """Evalúa si el contenido contiene secretos utilizando firmas y Laya System 1."""
    if not content or not content.strip():
        sys.exit(0)

    # Si contiene un placeholder o lectura de variable de entorno explícita, autorizar
    target = f"<{len(content)} caracteres>"
    if PLACEHOLDER_RE.search(content):
        record_audit_log("ALLOW", "detect_secrets", tool_name, target, "Placeholder seguro detectado", rule="content.placeholder")
        sys.exit(0)

    # 1. Chequeo preliminar con patrones de alta precisión
    found_pattern_desc: str | None = None
    for pattern, desc in HIGH_CONFIDENCE_PATTERNS:
        match = re.search(pattern, content)
        if match:
            found_pattern_desc = desc
            break

    log_step("signatures", result="match" if found_pattern_desc else "no_match", category=found_pattern_desc, checked=len(HIGH_CONFIDENCE_PATTERNS))
    if found_pattern_desc:
        emit_block(
            f"Posible secreto en el contenido: {found_pattern_desc}.\n"
            "Quita la credencial o léela desde una variable de entorno.",
            hook_name="detect_secrets", tool_name=tool_name, target=target,
            decided_by="python", rule="signature", evidence={"category": found_pattern_desc},
        )

    # 2. Laya solo ve las líneas con literales candidatos (alta entropía, URL con contraseña): sin ninguno,
    #    no hay credencial posible. En la traza real, 128 de 133 consultas por palabra clave fueron 'safe'.
    candidates = candidate_lines(content)
    log_step("candidates", count=len(candidates))
    if not candidates:
        log_step("laya", result="skipped", reason="sin literales candidatos (determinista)")
    elif should_use_laya():
        try:
            router = get_laya_router()
            res = router.predict("\n".join(candidates)[:LAYA_MAX_SECRET_CHARS], SECRET_QUESTIONS)
            answers = res["answers"]

            status = answers["secret_status"]["choice"]
            status_conf = answers["secret_status"]["answer_confidence"]
            secret_type = answers["secret_type"]["choice"]
            log_step("laya", result="ok", status=status, status_conf=round(status_conf, 3), secret_type=secret_type)

            # Laya solo escala a confirmación: los bloqueos son las firmas deterministas de arriba
            if status == "confidential_secret" and status_conf >= 0.70 and secret_type != "none":
                emit_decision(
                    "ask",
                    f"el modelo sospecha una credencial en el contenido ({secret_type}, confianza {status_conf:.2f} ≥ 0.70, "
                    f"{len(candidates)} línea(s) candidata(s)). Si es real, usa variables de entorno.",
                    hook_name="detect_secrets", tool_name=tool_name, target=target, decided_by="laya", rule="laya.secret",
                    evidence={"secret_type": secret_type, "confidence": round(status_conf, 3), "candidate_lines": len(candidates)},
                )
        except Exception as exc:
            # Fallback a las firmas deterministas, pero el fallo queda en la traza
            log_step("laya", result="error", error=repr(exc))

    record_audit_log("ALLOW", "detect_secrets", tool_name, target, "Contenido libre de secretos", rule="sin_objeciones")
    sys.exit(0)


def main() -> None:
    data = read_hook_input("detect_secrets")
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
    run_main(main)
