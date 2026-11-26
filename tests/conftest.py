"""Fixtures that locate the public twin in a Hypotrail checkout. The kit copies none of its files (`docs/design.md`)."""

import os
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def hypotrail_checkout() -> Path:
    """`HYPOTRAIL_CHECKOUT`, or `./hypotrail` where `e2e.yml` checks out the pinned commit."""
    return Path(os.environ.get("HYPOTRAIL_CHECKOUT", ROOT / "hypotrail")).resolve()


@pytest.fixture(scope="session")
def workload_dir() -> Path:
    """The `chinook_prose` Workload of the checkout. Without one the test is skipped, unless `AGENTKIT_REQUIRE_TWIN=1` (set by `e2e.yml`) makes it fail."""
    directory = hypotrail_checkout() / "examples" / "chinook_prose"
    if not (directory / "workload.json").is_file():
        message = f"no chinook_prose Workload under {directory}: set HYPOTRAIL_CHECKOUT to a Hypotrail checkout"
        if os.environ.get("AGENTKIT_REQUIRE_TWIN") == "1":
            pytest.fail(message)
        pytest.skip(message)
    return directory
