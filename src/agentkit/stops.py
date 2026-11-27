"""The stops (`docs/design.md`, section 6): the rules of `stops.json` of the working directory, the stop files `stops/STOP-<run>.json` a fired rule writes, and the stops still open.

A stop stays open until a `HUMAN` line of the decision log cites it in `source.stop`."""

import json
from pathlib import Path

from agentkit import decisions, ledger
from agentkit.cases import DEV

STOPS_FILE = Path("stops.json")
STOPS_DIR = decisions.STOPS
BUDGET_REACHED = "budget-reached@1"
FAILED_SHARE = "failed-share@1"
# The order rules are checked in, and the keys each takes.
RULE_KEYS = {BUDGET_REACHED: set(), FAILED_SHARE: {"above"}}


class StopsError(ValueError):
    """`stops.json` is missing or malformed: invalid input."""


class StopOpen(Exception):
    """A stop is open: the run is recorded `refused` and starts nothing."""

    def __init__(self, stops: list[str]):
        super().__init__(f"{', '.join(stops)} open: a stop is resumed only by a HUMAN decision that cites it")
        self.stops = stops


def load(path: Path = STOPS_FILE) -> dict[str, dict]:
    """The rules of `stops.json`, by name; a rule it does not list does not fire."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise StopsError(f"{path}: {error}") from error
    if not isinstance(data, dict) or set(data) != {"reason", "rules"} or not isinstance(data["reason"], str) or not isinstance(data["rules"], dict):
        raise StopsError(f"{path}: the stops file is an object of exactly reason and rules")
    for name, settings in data["rules"].items():
        if name not in RULE_KEYS or not isinstance(settings, dict) or set(settings) != RULE_KEYS[name]:
            raise StopsError(f"{path}: {name!r} is not one of {', '.join(RULE_KEYS)} with its keys")
    above = data["rules"].get(FAILED_SHARE, {}).get("above", 0)
    if not isinstance(above, (int, float)) or isinstance(above, bool) or not 0 <= above < 1:
        raise StopsError(f"{path}: the share above which {FAILED_SHARE} fires is a number from 0 to below 1")
    return data["rules"]


def fired(rules: dict[str, dict], partition: str, spent_before: int, dev_runs: int, statuses: list[str]) -> list[str]:
    """The rules a completed run fires. `spent_before` is the dev runs the budget counted before this one; `statuses` the observation status of each of its pairs."""
    if partition != DEV:
        return []
    found = []
    if BUDGET_REACHED in rules and spent_before + 1 >= dev_runs:
        found.append(BUDGET_REACHED)
    answered = sum(status in ("produced", "failed") for status in statuses)
    if FAILED_SHARE in rules and answered and statuses.count("failed") / answered > rules[FAILED_SHARE]["above"]:
        found.append(FAILED_SHARE)
    return found


def fire(run_id: str, rules: list[str]) -> str:
    """Writes the stop file of run `run_id`, named by the first rule it fired; returns the stop id."""
    stop_id = f"STOP-{run_id}"
    STOPS_DIR.mkdir(parents=True, exist_ok=True)
    stop = {"id": stop_id, "rule": rules[0], "rules": rules, "run": run_id, "time": ledger.now()}
    (STOPS_DIR / f"{stop_id}.json").write_text(json.dumps(stop, indent=2) + "\n", encoding="utf-8")
    return stop_id


def read_stops() -> list[dict]:
    """Every stop file, by stop id. Reads only."""
    try:
        return sorted((json.loads(path.read_text(encoding="utf-8")) for path in STOPS_DIR.glob("STOP-*.json")), key=lambda stop: stop["id"])
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise StopsError(f"{STOPS_DIR}: {error}") from error


def open_stops() -> list[dict]:
    """The stops no `HUMAN` decision cites."""
    resumed = {line["source"].get("stop") for line in decisions.lines() if line["mark"] == "HUMAN"}
    return [stop for stop in read_stops() if stop["id"] not in resumed]
