"""The conductor's marker files, `ledger/conductor/<conduct_id>.json` of the working directory.

The conductor writes its marker, its pid and start time, when its loop starts, and removes it when the loop ends, on an error too. While a marker names a live process, `agentkit decide --mark human` refuses, whatever `AGENTKIT_PROCESS` says. A marker whose process is gone is stale: it is reported and ignored. Like the process mark, this is a guard against an accidental `HUMAN` write, not a wall and not authentication: a process can delete the file, and a pid can be reused by another process."""

import json
import os
from pathlib import Path

from agentkit import ledger

CONDUCTOR_DIR = ledger.LEDGER_DIR / "conductor"


class MarkerError(ValueError):
    """A marker file cannot be read: `HUMAN` is refused rather than the file ignored."""


def path(conduct_id: str) -> Path:
    return CONDUCTOR_DIR / f"{conduct_id}.json"


def write(conduct_id: str) -> Path:
    marker = path(conduct_id)
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(json.dumps({"pid": os.getpid(), "start_time": ledger.now()}) + "\n", encoding="utf-8")
    return marker


def remove(conduct_id: str) -> None:
    path(conduct_id).unlink(missing_ok=True)


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _pid(marker: Path) -> int:
    try:
        pid = json.loads(marker.read_text(encoding="utf-8"))["pid"]
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError) as error:
        raise MarkerError(f"{marker}: {error!r}") from error
    if type(pid) is not int or pid <= 0:
        raise MarkerError(f"{marker}: the pid is not a positive integer")
    return pid


def read() -> tuple[list[Path], list[Path]]:
    """The markers whose process is alive, and the stale ones. Reads only."""
    found = sorted(CONDUCTOR_DIR.glob("*.json"))
    live = [marker for marker in found if _alive(_pid(marker))]
    return live, [marker for marker in found if marker not in live]
