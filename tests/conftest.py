"""Fixtures that locate the public twin in a Hypotrail checkout. The kit copies none of its files (`docs/design.md`)."""

import os
import shutil
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
# Resolved once, when pytest loads this file, against the directory pytest starts in: every test then runs in its own `tmp_path`.
CHECKOUT = Path(os.environ.get("HYPOTRAIL_CHECKOUT", ROOT / "hypotrail")).resolve()


def hypotrail_checkout() -> Path:
    """`HYPOTRAIL_CHECKOUT`, or `./hypotrail` where `e2e.yml` checks out the pinned commit."""
    return CHECKOUT


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


@pytest.fixture(autouse=True)
def ledger_in_tmp(tmp_path, monkeypatch) -> None:
    """The kit writes its ledger, its decision log and its stops under the working directory and reads `budget.json`, `stops.json` and `readings.jsonl` there: every test runs in its own, never in the repository, with a copy of the versioned files."""
    monkeypatch.chdir(tmp_path)
    shutil.copyfile(ROOT / "budget.json", tmp_path / "budget.json")
    shutil.copyfile(ROOT / "stops.json", tmp_path / "stops.json")
    shutil.copyfile(ROOT / "readings.jsonl", tmp_path / "readings.jsonl")


@pytest.fixture(autouse=True)
def unmarked_process(monkeypatch) -> None:
    """No test starts as the conductor's or the evaluator's process, and a mark a test's code sets is removed after it."""
    monkeypatch.setenv("AGENTKIT_PROCESS", "")
    monkeypatch.delenv("AGENTKIT_PROCESS")
