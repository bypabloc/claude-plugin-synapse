"""common.is_git_recoverable: borrados que `git checkout` restaura tal cual (rastreados y sin cambios locales)."""

import subprocess

import pytest

import common
from support import make_git_repo


@pytest.fixture(scope="module")
def repo():
    path = make_git_repo(
        "git_recoverable",
        tracked={
            "src/app.py": "x = 1\n",
            "src/edited.py": "y = 1\n",
            "src/skipped.py": "s = 1\n",
            "src/assumed.py": "a = 1\n",
            "clean_dir/a.ts": "a\n",
            "clean_dir/sub/b.ts": "b\n",
            "dirty_dir/a.ts": "a\n",
            "cache_dir/a.py": "a\n",
            ".gitignore": "*.env\n__pycache__/\n",
        },
        untracked={},
    )
    git = ["git", "-C", str(path), "-c", "user.name=t", "-c", "user.email=t@example.com"]
    subprocess.run([*git, "commit", "-qm", "init"], check=True)
    (path / "src/edited.py").write_text("y = 2\n")
    (path / "dirty_dir/new.ts").write_text("nuevo\n")
    (path / "cache_dir/__pycache__").mkdir()
    (path / "cache_dir/__pycache__/a.pyc").write_text("pyc\n")
    (path / "clean_dir/local.env").write_text("SECRET=x\n")
    (path / "src/staged.py").write_text("z = 1\n")
    subprocess.run([*git, "add", "src/staged.py"], check=True)
    for flag, rel in (("--skip-worktree", "src/skipped.py"), ("--assume-unchanged", "src/assumed.py")):
        subprocess.run([*git, "update-index", flag, rel], check=True)
        (path / rel).write_text("local\n")
    nested = path / "nested_repo"
    nested.mkdir()
    (nested / "n.py").write_text("n\n")
    subprocess.run(["git", "init", "-q", str(nested)], check=True)
    subprocess.run([*git[:2], str(nested), *git[3:], "add", "n.py"], check=True)
    subprocess.run([*git[:2], str(nested), *git[3:], "commit", "-qm", "init"], check=True)
    return path


@pytest.mark.parametrize(
    ("target", "expected"),
    [
        pytest.param("src/app.py", True, id="archivo_commiteado_sin_cambios"),
        pytest.param("cache_dir", True, id="directorio_limpio_con_cache_regenerable"),
        pytest.param("src/edited.py", False, id="archivo_con_cambios_locales"),
        pytest.param("src/staged.py", False, id="archivo_solo_en_index"),
        pytest.param("dirty_dir", False, id="directorio_con_archivo_sin_seguimiento"),
        pytest.param("clean_dir", False, id="directorio_con_archivo_ignorado_no_regenerable"),
        pytest.param("src/ghost.py", False, id="archivo_inexistente"),
        pytest.param("src/skipped.py", False, id="skip_worktree_oculta_cambios_locales"),
        pytest.param("src/assumed.py", False, id="assume_unchanged_oculta_cambios_locales"),
        pytest.param("nested_repo", False, id="repo_anidado_borraria_su_historial"),
    ],
)
def test_git_recoverable_targets(repo, target: str, expected: bool) -> None:
    assert common.is_git_recoverable(target, str(repo)) is expected


def test_repo_root_is_not_recoverable() -> None:
    path = make_git_repo("git_recoverable_root", tracked={"a.py": "a\n"}, untracked={})
    subprocess.run(["git", "-C", str(path), "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-qm", "init"], check=True)
    assert common.is_git_recoverable(".", str(path)) is False
