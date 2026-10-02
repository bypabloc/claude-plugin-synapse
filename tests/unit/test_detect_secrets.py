"""Casos de detect_secrets.py definidos en tests/cases/detect_secrets.json."""

import pytest

from support import assert_case, load_cases, materialize_setup_file, run_in_process

CASES = load_cases("detect_secrets")


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_case(case: dict) -> None:
    setup = materialize_setup_file(case)
    try:
        assert_case(run_in_process("detect_secrets", case["payload"]), case)
    finally:
        if setup:
            setup.unlink(missing_ok=True)
