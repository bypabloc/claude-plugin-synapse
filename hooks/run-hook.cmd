: << 'CMDBLOCK'
@echo off
rem Lanzador políglota de Synapse (spec 001 · 02 §8): cmd.exe ejecuta este bloque, sh/bash el de abajo.
rem Uso: run-hook.cmd <entrada> --host <h>   (entrada = dispatch o el nombre de un hook)
rem Busca Python 3.10+; sin intérprete emite la salida segura del host y sale con 0 (un exit != 0 falla abierto).
setlocal EnableExtensions
set "HERE=%~dp0"
set "ENTRY=%~1"
set "ARGS=%*"
call set "REST=%%ARGS:*%~1=%%"
set "PY=%SYNAPSE_PYTHON%"
if not defined PY py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1 && set "PY=py -3"
if not defined PY python -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1 && set "PY=python"
if not defined PY python3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1 && set "PY=python3"
if defined PY (
  %PY% "%HERE%%ENTRY%.py" %REST%
  exit /b 0
)
echo %REST% | findstr /c:"antigravity" >nul && echo {"decision":"force_ask","reason":"Synapse: no se encontro Python 3.10 o superior (instalalo o define SYNAPSE_PYTHON); confirma manualmente"}
echo %REST% | findstr /c:"codex" >nul && echo {"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":"Synapse: no se encontro Python 3.10 o superior (instalalo o define SYNAPSE_PYTHON)"}}
exit /b 0
CMDBLOCK
# sh / bash
HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
ENTRY=$1
shift
for c in "${SYNAPSE_PYTHON:-}" python3 python; do
  [ -n "$c" ] || continue
  if $c -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' >/dev/null 2>&1; then
    exec $c "$HERE/$ENTRY.py" "$@"
  fi
done
case " $* " in
  *" antigravity "*) printf '%s\n' '{"decision":"force_ask","reason":"Synapse: no se encontró Python 3.10 o superior (instálalo o define SYNAPSE_PYTHON); confirma manualmente"}' ;;
  *" codex "*) printf '%s\n' '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":"Synapse: no se encontró Python 3.10 o superior (instálalo o define SYNAPSE_PYTHON)"}}' ;;
esac
exit 0
