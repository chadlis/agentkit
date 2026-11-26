"""The harness: runs an adapter over the (Case, Repeat) pairs of one set, counted in the run ledger before the adapter's first call."""

from pathlib import Path

from agentkit import ledger
from agentkit.cases import read_cases

REPEATS = (0,)


def run(workload_dir: Path, partition: str, adapter, ledger_dir: Path = ledger.LEDGER_DIR) -> str:
    """Runs `adapter` over every (Case, Repeat) of `partition` and returns the run id. An adapter that raises leaves its record `running`, for the next run to mark `failed`."""
    pairs = [(case, repeat) for case in read_cases(workload_dir, partition) for repeat in REPEATS]
    run_id = ledger.open_run(
        ledger_dir,
        partition=partition,
        adapter=adapter.name,
        configuration=adapter.settings,
        pairs=[(case.case_id, repeat) for case, repeat in pairs],
    )
    for case, repeat in pairs:
        answer = adapter.answer(case, repeat)
        ledger.record_answer(ledger_dir, run_id, case.case_id, repeat, None if answer is None else answer.fields)
    ledger.close_run(ledger_dir, run_id)
    return run_id
