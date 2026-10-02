"""Casos de block_env_read.py definidos en tests/cases/block_env_read.json."""

import pytest

from support import assert_case, load_cases, materialize_setup_file, run_in_process

CASES = load_cases("block_env_read")


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_case(case: dict) -> None:
    setup = materialize_setup_file(case)
    try:
        assert_case(run_in_process("block_env_read", case["payload"]), case)
    finally:
        if setup:
            setup.unlink(missing_ok=True)
