"""Casos de protect_files.py definidos en tests/cases/protect_files.json."""

import pytest

from support import assert_case, load_cases, materialize_setup_file, run_in_process

CASES = load_cases("protect_files")


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_case(case: dict) -> None:
    setup = materialize_setup_file(case)
    try:
        assert_case(run_in_process("protect_files", case["payload"]), case)
    finally:
        if setup:
            setup.unlink(missing_ok=True)
