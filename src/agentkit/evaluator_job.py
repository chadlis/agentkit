"""The evaluator job's held-out reading (`docs/design.md`, sections 1 and 3): the one caller of `agentkit.evaluator.entry_point()`.

It reads one frozen Configuration, a scripted fixture, on the held-out Partition. The harness's pairs and answers, the export and Hypotrail's Study live in a temporary directory outside the working directory, which the reading deletes when it ends, on an error or SIGTERM too; SIGKILL leaves it. Hypotrail runs as a subprocess: `init`, `import agent`, `assess --scorer text_facts_v1` and `report`. The aggregate report and a separate metadata line leave the reading.

The committed reservation counts the reading before the Workload is retrieved. The local ledger reports the reading without its pairs. Once started, the reading is closed `failed` by any error, SIGINT or SIGTERM. The reading writes no decision.

What it prints never names a held-out Case: an error of Hypotrail is reported by its command and exit code only, a fixture error without its pair, and any other error after the count by its type only."""

import json
import os
import re
import shutil
import signal
import subprocess
import tempfile
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from agentkit import budget, evaluator, harness, ledger, readings, reservations
from agentkit.adapters.scripted import NAME as SCRIPTED, FixtureError, ScriptedAdapter
from agentkit.cases import WorkloadError
from agentkit.conductor import CONFIGURATION, HypotrailFailed, call_hypotrail
from agentkit.export import export

SCHEMA_VERSION = "kit-aggregate/1"
PUBLISHED = ("aggregate.json", "aggregate.md")
COMMIT = re.compile(r"[0-9a-f]{40}")
# The names of `aggregates` are fixed identifiers (`contracts/kit-aggregate-1/`).
AGGREGATE_NAME = re.compile(r"[a-z0-9_.]+")
JOB_VARIABLES = ("GITHUB_SHA", "GITHUB_REPOSITORY", "GITHUB_SERVER_URL", "GITHUB_RUN_ID", "GITHUB_ACTOR")
# The reasons of a reading closed `failed`: a Hypotrail command failed, SIGINT or SIGTERM stopped it, or another error did.
HYPOTRAIL, INTERRUPTED, ERROR = "hypotrail", "interrupted", "error"


class ReadingFailed(Exception):
    """An error other than Hypotrail's ended a counted reading; the message names its type only."""


@dataclass(frozen=True)
class Job:
    """What the report names of the job it was made in."""

    repository: str
    commit: str
    run: str
    actor: str


def job(environ=os.environ) -> Job:
    missing = [name for name in JOB_VARIABLES if not environ.get(name)]
    if missing:
        raise WorkloadError(f"the job sets no {', '.join(missing)}: the report names the job's commit, run and actor")
    if COMMIT.fullmatch(environ["GITHUB_SHA"]) is None:
        raise WorkloadError("GITHUB_SHA is not a 40-hex commit")
    run = f"{environ['GITHUB_SERVER_URL']}/{environ['GITHUB_REPOSITORY']}/actions/runs/{environ['GITHUB_RUN_ID']}"
    return Job(environ["GITHUB_REPOSITORY"], environ["GITHUB_SHA"], run, environ["GITHUB_ACTOR"])


def checkout_commit(workload_dir: Path) -> str:
    """The commit of the Hypotrail checkout that holds the Workload."""
    done = subprocess.run(["git", "-C", str(workload_dir), "rev-parse", "HEAD"], capture_output=True, text=True)
    commit = done.stdout.strip()
    if done.returncode != 0 or COMMIT.fullmatch(commit) is None:
        raise WorkloadError(f"{workload_dir} is not in a git checkout of Hypotrail: the report names its commit")
    return commit


def _number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def aggregates(assessment: dict, criteria: dict) -> list[dict]:
    """Each share and each count of the primary Criterion and of every other Criterion, under a fixed name, as Hypotrail printed them."""
    rows = []
    for criterion, scored in {assessment["primary_criterion"]: assessment, **criteria}.items():
        for metric in scored["metrics"]:
            rows.append({"name": f"criterion.{criterion}.{metric['id']}", "value": metric["value"], "interval": None, "n": metric["denominator"]})
        for count, value in scored["counts"].items():
            rows.append({"name": f"criterion.{criterion}.{count}", "value": value})
    for row in rows:
        if AGGREGATE_NAME.fullmatch(row["name"]) is None or not (row["value"] is None or _number(row["value"])) or type(row.get("n", 0)) is not int:
            raise HypotrailFailed("hypotrail assess gave an aggregate whose name or value the report cannot hold")
    return rows


def page(aggregate: dict) -> str:
    """The short page of the report, made from `aggregate.json` alone."""
    reading, counts, kit, hypotrail = aggregate["reading"], aggregate["counts"], aggregate["kit"], aggregate["hypotrail"]
    lines = [
        f"# Held-out reading {reading['number']} of {reading['allowed']}: {kit['candidate']}",
        "",
        f"- Partition `{aggregate['partition']}`: {counts['units']} pairs, {counts['produced']} produced, {counts['failed']} failed, {counts['missing']} missing.",
        f"- Kit: {kit['repository']} at `{kit['commit']}`. Hypotrail at `{hypotrail['commit']}`.",
        f"- Job run: {reading['job_run']}, started by {reading['actor']}.",
        f"- Hypotrail ids: Observation sets {', '.join(f'`{item}`' for item in hypotrail['observation_set_ids'])}; Assessments {', '.join(f'`{item}`' for item in hypotrail['assessment_ids'])}; "
        + (f"Comparison `{hypotrail['comparison_id']}`." if hypotrail["comparison_id"] else "no Comparison."),
        "- Decision: none recorded by this job." if aggregate["decision"] is None else f"- Decision: `{aggregate['decision']['decision_id']}`, {aggregate['decision']['channel']}, level {aggregate['decision']['level']}.",
        "",
        "| Aggregate | Value | n |",
        "|---|---|---|",
        *(f"| `{row['name']}` | {json.dumps(row['value'])} | {row.get('n', '')} |" for row in aggregate["aggregates"]),
        "",
    ]
    return "\n".join(lines)


def _terminated(number: int, frame) -> None:
    raise SystemExit(128 + number)


def authorise(candidate: str, hypotrail_commit: str, reservations_checkout: Path | None, where: Job, limits: budget.Budget) -> reservations.Permit:
    if CONFIGURATION.fullmatch(candidate) is None or COMMIT.fullmatch(hypotrail_commit) is None:
        raise WorkloadError("the reservation requires a named Candidate and a pinned Hypotrail commit")
    if reservations_checkout is None:
        raise readings.RecordError("the evaluator requires a current Agentkit main reservation checkout")
    return reservations.authorise(
        reservations_checkout, limits, candidate=candidate, run=where.run, attempt=os.environ.get("GITHUB_RUN_ATTEMPT", ""),
        job_commit=where.commit, hypotrail_commit=hypotrail_commit,
    )


def verify_reservation(candidate: str, hypotrail_commit: str, reservations_checkout: Path) -> None:
    """Refuse an unreserved dispatch before the workflow retrieves the Workload."""
    if not evaluator.in_evaluator_job():
        harness.refuse_held_out(evaluator.HELD_OUT, SCRIPTED, None)
    try:
        authorise(candidate, hypotrail_commit, reservations_checkout, job(), budget.load())
    except budget.BudgetRefused as refusal:
        ledger.refuse_run(ledger.LEDGER_DIR, partition=evaluator.HELD_OUT, adapter=SCRIPTED, configuration=None, reason=refusal.reason)
        raise


def read(workload_dir: Path, candidate: str, out: Path, reservations_checkout: Path | None = None) -> dict:
    """Reads `candidate` on the held-out Partition under the evaluator entry point, writes the published files in `out`, and returns `aggregate.json`.

    Outside a job of the evaluator workflow, the reading is recorded `refused` with no pair and raises `HeldOutRefused`, before the fixture is read. A reading the report could not name (an `out` that holds files, a job variable or a checkout commit missing, a candidate that is no fixture) is invalid input and records nothing. An open stop or a spent budget of held-out readings records it `refused` and raises, with no adapter call. Once counted, any error, SIGINT or SIGTERM closes it `failed`, spent, and retains its metadata: a failing Hypotrail command raises `HypotrailFailed`, any other error `ReadingFailed`."""
    if not evaluator.in_evaluator_job():
        harness.refuse_held_out(evaluator.HELD_OUT, SCRIPTED, None)
    with evaluator.entry_point():
        if CONFIGURATION.fullmatch(candidate) is None:
            raise WorkloadError(f"--candidate is a fixture name, not {candidate!r}")
        if out.exists() and (not out.is_dir() or any(out.iterdir())):
            raise WorkloadError(f"{out} holds files: the published directory is empty or new, so that only the aggregate report leaves the job")
        where = job()
        hypotrail_commit = checkout_commit(workload_dir)
        limits, _, records = harness.prepare_run(evaluator.HELD_OUT, SCRIPTED, None)
        spent = [item for item in records if item["set"] == evaluator.HELD_OUT and item["status"] != "refused"]
        try:
            permit = authorise(candidate, hypotrail_commit, reservations_checkout, where, limits)
            if any(item.get("candidate") == candidate or item.get("job_run") == where.run for item in spent):
                raise budget.BudgetRefused("held_out_readings", "this Candidate has had its held-out reading")
            reservations.verify_frozen_fixture(workload_dir, candidate, permit.first_hypotrail_commit, permit.earlier_candidates)
            adapter = ScriptedAdapter(workload_dir / "fixtures" / f"answers-{candidate}.jsonl")
            configuration = readings.digest(adapter.settings)
            if configuration != permit.configuration_digest:
                raise budget.BudgetRefused("reading_reservation", "the Candidate Configuration does not match the reservation")
        except FixtureError as error:
            raise WorkloadError(error.keyless) from None
        except budget.BudgetRefused as refusal:
            ledger.refuse_run(ledger.LEDGER_DIR, partition=evaluator.HELD_OUT, adapter=SCRIPTED, configuration=None, reason=refusal.reason)
            raise
        readings.write(out, candidate, configuration, where.commit, where.run, "failed")
        reading_id = ledger.open_run(
            ledger.LEDGER_DIR, partition=evaluator.HELD_OUT, adapter=SCRIPTED, configuration=None,
            pairs=[], candidate=candidate, job_run=where.run,
        )
        # SIGTERM raises, as SIGINT does, so that the reading is closed and the `finally` below deletes the temporary directory.
        previous = signal.signal(signal.SIGTERM, _terminated)
        work = None
        try:
            work = Path(tempfile.mkdtemp(prefix="agentkit-evaluator-"))
            counts, hypotrail = _inside(workload_dir, adapter, work, reading_id)
            aggregate = {
                "schema_version": SCHEMA_VERSION,
                "kit": {"repository": where.repository, "commit": where.commit, "candidate": candidate},
                "partition": evaluator.HELD_OUT,
                "reading": {"number": 1, "allowed": 1, "job_run": where.run, "actor": where.actor},
                "counts": {"units": counts.total(), "produced": counts["produced"], "failed": counts["failed"], "missing": counts["missing"]},
                "aggregates": hypotrail.pop("aggregates"),
                "hypotrail": {"commit": hypotrail_commit, **hypotrail, "comparison_id": None},
                "decision": None,
            }
            (out / "aggregate.json").write_text(json.dumps(aggregate, indent=2) + "\n", encoding="utf-8")
            (out / "aggregate.md").write_text(page(aggregate), encoding="utf-8")
            readings.write(out, candidate, configuration, where.commit, where.run, "completed")
        except BaseException as error:
            failure = HYPOTRAIL if isinstance(error, HypotrailFailed) else INTERRUPTED if isinstance(error, (KeyboardInterrupt, SystemExit)) else ERROR
            ledger.close_run(ledger.LEDGER_DIR, reading_id, "failed", failure)
            for name in PUBLISHED:
                (out / name).unlink(missing_ok=True)
            if failure == ERROR:
                # Its message, or a traceback, could name a held-out Case.
                raise ReadingFailed(f"the reading failed on a {type(error).__name__}") from None
            raise
        finally:
            if work is not None:
                shutil.rmtree(work, ignore_errors=True)
            signal.signal(signal.SIGTERM, previous)
        ledger.close_run(ledger.LEDGER_DIR, reading_id)
        return aggregate


def _inside(workload_dir: Path, adapter: ScriptedAdapter, work: Path, reading_id: str) -> tuple[Counter, dict]:
    """The per-Case part of the reading, all of it in `work`: the harness run, its export, and Hypotrail's Study. Returns the export's counts, and the Hypotrail ids with the aggregates."""
    job_ledger = work / "ledger"
    run_id = harness.run(workload_dir, evaluator.HELD_OUT, adapter, ledger_dir=job_ledger)
    counts = export(run_id, work / "export", ledger_dir=job_ledger)
    examples, study = str(workload_dir.parent), work / "study"
    made = call_hypotrail("init", "--workload", workload_dir.name, "--study", str(study), "--examples-dir", examples, quiet=True)
    imported = call_hypotrail(
        "import", "agent", "--study", str(study), "--source", str(work / "export" / "answers.jsonl"), "--population", str(work / "export" / "population.json"),
        "--request-id", f"reading-{reading_id}", "--examples-dir", examples, quiet=True,
    )
    assessment = call_hypotrail(
        "assess", "--study", str(study), "--observations", imported["observation_set_id"], "--scorer", "text_facts_v1",
        "--references", made["references_id"], "--examples-dir", examples, quiet=True,
    )["assessment"]
    call_hypotrail("report", "--study", str(study), "--out", str(work / "report.html"), "--examples-dir", examples, quiet=True)
    try:
        # The Assessment's per-Criterion aggregates; its per-Case file is never opened.
        criteria = json.loads((study / assessment["detailed_metrics_ref"]["path"]).read_text(encoding="utf-8"))["criteria"]
        rows = aggregates(assessment, criteria)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError) as error:
        raise HypotrailFailed(f"hypotrail assess gave no per-Criterion aggregates ({type(error).__name__})") from None
    return counts, {"observation_set_ids": [imported["observation_set_id"]], "assessment_ids": [assessment["assessment_id"]], "aggregates": rows}
