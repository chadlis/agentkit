"""The budget, `budget.json` of the working directory (`docs/design.md`, section 5): dev runs per cycle, held-out readings per Configuration, and the ceiling of one live invocation with the prices it is counted at."""

import json
import math
from dataclasses import dataclass
from pathlib import Path

from agentkit import evaluator
from agentkit.adapters import RunStopped
from agentkit.cases import DEV

BUDGET_FILE = Path("budget.json")
KEYS = ("reason", "dev_runs", "held_out_readings", "live", "prices")
TOTAL_READINGS = "held_out_total_readings"
CEILING_KEYS = ("calls", "tokens", "usd")
PRICE_KEYS = ("input_usd_per_mtok", "output_usd_per_mtok", "as_of", "source")
CEILING_REASONS = ("call_ceiling", "token_ceiling", "cost_ceiling")


class BudgetError(ValueError):
    """`budget.json` is missing or malformed, or holds no price for a live model: invalid input."""


class BudgetRefused(Exception):
    """The run would exceed a limit of the budget: it is recorded `refused` and starts nothing."""

    def __init__(self, reason: str, message: str):
        super().__init__(message)
        self.reason = reason


class CeilingReached(RunStopped):
    """A live invocation reached its ceiling before a call; the harness closes the run `failed` with `reason`."""

    def __init__(self, reason: str):
        super().__init__(reason, f"the live ceiling is reached: {reason}")


@dataclass(frozen=True)
class Ceiling:
    calls: int
    tokens: int
    usd: float


@dataclass(frozen=True)
class Price:
    input_usd_per_mtok: float
    output_usd_per_mtok: float


@dataclass(frozen=True)
class Budget:
    dev_runs: int
    held_out_readings: int
    live: Ceiling
    prices: dict
    held_out_total_readings: int | None = None

    def protected_total(self) -> int:
        """A protected read requires a committed total, even when dev uses a legacy budget."""
        if self.held_out_total_readings is None:
            raise BudgetError(f"{BUDGET_FILE}: protected readings require {TOTAL_READINGS}")
        return self.held_out_total_readings

    def price(self, model: str) -> Price:
        """The price of `model`; a model with no price refuses the run."""
        if model not in self.prices:
            raise BudgetError(f"{BUDGET_FILE} holds no price for the model {model!r}")
        entry = self.prices[model]
        return Price(entry["input_usd_per_mtok"], entry["output_usd_per_mtok"])


def _count(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _positive(value) -> bool:
    return _amount(value) and value > 0


def _amount(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0


def load(path: Path = BUDGET_FILE) -> Budget:
    try:
        content = path.read_bytes()
    except OSError as error:
        raise BudgetError(f"{path}: {error}") from error
    return parse(content, path)


def parse(content: bytes, path: Path | str) -> Budget:
    """Validate budget bytes read from a working directory or a committed Git object."""
    try:
        data = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise BudgetError(f"{path}: {error}") from error
    if not isinstance(data, dict) or data.keys() - {TOTAL_READINGS} != set(KEYS) or not isinstance(data["reason"], str):
        raise BudgetError(f"{path}: the budget is an object of {', '.join(KEYS)} with optional {TOTAL_READINGS}")
    if not all(_count(data[key]) for key in ("dev_runs", "held_out_readings")) or (TOTAL_READINGS in data and not _count(data[TOTAL_READINGS])):
        raise BudgetError(f"{path}: dev_runs and the held-out limits are non-negative integers")
    live = data["live"]
    if not isinstance(live, dict) or set(live) != set(CEILING_KEYS) or not _count(live["calls"]) or not _count(live["tokens"]) or not _amount(live["usd"]):
        raise BudgetError(f"{path}: live is {{calls, tokens, usd}}, non-negative")
    prices = data["prices"]
    for model, entry in prices.items() if isinstance(prices, dict) else [(None, None)]:
        if (
            not isinstance(entry, dict) or set(entry) != set(PRICE_KEYS)
            or not _positive(entry["input_usd_per_mtok"]) or not _positive(entry["output_usd_per_mtok"])
            or not isinstance(entry["as_of"], str) or not isinstance(entry["source"], str)
        ):
            raise BudgetError(f"{path}: the price of {model!r} is {{{', '.join(PRICE_KEYS)}}}, with prices above 0 so that the spend ceiling binds")
    return Budget(data["dev_runs"], data["held_out_readings"], Ceiling(live["calls"], live["tokens"], live["usd"]), prices, data.get(TOTAL_READINGS))


def check_run(budget: Budget, records: list[dict], partition: str, configuration: dict | None, is_live: bool) -> None:
    """Raises `BudgetRefused` when the run would exceed a limit; a `refused` record spends nothing."""
    spent = [record for record in records if record["set"] == partition and record["status"] != "refused"]
    if partition == DEV and len(spent) >= budget.dev_runs:
        raise BudgetRefused("dev_runs", f"the {budget.dev_runs} dev runs of the cycle are spent")
    if partition == evaluator.HELD_OUT:
        total_readings = budget.protected_total()
        readings = [record for record in spent if record["configuration"] == configuration]
        if len(readings) >= budget.held_out_readings:
            raise BudgetRefused("held_out_readings", f"this Configuration has had its {budget.held_out_readings} held-out readings")
        if len(spent) >= total_readings:
            raise BudgetRefused(TOTAL_READINGS, f"the {total_readings} held-out readings are spent")
    if is_live and 0 in (budget.live.calls, budget.live.tokens, budget.live.usd):
        raise BudgetRefused("live", "the live ceiling of the budget is 0")
