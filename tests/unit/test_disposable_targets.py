"""common.is_disposable_target: qué rutas se pueden borrar sin pedir confirmación."""

import pytest

import common
from support import PROJECT_DIR, make_git_repo


@pytest.fixture(scope="module")
def repo():
    return make_git_repo(
        "disposable_targets",
        tracked={"src/app.py": "x = 1\n", ".gitignore": "generated/\n"},
        untracked={
            "src/new.test.tsx": "it()\n",
            "untracked_dir/a.ts": "a\n",
            "generated/out.json": "{}\n",
        },
    )


@pytest.mark.parametrize(
    ("target", "expected"),
    [
        pytest.param("src/new.test.tsx", True, id="archivo_sin_seguimiento"),
        pytest.param("generated/out.json", True, id="archivo_gitignoreado"),
        pytest.param("dist/bundle.js", True, id="artefacto_de_build"),
        pytest.param("cache.pyc", True, id="extension_efimera"),
        pytest.param("src/app.py", False, id="archivo_rastreado"),
        pytest.param("untracked_dir", False, id="directorio_sin_seguimiento"),
        pytest.param("src/ghost.py", False, id="archivo_inexistente"),
    ],
)
def test_git_repo_targets(repo, target: str, expected: bool) -> None:
    assert common.is_disposable_target(target, str(repo)) is expected


@pytest.mark.parametrize(
    ("target", "expected"),
    [
        pytest.param("./tmp/cache.json", True, id="tmp_del_proyecto"),
        pytest.param("/tmp/claude-1000/session/out.log", True, id="scratchpad_de_sesion"),
        pytest.param("/tmp/os_file.txt", False, id="tmp_del_sistema"),
        # '*~' en .gitignore coincidía con la ruta literal '~/...' (sin expandir)
        pytest.param("~/.ssh/id_rsa", False, id="tilde_no_coincide_con_patron_backup"),
        pytest.param("~/.bashrc", False, id="home_no_es_desechable"),
    ],
)
def test_tmp_targets(target: str, expected: bool) -> None:
    assert common.is_disposable_target(target, str(PROJECT_DIR)) is expected
