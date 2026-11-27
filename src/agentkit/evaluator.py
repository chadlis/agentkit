"""The entry point of the evaluator job: the only caller under which the held-out Partition is read (`docs/design.md`, section 3).

It checks the environment GitHub Actions sets in a job of the evaluator workflow. A local process can set the same variables, so this is a guard in code, not a wall."""

import os
import re
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

from agentkit import decisions

HELD_OUT = "public_check"
WORKFLOW = ".github/workflows/evaluator.yml"
# `GITHUB_WORKFLOW_REF` is `<owner>/<repo>/<workflow path>@<ref>`.
WORKFLOW_REF = re.compile(r"[^/]+/[^/]+/" + re.escape(WORKFLOW) + r"@.+")

_inside = ContextVar("inside_evaluator_entry_point", default=False)


class HeldOutRefused(Exception):
    """The held-out Partition was asked for outside the evaluator entry point."""


def in_evaluator_job(environ=os.environ) -> bool:
    return environ.get("GITHUB_ACTIONS") == "true" and WORKFLOW_REF.fullmatch(environ.get("GITHUB_WORKFLOW_REF", "")) is not None


def inside() -> bool:
    """True only within `entry_point()`."""
    return _inside.get()


@contextmanager
def entry_point() -> Iterator[None]:
    """Within it, `agentkit.cases` and the harness read the held-out Partition; refused unless the process is a job of the evaluator workflow. It marks the process as the evaluator's for the decision log."""
    if not in_evaluator_job():
        raise HeldOutRefused(f"the {HELD_OUT} Partition is read only in a GitHub Actions job of {WORKFLOW}")
    # The mark outlives the block: the evaluator's process, and every child it starts, never writes HUMAN.
    decisions.enter_process("evaluator")
    token = _inside.set(True)
    try:
        yield
    finally:
        _inside.reset(token)


def refusal() -> HeldOutRefused:
    return HeldOutRefused(f"the {HELD_OUT} Partition is read only under the evaluator entry point (agentkit.evaluator), in a job of {WORKFLOW}")
