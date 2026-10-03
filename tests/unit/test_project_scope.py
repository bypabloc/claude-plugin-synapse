"""block_dangerous: alcance del proyecto (entorno git), variables de shell resueltas y carpeta .git.

Escenarios tomados de la traza real (2026-10-03): worktrees dentro del repo, cwd fuera del proyecto,
symlinks de node_modules y bucles `for p in *; do rm -rf "$p"; done`.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

import common
from support import GIT_REPOS_DIR, make_git_repo, run_in_process

GIT = ["git", "-c", "user.email=t@t", "-c", "user.name=t"]


@pytest.fixture(scope="module")
def repo() -> Path:
    """Repo principal con dos worktrees anidados (como klinikae) y un symlink que sale del proyecto."""
    root = make_git_repo("scope", tracked={"client/package.json": "{}\n", "src/app.py": "x = 1\n"}, untracked={})
    subprocess.run([*GIT, "-C", str(root), "commit", "-qm", "init"], check=True)
    for rel, branch in ((".claude/worktrees/wt", "wt"), ("tmp/wt-dev", "wtdev")):
        subprocess.run([*GIT, "-C", str(root), "worktree", "add", "-q", "-b", branch, str(root / rel)], check=True)
    (root / "client" / "node_modules").mkdir()
    (root / "tmp" / "wt-dev" / "client" / "node_modules").symlink_to(root / "client" / "node_modules")
    outside().mkdir(exist_ok=True)
    (root / "link_out").symlink_to(outside())
    return root


def outside() -> Path:
    return GIT_REPOS_DIR / "scope_outside"


@pytest.fixture(autouse=True)
def _fresh_roots():
    common.allowed_roots.cache_clear()


def decide(command: str, cwd: Path, project: Path | None, monkeypatch) -> str:
    """Solo la capa determinista: con --laya gpu el modelo puede sumar un 'ask' legítimo."""
    import block_dangerous

    monkeypatch.setattr(block_dangerous, "should_use_laya", lambda: False)
    if project is None:
        monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    else:
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(project))
    result = run_in_process("block_dangerous", {"tool_name": "Bash", "tool_input": {"command": command}, "cwd": str(cwd)})
    if result.exit_code == 2:
        return "block"
    return result.decision or "pass"


# (comando, cwd relativo al repo, proyecto relativo al repo, decisión esperada); {out}, {repo}, {parent} se expanden
CASES = [
    # Variables que apuntan fuera del entorno git: bloqueadas
    pytest.param('p={out}; rm -rf "$p"', ".", ".", "block", id="variable_fuera_del_proyecto"),
    pytest.param("p={parent}; rm -rf $p/otro", ".", ".", "block", id="variable_carpeta_padre"),
    pytest.param("export P={out} && rm -rf $P/x", ".", ".", "block", id="export_fuera"),
    pytest.param('for d in {out} {repo}/tmp/a; do rm -rf "$d"; done', ".", ".", "block", id="for_con_un_valor_fuera"),
    pytest.param("for f in a; do rm -rf {out}/x; done", ".", ".", "block", id="rm_dentro_de_do"),
    pytest.param('rm -rf "${SYNAPSE_NO_DEFINIDA:-{out}}"', ".", ".", "block", id="valor_por_defecto_fuera"),
    pytest.param('p={out}; cd "$p" && rm -rf x', ".", ".", "block", id="cd_con_variable_fuera"),
    pytest.param("cd {out} && rm -rf x", ".", ".", "block", id="cd_fuera"),
    # Variables resueltas dentro del proyecto: el flujo normal (temporales auto-aprobados)
    pytest.param('p=tmp/cache; rm -rf "$p"', ".", ".", "allow", id="variable_en_tmp"),
    pytest.param('cd tmp && for p in * .[!.]*; do [ -e "$p" ] || continue; rm -rf -- "$p"; done', ".", ".", "allow", id="glob_en_tmp"),
    pytest.param('cd tmp && for p in .*; do rm -rf "$p"; done', ".", ".", "ask", id="glob_punto_puede_ser_el_padre"),
    pytest.param("cd .. && rm -rf tmp/x", "client", ".", "allow", id="subir_a_la_raiz_git"),
    # Variables no resolubles: no se bloquean (bash las necesita), solo pierden el auto-allow
    pytest.param('rm -rf "$(cat tmp/lista)"', ".", ".", "ask", id="sustitucion_pide_confirmacion"),
    pytest.param('rm -rf "$SYNAPSE_NO_DEFINIDA"', ".", ".", "ask", id="variable_desconocida_pide_confirmacion"),
    pytest.param('p=$(mktemp -d -p tmp); rm -rf "$p"', ".", ".", "ask", id="asignacion_desde_comando"),
    pytest.param('echo "$X" > "$DESTINO_DESCONOCIDO"', ".", ".", "pass", id="redireccion_a_variable_desconocida"),
    # Worktrees y entorno git (falsos positivos de la traza real)
    pytest.param("bun test > ../../../tmp/demo.log 2>&1", ".claude/worktrees/wt", ".claude/worktrees/wt", "pass", id="worktree_escribe_en_repo_principal"),
    pytest.param("cd {repo}/.claude/worktrees/wt && git grep -l x > tmp/f.txt", "{out}", ".claude/worktrees/wt", "pass", id="cwd_fuera_proyecto_worktree"),
    pytest.param("rm -f client/node_modules devtools/node_modules", "tmp/wt-dev", ".", "allow", id="symlink_en_worktree_anidado"),
    pytest.param("rm -f link_out", ".", ".", "ask", id="rm_de_symlink_borra_solo_el_enlace"),
    pytest.param("touch link_out/x", ".", ".", "block", id="escribir_a_traves_de_symlink"),
    pytest.param("rm -rf {out}/x", ".", None, "block", id="sin_claude_project_dir_usa_cwd"),
    # .git: cualquier comando que la involucre pide confirmación (salvo los bloqueos deterministas)
    pytest.param("cat .git/HEAD", ".", ".", "ask", id="git_dir_lectura"),
    pytest.param("ls .git", ".", ".", "ask", id="git_dir_listado"),
    pytest.param("rm -rf .git", ".", ".", "ask", id="git_dir_borrado"),
    pytest.param('d=.git; rm -rf "$d"', ".", ".", "ask", id="git_dir_por_variable"),
    pytest.param("cd client && cat ../.git/config", ".", ".", "ask", id="git_dir_relativo"),
    pytest.param("echo x > .git/HEAD", ".", ".", "block", id="git_dir_echo_sigue_bloqueado"),
    pytest.param("rg -n x --glob '!.git' .", ".", ".", "pass", id="exclusion_de_git_no_cuenta"),
    pytest.param("rsync -a --exclude .git client/ tmp/copia", ".", ".", "pass", id="rsync_exclude_git"),
    pytest.param("touch .gitignore .github/x.yml", ".", ".", "pass", id="gitignore_no_es_git_dir"),
]


def _expand(text: str, repo: Path) -> str:
    return text.replace("{out}", str(outside())).replace("{repo}", str(repo)).replace("{parent}", str(repo.parent))


def _path(rel: str, repo: Path) -> Path:
    expanded = _expand(rel, repo)
    return Path(expanded) if os.path.isabs(expanded) else repo / rel


@pytest.mark.parametrize(("command", "cwd", "project", "expected"), CASES)
def test_scope(repo, monkeypatch, command: str, cwd: str, project: str | None, expected: str) -> None:
    project_path = None if project is None else _path(project, repo)
    assert decide(_expand(command, repo), _path(cwd, repo), project_path, monkeypatch) == expected


@pytest.mark.parametrize(("value", "expected"), [("tmp/env-dir", "allow"), ("{out}", "block")])
def test_environment_variables_are_resolved(repo, monkeypatch, value: str, expected: str) -> None:
    target = _expand(value, repo)
    monkeypatch.setenv("SYNAPSE_TEST_DIR", target if os.path.isabs(target) else str(repo / target))
    assert decide('rm -rf "$SYNAPSE_TEST_DIR"', repo, repo, monkeypatch) == expected


def test_allowed_roots_cover_worktrees(repo, monkeypatch) -> None:
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(repo / ".claude/worktrees/wt"))
    roots = common.allowed_roots(str(outside()))
    assert str(repo) in roots and str(repo / ".claude/worktrees/wt") in roots and str(repo / "tmp/wt-dev") in roots


def test_allowed_roots_never_widen_to_home(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    monkeypatch.setattr(common, "_git_lines", lambda *_: [str(Path.home())])
    assert common.allowed_roots(str(tmp_path)) == (os.path.realpath(tmp_path),)


@pytest.mark.parametrize(
    ("word", "variables", "expected"),
    [
        pytest.param("$p/x", {"p": ["/a"]}, ["/a/x"], id="simple"),
        pytest.param("${p}x", {"p": ["/a"]}, ["/ax"], id="llaves"),
        pytest.param("$p", {"p": ["a", "b"]}, ["a", "b"], id="multiples_valores"),
        pytest.param("${q:-/d}", {}, ["/d"], id="por_defecto"),
        pytest.param("$p", {"p": None}, None, id="valor_desconocido"),
        pytest.param("$(pwd)/x", {}, None, id="sustitucion"),
        pytest.param("`pwd`", {}, None, id="backticks"),
        pytest.param("${p#a}", {"p": ["ab"]}, None, id="operador_no_soportado"),
        pytest.param("$1", {}, None, id="posicional"),
        pytest.param("plain", {}, ["plain"], id="sin_variables"),
    ],
)
def test_expand_word(word: str, variables: dict, expected) -> None:
    assert common.expand_word(word, variables, "/cwd") == expected
