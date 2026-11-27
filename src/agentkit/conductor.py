"""The conductor: the automatic mode on the dev set (`docs/design.md`, sections 1 and 7).

It runs each Configuration, a scripted fixture, through the harness, under the budget and the stops. It exports each run and has Hypotrail import and assess it, then compare it with the Control: the first Configuration, then the one each Comparison keeps. Hypotrail runs as a subprocess. Of what Hypotrail prints, the conductor reads the ids it passes on and `comparison.delta`, never a Study file nor the References. The Study lives in a temporary directory, deleted when the loop ends, and only the dev deltas are kept under `ledger/conductor/`. Each Comparison gives one `PROXY` line under `keep-higher-dev-score@1`. It never runs `hypotrail decide`, never writes `HUMAN`, never resumes a stop and never edits the budget.

While its loop runs, its marker file says so (`agentkit.markers`). When the loop ends, on an error, SIGINT or SIGTERM too, it writes a note for the person, `ledger/conductor/<conduct_id>-note.md`: the runs spent, the `PROXY` decisions, and the stop or limit that parked it."""

import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from agentkit import budget, decisions, harness, ledger, markers, stops
from agentkit.adapters.scripted import NAME as SCRIPTED, ScriptedAdapter
from agentkit.cases import WorkloadError
from agentkit.evaluator import HeldOutRefused
from agentkit.export import export
from agentkit.rules import KEEP_HIGHER_DEV_SCORE, keep_higher_dev_score

CONDUCTOR_DIR = markers.CONDUCTOR_DIR
CONFIGURATION = re.compile(r"[A-Za-z0-9_-]+")
CONDUCT_ID = re.compile(r"c-([0-9]{4,})")
INVALID = (WorkloadError, ledger.LedgerError, budget.BudgetError, stops.StopsError, decisions.DecisionError)
# How a loop ends; the command maps each to its exit.
DONE, STOP, STOP_OPEN, BUDGET, HELD_OUT, RUN_STOPPED, HYPOTRAIL, INVALID_INPUT = (
    "done", "stop", "stop_open", "budget", "held_out", "run_stopped", "hypotrail", "invalid_input"
)


class HypotrailFailed(Exception):
    """A Hypotrail command exited with an error, or printed no JSON object."""


@dataclass
class Journal:
    """What a loop did, for its note."""

    conduct_id: str
    configurations: list[str]
    runs: list[tuple[str, str]] = field(default_factory=list)
    decided: list[str] = field(default_factory=list)
    outcome: str | None = None
    detail: str = ""


def hypotrail_command() -> list[str]:
    return [sys.executable, "-m", "hypotrail"]


def check_configurations(text: str) -> list[str]:
    """The Configurations of `--configurations A,B`, in order: fixture names, each once."""
    names = text.split(",")
    if any(CONFIGURATION.fullmatch(name) is None for name in names) or len(set(names)) != len(names):
        raise WorkloadError(f"--configurations is a list of distinct fixture names separated by commas, not {text!r}")
    return names


def next_id() -> str:
    numbers = [int(found[1]) for item in CONDUCTOR_DIR.glob("c-*") if (found := CONDUCT_ID.match(item.name))] if CONDUCTOR_DIR.is_dir() else []
    return f"c-{max(numbers, default=0) + 1:04d}"


def _hypotrail(*args: str) -> dict:
    environment = {key: value for key, value in os.environ.items() if key != "HYPOTRAIL_ANTHROPIC_API_KEY"}
    done = subprocess.run([*hypotrail_command(), *args, "--json"], capture_output=True, text=True, env=environment)
    try:
        printed = json.loads(done.stdout) if done.returncode == 0 else None
    except json.JSONDecodeError:
        printed = None
    if not isinstance(printed, dict):
        raise HypotrailFailed(f"hypotrail {' '.join(args[:2])} exited {done.returncode}: {done.stderr.strip()[-500:]}")
    return printed


class Study:
    """The conductor's dev Study in Hypotrail, made on first use in a temporary directory outside the working directory, with the exports Hypotrail imports.

    `hypotrail init` copies the whole References file into the Study, the held-out References included: `close` deletes the directory, and only the dev Comparisons are kept, in `ledger/conductor/<conduct_id>-comparisons.jsonl`."""

    def __init__(self, workload_dir: Path, conduct_id: str):
        self.workload_dir, self.conduct_id = workload_dir, conduct_id
        self.examples = str(workload_dir.parent)
        self.work: Path | None = None
        self.references_id: str | None = None

    @property
    def path(self) -> Path:
        return self.work / "study"

    def close(self) -> None:
        if self.work is not None:
            shutil.rmtree(self.work, ignore_errors=True)

    def assess(self, configuration: str, run_id: str) -> str:
        """Exports the run, has Hypotrail import and assess it, and returns the Assessment id."""
        if self.references_id is None:
            self.work = Path(tempfile.mkdtemp(prefix=f"agentkit-{self.conduct_id}-"))
            made = _hypotrail("init", "--workload", self.workload_dir.name, "--study", str(self.path), "--examples-dir", self.examples)
            self.references_id = made["references_id"]
        out = self.work / f"export-{configuration}"
        export(run_id, out)
        imported = _hypotrail(
            "import", "agent", "--study", str(self.path), "--source", str(out / "answers.jsonl"), "--population", str(out / "population.json"),
            "--request-id", f"{self.conduct_id}-{run_id}", "--examples-dir", self.examples,
        )
        assessed = _hypotrail(
            "assess", "--study", str(self.path), "--observations", imported["observation_set_id"], "--scorer", "text_facts_v1",
            "--references", self.references_id, "--examples-dir", self.examples,
        )
        return assessed["assessment"]["assessment_id"]

    def delta(self, control: str, candidate: str) -> dict:
        """The `comparison.delta` of the Control and the Candidate, which holds their dev `text_facts_success` shares."""
        delta = _hypotrail("compare", "--study", str(self.path), "--control", control, "--candidate", candidate, "--examples-dir", self.examples)["comparison"]["delta"]
        shares = (delta.get("control"), delta.get("candidate"))
        if not all(isinstance(share, (int, float)) and not isinstance(share, bool) for share in shares):
            raise HypotrailFailed(f"hypotrail compare gave no two shares to compare ({delta.get('reason')})")
        return delta


def _loop(journal: Journal, workload_dir: Path, partition: str) -> tuple[str, str]:
    study = Study(workload_dir, journal.conduct_id)
    try:
        return _configurations(journal, workload_dir, partition, study)
    finally:
        study.close()


def _configurations(journal: Journal, workload_dir: Path, partition: str, study: Study) -> tuple[str, str]:
    kept: tuple[str, str, str] | None = None
    for configuration in journal.configurations:
        try:
            # Before the fixture is read, as `agentkit run` does.
            harness.refuse_held_out(partition, SCRIPTED, None)
            adapter = ScriptedAdapter(workload_dir / "fixtures" / f"answers-{configuration}.jsonl")
            run_id = harness.run(workload_dir, partition, adapter)
        except HeldOutRefused as error:
            return HELD_OUT, str(error)
        except stops.StopOpen as error:
            return STOP_OPEN, str(error)
        except budget.BudgetRefused as error:
            return BUDGET, f"the budget limit {error.reason}: {error}"
        except INVALID as error:
            return INVALID_INPUT, str(error)
        journal.runs.append((configuration, run_id))
        record = next(item for item in ledger.read_records() if item["run_id"] == run_id)
        if record["status"] != "completed":
            return RUN_STOPPED, f"run {run_id} stopped: {record.get('reason')}"
        if record.get("stop") is not None:
            stop = next(item for item in stops.read_stops() if item["id"] == record["stop"])
            return STOP, f"{stop['id']} ({stop['rule']}) fired on run {run_id}; it is resumed only by a HUMAN decision that cites it"
        assessment = study.assess(configuration, run_id)
        if kept is None:
            kept = (configuration, run_id, assessment)
            continue
        control_name, control_run = kept[0], kept[1]
        delta = study.delta(kept[2], assessment)
        control, candidate = delta["control"], delta["candidate"]
        CONDUCTOR_DIR.mkdir(parents=True, exist_ok=True)
        with (CONDUCTOR_DIR / f"{journal.conduct_id}-comparisons.jsonl").open("a", encoding="utf-8", newline="\n") as kept_file:
            kept_file.write(json.dumps({"control": control_name, "control_run": control_run, "candidate": configuration, "candidate_run": run_id, "delta": delta}) + "\n")
        option = keep_higher_dev_score(control=control, candidate=candidate)
        if option == "keep_candidate":
            kept = (configuration, run_id, assessment)
        line = decisions.append(
            question=f"keep_after_comparison: Control {control_name} vs Candidate {configuration} (run {run_id}), dev text_facts_success {control} vs {candidate}",
            option=f"{option}: {kept[0]}",
            mark="PROXY",
            rule=KEEP_HIGHER_DEV_SCORE,
            source={"run": run_id},
            by=decisions.process(),
        )
        journal.decided.append(line["id"])
    return DONE, f"every Configuration was tried; {kept[0] if kept else 'none'} is kept"


def note(journal: Journal) -> str:
    runs = ", ".join(f"{run_id} ({configuration})" for configuration, run_id in journal.runs) or "none"
    ending = "Done" if journal.outcome == DONE else "Parked" if journal.outcome is not None else "Ended on an error"
    return "\n".join([
        f"# Conductor {journal.conduct_id}",
        "",
        f"- Configurations: {', '.join(journal.configurations)} (Control: {journal.configurations[0]})",
        f"- Runs spent: {runs}",
        f"- PROXY decisions ({KEEP_HIGHER_DEV_SCORE}): {', '.join(journal.decided) or 'none'}",
        f"- {ending}: {journal.detail}",
        "",
    ])


def _terminated(number: int, frame) -> None:
    raise SystemExit(128 + number)


def conduct(workload_dir: Path, partition: str, configurations: list[str]) -> Journal:
    """Runs the loop under a marker file and returns its journal; the note is written and the marker removed when it ends, on an error, SIGINT or SIGTERM too."""
    journal = Journal(next_id(), configurations)
    # The mark outlives the loop: this process, and every child it starts, never writes HUMAN.
    decisions.enter_process("conductor")
    markers.anchor()
    # SIGTERM raises, as SIGINT does, so that the `finally` below deletes the temporary Study and the marker. SIGKILL leaves both.
    previous = signal.signal(signal.SIGTERM, _terminated)
    markers.write(journal.conduct_id)
    try:
        journal.outcome, journal.detail = _loop(journal, workload_dir, partition)
    except HypotrailFailed as error:
        journal.outcome, journal.detail = HYPOTRAIL, str(error)
    except BaseException as error:
        journal.detail = repr(error)
        raise
    finally:
        markers.remove(journal.conduct_id)
        CONDUCTOR_DIR.mkdir(parents=True, exist_ok=True)
        (CONDUCTOR_DIR / f"{journal.conduct_id}-note.md").write_text(note(journal), encoding="utf-8")
        signal.signal(signal.SIGTERM, previous)
    return journal
