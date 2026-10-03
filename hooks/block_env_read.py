#!/usr/bin/env python3
"""PreToolUse hook — Bloqueo de lectura directa de archivos .env potenciado por Laya System 1.

Reemplaza: /home/bypabloc/projects/bypabloc/optical-soft/.claude/hooks/block-env-read.py

Contexto y Propósito:
  1. Bloquea la LECTURA directa de archivos de credenciales (.env*) tanto vía la herramienta Read
     como vía comandos Bash (cat, head, tail, awk, sed, grep, cut, base64, xxd).
  2. Permite explícitamente el uso legítimo de variables mediante 'source .env' o '. .env',
     cargando las variables en el proceso Bash sin que los secretos pasen al contexto del LLM.
  3. Infiere e imprime las KEYS detectadas junto a su formato sin revelar el valor real
     (e.g., <URL, 24 caracteres>, <booleano>, <numérico, 4 dígitos>, <texto, 12 caracteres>).
  4. Evaluación semántica con Laya System 1 para detectar scripts y comandos de volcado no convencionales.
  5. Fallback automático a verificación sintáctica determinística si no hay GPU o falla Laya.

Vínculos con la investigación en docs/research/laya/:
  - docs/research/laya/08-casos-de-uso-y-patrones-arquitecturales.md:
      Filtrado de datos y prevención de exfiltración de credenciales en agentes autónomos.
  - docs/research/laya/09-recetario-de-ejemplos-practicos.md:
      Técnicas de ofuscación y formateo seguro de metadatos de configuración.
  - docs/research/laya/04-el-router-y-multilingue.md:
      Clasificación de intenciones de comandos shell mediante esquemas tipados.

Ejemplos de Uso en CLI:
  # 1. Caso lectura directa con cat (bloqueado con inferencia de tipos):
  $ echo '{"tool_name": "Bash", "tool_input": {"command": "cat .env"}}' | python hooks/block_env_read.py --gpu
  -> 🚫 BLOCKED: Lectura directa de archivo de credenciales: .env
     Si necesitas USAR las variables (no verlas), usa en Bash:
       set -a; source .env; set +a
     Keys disponibles (formato inferido, valores NUNCA mostrados):
       DATABASE_URL=<URL, 32 caracteres>
       DEBUG=<booleano> [exit 2]

  # 2. Caso lectura con herramienta Read (bloqueado):
  $ echo '{"tool_name": "Read", "tool_input": {"file_path": ".env.production"}}' | python hooks/block_env_read.py --gpu
  -> 🚫 BLOCKED: Lectura directa de archivo de credenciales [exit 2]

  # 3. Caso carga legítima con source (permitido):
  $ echo '{"tool_name": "Bash", "tool_input": {"command": "set -a; source .env; set +a"}}' | python hooks/block_env_read.py --gpu
  -> [salida limpia, exit 0]
"""

from __future__ import annotations

import os
import re
import shlex
import sys

# Agregar directorio actual a sys.path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from common import (
    build_env_block_message,
    emit_block,
    emit_decision,
    extract_bash_tokens,
    get_laya_router,
    log_step,
    is_env_file,
    read_hook_input,
    record_audit_log,
    resolve_path,
    run_main,
    should_use_laya,
)

READ_COMMANDS = {
    "cat", "head", "tail", "less", "more", "batcat", "bat",
    "awk", "sed", "grep", "egrep", "fgrep", "cut", "xxd", "base64",
    "strings", "hexdump", "od", "nl", "pr", "view", "nano", "vim", "vi",
}

ENV_INTENT_QUESTIONS = {
    "env_intent": {
        "type": "choice",
        "instructions": "Determine if this shell command is attempting to read, display, or exfiltrate contents of an environment (.env) file or credentials, versus loading/sourcing it or running unaffected commands.",
        "criteria": {
            "read_or_exfiltrate": "reading, displaying, dumping, catting, grepping, or extracting secrets from a .env file",
            "source_or_load": "sourcing or loading variables into the shell process without displaying values (e.g. source .env, . .env, set -a)",
            "safe_or_unrelated": "command does not attempt to read or display private environment file contents"
        }
    }
}


def find_env_args_in_bash(command: str, cwd: str) -> list[str]:
    """Detecta si algun sub-comando de lectura clasica toma un .env como argumento de entrada."""
    parts = extract_bash_tokens(command)
    hits: list[str] = []

    for part in parts:
        try:
            tokens = shlex.split(part, posix=True)
        except ValueError:
            continue
        if not tokens:
            continue

        cmd = os.path.basename(tokens[0])
        if cmd not in READ_COMMANDS:
            continue

        # Evitar falsos positivos si el token es destino de redireccion (e.g. cat > .env << 'EOF')
        skip_next = False
        for tok in tokens[1:]:
            if skip_next:
                skip_next = False
                continue
            if tok in {">", ">>", "<<", "<<<", "<"}:
                skip_next = True
                continue
            if tok.startswith(("-", ">", "<")):
                continue

            resolved = resolve_path(tok, cwd)
            if is_env_file(resolved):
                hits.append(resolved)

    return hits


SAFE_ENV_CMDS = {"ls", "eza", "exa", "stat", "test", "[", "[[", "file", "touch", "cp", "mv", "rm", "chmod", "mkdir", "du", "wc"}
SAFE_GIT_ENV_SUBCMDS = {"check-ignore", "ls-files", "status", "add", "rm", "mv"}
ENV_FILE_FLAGS = ("--env-file", "env_file")
CODE_READ_RE = re.compile(r"open\(|read_text|readFileSync|readFile\(|file_get_contents|dotenv_values|File\.read|IO\.read")
QUOTED_RE = re.compile(r"""['"]([^'"\n]+)['"]""")
INTERPRETER_RE = re.compile(r"^(python|node|bun|deno|ruby|perl|php)[\d.]*$")


def _is_env_token(token: str) -> bool:
    name = os.path.basename(token.split("=", 1)[-1])
    return is_env_file(name) or bool(re.match(r"^\.env[*?\[]", name))  # .env* también nombra archivos .env


def env_mentions(command: str) -> list[tuple[list[str], int]]:
    """(tokens del sub-comando, índice) de cada argumento que nombra un archivo .env."""
    shell = re.sub(r"<<-?\s*(['\"]?)(\w+)\1\n.*?\n\2(\n|$)", "\n", command, flags=re.S)  # sin cuerpos de heredoc
    found = []
    for part in extract_bash_tokens(shell):
        try:
            tokens = shlex.split(part, posix=True)
        except ValueError:
            continue
        found += [(tokens, i) for i, tok in enumerate(tokens) if i and _is_env_token(tok)]
    return found


def embedded_env_refs(command: str) -> list[str]:
    """Código embebido (cuerpo de heredoc de un intérprete, -c/-e) que menciona un archivo .env entre comillas."""
    codes = [m.group(3) for m in re.finditer(r"(\S+)\s+-?\s*<<-?\s*(['\"]?)\w+\2\n(.*?)\n\w+(\n|$)", command, flags=re.S)
             if INTERPRETER_RE.match(os.path.basename(m.group(1)))]
    try:
        tokens = shlex.split(command.split("\n", 1)[0], posix=True)
    except ValueError:
        tokens = []
    codes += [tokens[i + 1] for i, t in enumerate(tokens[:-1]) if t in {"-c", "-e", "--eval"}]
    return [code for code in codes if any(_is_env_token(q) for q in QUOTED_RE.findall(code))]


def _is_safe_env_use(mention: tuple[list[str], int]) -> bool:
    tokens, i = mention
    if tokens[i - 1] in ENV_FILE_FLAGS or tokens[i].startswith(ENV_FILE_FLAGS):
        return True
    cmd = os.path.basename(tokens[0])
    if cmd == "git":
        sub = next((t for t in tokens[1:] if not t.startswith("-")), "")
        return sub in SAFE_GIT_ENV_SUBCMDS
    return cmd in SAFE_ENV_CMDS


def evaluate_bash_env_read(command: str, cwd: str) -> None:
    """Inspecciona el comando Bash combinando analisis sintactico y clasificacion semantica Laya."""
    if not command.strip():
        sys.exit(0)

    # 1. Chequeo rapido sintactico de comandos de lectura tradicionales
    hits = find_env_args_in_bash(command, cwd)
    log_step("syntax.env_read", result="match" if hits else "no_match", hits=hits)
    bash = {"hook_name": "block_env_read", "tool_name": "Bash"}
    source_hint = "Usa 'set -a; source <env-file>; set +a' para cargar variables en el proceso sin exponer valores."
    if hits:
        emit_block(build_env_block_message(hits[0]), target=hits[0], decided_by="python", rule="syntax.env_read",
                   evidence={"files": hits}, **bash)

    # Sin un archivo .env real entre los argumentos ni en el código embebido, no hay nada que evaluar.
    # (`os.environ`, `process.env.X`, `load_dotenv` no son archivos: antes llegaban a Laya por el texto ".env")
    mentions = env_mentions(command)
    body_refs = embedded_env_refs(command)
    if not mentions and not body_refs:
        record_audit_log("ALLOW", "block_env_read", "Bash", command, "Comando sin referencia a archivos .env", rule="env.no_reference")
        sys.exit(0)

    # Código embebido (heredoc, -c/-e) que abre un .env: lectura programática, igual que la regla de una línea
    if any(CODE_READ_RE.search(code) for code in body_refs):
        emit_block(f"Lectura programática de archivo .env en código embebido (heredoc o -c/-e).\nComando: {command}\n{source_hint}",
                   target=command, decided_by="python", rule="syntax.embedded_env_read", **bash)

    # Permitir patron legitimo de source explicito
    if re.search(r"(?:^|\s|;)(?:source|\.)\s+[^\s;&|]+\.env", command):
        # Verificar que no contenga pipes hacia comandos de impresion o echo
        if not any(f"| {rc}" in command or f"|{rc}" in command for rc in READ_COMMANDS):
            record_audit_log("ALLOW", "block_env_read", "Bash", command, "Carga legítima mediante source autorizada", rule="env.source")
            sys.exit(0)

    # Detección determinística de lectura mediante scripts de una línea (Python, Node, Ruby, Perl)
    scripting_read_pat = r"(?i)(?:python\d*|node|perl|ruby|php)\s+.*(?:\.read\(|readFileSync|open\(|file_get_contents).*\.env"
    if re.search(scripting_read_pat, command):
        emit_block(f"Lectura programática de archivo .env con un script de una línea.\nComando: {command}\n{source_hint}",
                   target=command, decided_by="python", rule="syntax.script_env_read", **bash)

    # Usos que nunca muestran el contenido: listar, comprobar, copiar/mover, git sin diff, --env-file
    if mentions and not body_refs and all(_is_safe_env_use(m) for m in mentions):
        log_step("laya", result="skipped", reason="uso seguro de .env (determinista)")
        record_audit_log("ALLOW", "block_env_read", "Bash", command, "Uso de .env que no expone valores", rule="env.safe_use")
        sys.exit(0)

    # 2. Evaluación semántica con Laya System 1, solo para lo que el código no resuelve
    if should_use_laya():
        try:
            router = get_laya_router()
            res = router.predict(command, ENV_INTENT_QUESTIONS)
            answers = res["answers"]

            intent = answers["env_intent"]["choice"]
            conf = answers["env_intent"]["answer_confidence"]
            log_step("laya", result="ok", intent=intent, confidence=round(conf, 3))

            # Laya solo escala a confirmación: los bloqueos de .env son las reglas sintácticas de arriba
            if intent == "read_or_exfiltrate" and conf >= 0.70:
                emit_decision("ask", f"el modelo sospecha lectura de un archivo .env (confianza {conf:.2f} ≥ 0.70). {source_hint}",
                              target=command, decided_by="laya", rule="laya.env_intent",
                              evidence={"intent": intent, "confidence": round(conf, 3)}, **bash)
        except Exception as exc:
            # Fallback a las reglas deterministas, pero el fallo queda en la traza
            log_step("laya", result="error", error=repr(exc))

    record_audit_log("ALLOW", "block_env_read", "Bash", command, "Comando seguro autorizado", rule="sin_objeciones")
    sys.exit(0)


def main() -> None:
    data = read_hook_input("block_env_read")
    if not data:
        sys.exit(0)

    tool_name = data.get("tool_name")
    cwd = data.get("cwd") or os.getcwd()

    if tool_name == "Read":
        file_path = data.get("tool_input", {}).get("file_path", "")
        resolved = resolve_path(file_path, cwd)
        if resolved and is_env_file(resolved):
            emit_block(build_env_block_message(resolved), hook_name="block_env_read", tool_name="Read", target=resolved,
                       decided_by="python", rule="read_tool.env_file")
        record_audit_log("ALLOW", "block_env_read", "Read", file_path, "Lectura permitida (no es .env sensible)", rule="read_tool.not_env")
        sys.exit(0)

    if tool_name == "Bash":
        command = data.get("tool_input", {}).get("command", "")
        evaluate_bash_env_read(command, cwd)

    sys.exit(0)


if __name__ == "__main__":
    run_main(main)
