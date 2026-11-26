"""The harness: runs an adapter over the (Case, Repeat) pairs of one set, counted in the run ledger before the adapter's first call."""

from pathlib import Path

from agentkit import budget, evaluator, ledger
from agentkit.adapters import RunStopped
from agentkit.cases import read_cases

REPEATS = (0,)


def refuse_held_out(partition: str, adapter: str, configuration: dict | None, ledger_dir: Path = ledger.LEDGER_DIR) -> None:
    """Outside the evaluator entry point, records a run of the held-out Partition `refused` and raises `HeldOutRefused`; does nothing otherwise."""
    if partition == evaluator.HELD_OUT and not evaluator.inside():
        ledger.refuse_run(ledger_dir, partition=partition, adapter=adapter, configuration=configuration)
        raise evaluator.refusal()


def run(workload_dir: Path, partition: str, adapter, ledger_dir: Path = ledger.LEDGER_DIR, budget_file: Path = budget.BUDGET_FILE) -> str:
    """Runs `adapter` over every (Case, Repeat) of `partition` and returns the run id. An adapter that raises leaves its record `running`, for the next run to mark `failed`.

    The held-out Partition outside the evaluator entry point is recorded `refused`, with no pair and no adapter call, and raises `HeldOutRefused`.
    A run that would exceed a limit of the budget is recorded `refused` with the limit as its reason, with no adapter call, and raises `BudgetRefused`.
    An adapter that raises `RunStopped` (the live ceiling, a provider error) stops the run, which is closed `failed` with its reason."""
    refuse_held_out(partition, adapter.name, adapter.settings, ledger_dir)
    limits = budget.load(budget_file)
    try:
        budget.check_run(limits, ledger.read_records(ledger_dir), partition, adapter.settings, getattr(adapter, "is_live", False))
    except budget.BudgetRefused as refusal:
        ledger.refuse_run(ledger_dir, partition=partition, adapter=adapter.name, configuration=adapter.settings, reason=refusal.reason)
        raise
    pairs = [(case, repeat) for case in read_cases(workload_dir, partition) for repeat in REPEATS]
    run_id = ledger.open_run(
        ledger_dir,
        partition=partition,
        adapter=adapter.name,
        configuration=adapter.settings,
        pairs=[(case.case_id, repeat) for case, repeat in pairs],
    )
    try:
        for case, repeat in pairs:
            answer = adapter.answer(case, repeat)
            ledger.record_answer(ledger_dir, run_id, case.case_id, repeat, None if answer is None else answer.fields)
    except RunStopped as stop:
        if stop.fields is not None:
            ledger.record_answer(ledger_dir, run_id, case.case_id, repeat, stop.fields)
        ledger.close_run(ledger_dir, run_id, "failed", stop.reason)
        return run_id
    ledger.close_run(ledger_dir, run_id)
    return run_id
