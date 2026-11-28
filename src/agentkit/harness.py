"""The harness: runs an adapter over the (Case, Repeat) pairs of one set, counted in the run ledger before the adapter's first call."""

from pathlib import Path

from agentkit import budget, evaluator, ledger, stops
from agentkit.adapters import RunStopped
from agentkit.cases import WorkloadError, read_cases


def refuse_held_out(partition: str, adapter: str, configuration: dict | None, ledger_dir: Path = ledger.LEDGER_DIR) -> None:
    """Outside the evaluator entry point, records a run of the held-out Partition `refused` and raises `HeldOutRefused`; does nothing otherwise."""
    if partition == evaluator.HELD_OUT and not evaluator.inside():
        ledger.refuse_run(ledger_dir, partition=partition, adapter=adapter, configuration=configuration)
        raise evaluator.refusal()


def _without_reason(fields: dict | None) -> bool:
    """True for a `failed` answer that does not say why in `metadata.reason`."""
    if fields is None or fields.get("observation_status") != "failed":
        return False
    metadata = fields.get("metadata")
    return not isinstance(metadata, dict) or not isinstance(metadata.get("reason"), str) or not metadata["reason"]


def prepare_run(partition: str, adapter: str, configuration: dict | None, ledger_dir: Path = ledger.LEDGER_DIR, budget_file: Path = budget.BUDGET_FILE) -> tuple[budget.Budget, dict, list[dict]]:
    """Read the budget, stop rules and ledger. Record a refusal if a stop is open."""
    limits = budget.load(budget_file)
    rules = stops.load()
    blocking = stops.open_stops()
    if blocking:
        ledger.refuse_run(ledger_dir, partition=partition, adapter=adapter, configuration=configuration, reason="stop_open")
        raise stops.StopOpen([stop["id"] for stop in blocking])
    return limits, rules, ledger.read_records(ledger_dir)


def admit(partition: str, adapter, ledger_dir: Path = ledger.LEDGER_DIR, budget_file: Path = budget.BUDGET_FILE) -> tuple[budget.Budget, dict, list[dict]]:
    """Checks, in this order, that no stop is open and that the run of `adapter` on `partition` exceeds no limit of the budget; returns the budget, the stop rules and the ledger's records.

    A refused run is recorded `refused` in `ledger_dir`, with `stop_open` or the limit as its reason, and raises `StopOpen` or `BudgetRefused`."""
    limits, rules, records = prepare_run(partition, adapter.name, adapter.settings, ledger_dir, budget_file)
    try:
        budget.check_run(limits, records, partition, adapter.settings, getattr(adapter, "is_live", False))
    except budget.BudgetRefused as refusal:
        ledger.refuse_run(ledger_dir, partition=partition, adapter=adapter.name, configuration=adapter.settings, reason=refusal.reason)
        raise
    return limits, rules, records


def run(workload_dir: Path, partition: str, adapter, ledger_dir: Path = ledger.LEDGER_DIR, budget_file: Path = budget.BUDGET_FILE, repeats: int = 1) -> str:
    """Runs `adapter` over every (Case, Repeat) of `partition` and returns the run id. An adapter that raises leaves its record `running`, for the next run to mark `failed`.

    The held-out Partition outside the evaluator entry point is recorded `refused`, with no pair and no adapter call, and raises `HeldOutRefused`.
    A run that would exceed a limit of the budget is recorded `refused` with the limit as its reason, with no adapter call, and raises `BudgetRefused`.
    While a stop is open, the run is recorded `refused` with the reason `stop_open`, with no adapter call, and raises `StopOpen`.
    An adapter that raises `RunStopped` (the live ceiling, a provider error) stops the run, which is closed `failed` with its reason, and fires no stop rule.
    A `failed` answer without `metadata.reason` closes the run `failed` without a reason, which is never exported, and raises `WorkloadError`.
    A completed run that fires a rule of `stops.json` writes its stop file, and its record names the stop.
    Every Case is run `repeats` times, repeat 0 to `repeats` - 1; fewer than 1 is invalid input, refused before anything is recorded."""
    if type(repeats) is not int or repeats < 1:
        raise WorkloadError(f"repeats must be an integer of at least 1, not {repeats!r}")
    refuse_held_out(partition, adapter.name, adapter.settings, ledger_dir)
    limits, rules, records = admit(partition, adapter, ledger_dir, budget_file)
    pairs = [(case, repeat) for case in read_cases(workload_dir, partition) for repeat in range(repeats)]
    run_id = ledger.open_run(
        ledger_dir,
        partition=partition,
        adapter=adapter.name,
        configuration=adapter.settings,
        pairs=[(case.case_id, repeat) for case, repeat in pairs],
    )
    statuses = []
    try:
        for case, repeat in pairs:
            answer = adapter.answer(case, repeat)
            fields = None if answer is None else answer.fields
            if _without_reason(fields):
                ledger.close_run(ledger_dir, run_id, "failed")
                raise WorkloadError(f"the adapter answered {case.case_id}, repeat {repeat}, failed without a metadata.reason")
            ledger.record_answer(ledger_dir, run_id, case.case_id, repeat, fields)
            statuses.append("missing" if fields is None else fields.get("observation_status"))
    except RunStopped as stop:
        if stop.fields is not None:
            ledger.record_answer(ledger_dir, run_id, case.case_id, repeat, stop.fields)
        ledger.close_run(ledger_dir, run_id, "failed", stop.reason)
        return run_id
    spent_before = sum(record["set"] == partition and record["status"] != "refused" for record in records)
    rules_fired = stops.fired(rules, partition, spent_before, limits.dev_runs, statuses)
    ledger.close_run(ledger_dir, run_id, stop=stops.fire(run_id, rules_fired) if rules_fired else None)
    return run_id
