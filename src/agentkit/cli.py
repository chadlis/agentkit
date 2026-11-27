"""The `agentkit` command. The run ledger is `ledger/` of the working directory."""

import argparse
import os
import sys
from pathlib import Path

from agentkit import budget, conductor, decisions, harness, ledger, markers, stops
from agentkit.adapters import live
from agentkit.adapters.scripted import ScriptedAdapter
from agentkit.cases import WorkloadError
from agentkit.evaluator import HeldOutRefused
from agentkit.export import export

EXIT_RUN_FAILED = 1
EXIT_INVALID_INPUT = 2
EXIT_STOP = 3
EXIT_OPEN_STOP = 5
EXIT_BUDGET = 8
EXIT_HELD_OUT_REFUSED = 9
RUN_OPTIONS = ("workload_dir", "partition", "adapter")
ADAPTER_OPTIONS = ("fixture", "model", "repeats")
INVALID = (WorkloadError, ledger.LedgerError, budget.BudgetError, stops.StopsError, decisions.DecisionError, markers.MarkerError)


def add_run_options(parser: argparse.ArgumentParser, required: bool) -> None:
    parser.add_argument("--workload-dir", required=required, type=Path, metavar="DIR", help="the Workload directory of a Hypotrail checkout (for example hypotrail/examples/chinook_prose)")
    parser.add_argument("--set", required=required, choices=["dev", "public_check"], dest="partition", help="the Partition to run; the held-out set (public_check) is refused with exit 9 outside the evaluator job")
    parser.add_argument("--adapter", required=required, choices=["scripted", "live"], help="scripted: replays the answers of an authored fixture, with no model; live: calls a model, opt-in and bounded by budget.json")
    parser.add_argument("--fixture", metavar="NAME", help="scripted only: the fixture it replays, fixtures/answers-NAME.jsonl of the Workload")
    parser.add_argument("--model", metavar="ID", help="live only: the Anthropic model id; there is no default")
    parser.add_argument("--live", action="store_true", help="live only: allow paid model calls, under the ceiling of budget.json")
    parser.add_argument("--repeats", type=int, metavar="N", help="run every Case N times, repeat 0 to N-1 (default 1)")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agentkit", description="Run a free-text agent over a Workload's Cases and export its answers for Hypotrail.")
    commands = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")
    run = commands.add_parser("run", help="run an adapter over one set, counted in the ledger before its first call")
    add_run_options(run, required=True)
    run.set_defaults(handler=_run)
    export = commands.add_parser(
        "export",
        help="write answers.jsonl and population.json for `hypotrail import agent` from a closed ledger run: the one given by --run, or a new run of the options",
    )
    export.add_argument("--run", metavar="RUN_ID", help="the completed ledger run to export, instead of a new run")
    add_run_options(export, required=False)
    export.add_argument("--out", required=True, type=Path, metavar="OUT", help="the directory to write answers.jsonl and population.json in")
    export.set_defaults(handler=_export)
    status = commands.add_parser("status", help="print, read-only, the ledger's runs per set and per status, the open stops and the decisions with their marks")
    status.set_defaults(handler=_status)
    decide = commands.add_parser("decide", help="append one decision to decisions/log.jsonl, marked PROXY with its rule or HUMAN with a person's words file")
    decide.add_argument("--question", required=True, metavar="TEXT", help="what is decided: which Configuration to keep trying, whether to resume after a stop")
    decide.add_argument("--option", required=True, metavar="TEXT", help="the option kept")
    decide.add_argument("--mark", required=True, choices=["proxy", "human"])
    decide.add_argument("--rule", metavar="NAME@VERSION", help="PROXY only, required: the rule applied")
    decide.add_argument("--words-file", type=Path, metavar="FILE", help="HUMAN only, required: a file the person wrote, read whole")
    decide.add_argument("--run", metavar="RUN_ID", help="cite a run of the ledger")
    decide.add_argument("--stop", metavar="STOP_ID", help="cite a stop of stops/")
    decide.add_argument("--pull-request", metavar="URL@COMMIT", help="cite a pull request: https://github.com/<owner>/<repo>/pull/<n>@<40-hex commit>, its merge commit or reviewed head")
    decide.set_defaults(handler=_decide)
    cite = commands.add_parser("cite", help="print the kit-citation/1 line of a decision that exists at the commit checked out")
    cite.add_argument("id", metavar="K-NNNN")
    cite.set_defaults(handler=_cite)
    conduct = commands.add_parser("conduct", help="the automatic mode: run each Configuration on the dev set under the budget and the stops, compare each with the Control in Hypotrail, write PROXY decisions, and park on a stop or a limit")
    conduct.add_argument("--workload-dir", required=True, type=Path, metavar="DIR", help="the Workload directory of a Hypotrail checkout (for example hypotrail/examples/chinook_prose)")
    conduct.add_argument("--set", required=True, choices=["dev", "public_check"], dest="partition", help="dev; the held-out set (public_check) is refused with exit 9")
    conduct.add_argument("--configurations", required=True, metavar="A,B", help="the scripted fixtures to try, in order; the first is the Control")
    conduct.add_argument("--adapter", choices=["scripted", "live"], default="scripted", help="scripted only: the conductor makes no live call, and live is refused with exit 2")
    conduct.set_defaults(handler=_conduct)
    return parser


def _start(args: argparse.Namespace) -> str:
    # Before the adapter is built: a held-out run is refused even when its fixture cannot be read, and records no configuration.
    harness.refuse_held_out(args.partition, args.adapter, None)
    if args.adapter == live.NAME:
        if args.fixture is not None:
            raise WorkloadError("--fixture is for the scripted adapter")
        limits = budget.load()
        adapter = live.build(live=args.live, model=args.model, environ=os.environ, ceiling=limits.live, price_of=limits.price)
    else:
        if args.fixture is None or args.model is not None or args.live:
            raise WorkloadError("the scripted adapter needs --fixture, and takes neither --model nor --live")
        adapter = ScriptedAdapter(args.workload_dir / "fixtures" / f"answers-{args.fixture}.jsonl")
    return harness.run(args.workload_dir, args.partition, adapter, repeats=1 if args.repeats is None else args.repeats)


def _record(run_id: str) -> dict:
    return next(item for item in ledger.read_records() if item["run_id"] == run_id)


def _stopped(run_id: str) -> str | None:
    """The reason the harness stopped run `run_id` before its last pair, or None."""
    record = _record(run_id)
    return record.get("reason") if record["status"] == "failed" else None


def _fired(run_id: str) -> str | None:
    """The stop that the completed run `run_id` fired, or None; says so on stderr."""
    stop = _record(run_id).get("stop")
    if stop is not None:
        print(f"Run {run_id} fired a stop rule: {stops.STOPS_DIR / stop}.json. Every run exits {EXIT_OPEN_STOP} until a HUMAN decision cites {stop}", file=sys.stderr)
    return stop


def _run(args: argparse.Namespace) -> int:
    try:
        run_id = _start(args)
    except HeldOutRefused as error:
        print(f"refused: {error}", file=sys.stderr)
        return EXIT_HELD_OUT_REFUSED
    except stops.StopOpen as error:
        print(f"refused: {error}", file=sys.stderr)
        return EXIT_OPEN_STOP
    except budget.BudgetRefused as error:
        print(f"refused by the budget ({error.reason}): {error}", file=sys.stderr)
        return EXIT_BUDGET
    except INVALID as error:
        print(f"invalid input: {error}", file=sys.stderr)
        return EXIT_INVALID_INPUT
    reason = _stopped(run_id)
    if reason is not None:
        print(f"Run {run_id} stopped ({reason}) and is recorded failed (in {ledger.runs_file(ledger.LEDGER_DIR)})", file=sys.stderr)
        return EXIT_BUDGET if reason in budget.CEILING_REASONS else EXIT_RUN_FAILED
    print(f"Run {run_id} completed (in {ledger.runs_file(ledger.LEDGER_DIR)})")
    return EXIT_STOP if _fired(run_id) is not None else 0


def _export(args: argparse.Namespace) -> int:
    given = [name for name in RUN_OPTIONS if getattr(args, name) is not None]
    if args.run is not None and (given or args.live or any(getattr(args, name) is not None for name in ADAPTER_OPTIONS)):
        print("invalid input: --run exports an existing run and takes none of --workload-dir, --set, --adapter, --fixture, --model, --live, --repeats", file=sys.stderr)
        return EXIT_INVALID_INPUT
    if args.run is None and len(given) != len(RUN_OPTIONS):
        print("invalid input: give --run, or all of --workload-dir, --set, --adapter and the adapter's options", file=sys.stderr)
        return EXIT_INVALID_INPUT
    try:
        run_id = args.run if args.run is not None else _start(args)
        counts = export(run_id, args.out)
    except HeldOutRefused as error:
        print(f"refused: {error}", file=sys.stderr)
        return EXIT_HELD_OUT_REFUSED
    except stops.StopOpen as error:
        print(f"refused: {error}", file=sys.stderr)
        return EXIT_OPEN_STOP
    except budget.BudgetRefused as error:
        print(f"refused by the budget ({error.reason}): {error}", file=sys.stderr)
        return EXIT_BUDGET
    except INVALID as error:
        print(f"invalid input: {error}", file=sys.stderr)
        return EXIT_INVALID_INPUT
    print(f"Exported run {run_id}: {counts.total()} lines ({counts['produced']} produced, {counts['failed']} failed, {counts['missing']} missing) to {args.out}")
    return EXIT_STOP if args.run is None and _fired(run_id) is not None else 0


def _status(args: argparse.Namespace) -> int:
    try:
        table = ledger.counts(ledger.read_records())
        blocking = stops.open_stops()
        lines = decisions.lines()
    except INVALID as error:
        print(f"invalid input: {error}", file=sys.stderr)
        return EXIT_INVALID_INPUT
    if not table:
        print(f"No runs in {ledger.runs_file(ledger.LEDGER_DIR)}")
    for partition, row in table.items():
        total = sum(row.values())
        detail = ", ".join(f"{count} {status}" for status, count in row.items())
        print(f"{partition}: {total} {'run' if total == 1 else 'runs'} ({detail})")
    print("Open stops: " + (", ".join(f"{stop['id']} ({stop['rule']}, run {stop['run']})" for stop in blocking) or "none"))
    if not lines:
        print(f"No decisions in {decisions.LOG}")
    for line in lines:
        rule = f", rule {line['rule']}" if line["mark"] == "PROXY" else ""
        print(f"{line['id']} {line['mark']} (level {decisions.level(line)}{rule}): {line['question']} -> {line['option']}")
    return 0


def _decide(args: argparse.Namespace) -> int:
    source = {"run": args.run, "stop": args.stop, "pull_request": args.pull_request}
    try:
        if args.mark == "human":
            for marker in markers.read()[1]:
                print(f"stale conductor marker, ignored: {marker} names a process that is gone", file=sys.stderr)
        line = decisions.append(
            question=args.question, option=args.option, mark=args.mark.upper(), source=source, by=decisions.process(), rule=args.rule, words_file=args.words_file
        )
    except INVALID as error:
        print(f"refused ({getattr(error, 'code', 'INVALID_INPUT')}): {error}", file=sys.stderr)
        return EXIT_INVALID_INPUT
    print(f"{line['id']} {line['mark']} by {line['by']}: {line['option']} (in {decisions.LOG})")
    return 0


def _cite(args: argparse.Namespace) -> int:
    try:
        citation = decisions.cite(args.id)
    except decisions.DecisionError as error:
        print(f"refused ({error.code}): {error}", file=sys.stderr)
        return EXIT_INVALID_INPUT
    print(citation)
    return 0


CONDUCT_EXITS = {
    conductor.DONE: 0,
    conductor.STOP: EXIT_STOP,
    conductor.STOP_OPEN: EXIT_OPEN_STOP,
    conductor.BUDGET: EXIT_BUDGET,
    conductor.HELD_OUT: EXIT_HELD_OUT_REFUSED,
    conductor.RUN_STOPPED: EXIT_RUN_FAILED,
    conductor.HYPOTRAIL: EXIT_RUN_FAILED,
    conductor.INVALID_INPUT: EXIT_INVALID_INPUT,
}


def _conduct(args: argparse.Namespace) -> int:
    if args.adapter != "scripted":
        print("invalid input: the conductor runs scripted fixtures only and makes no live call", file=sys.stderr)
        return EXIT_INVALID_INPUT
    try:
        configurations = conductor.check_configurations(args.configurations)
    except WorkloadError as error:
        print(f"invalid input: {error}", file=sys.stderr)
        return EXIT_INVALID_INPUT
    journal = conductor.conduct(args.workload_dir, args.partition, configurations)
    print(f"Conductor {journal.conduct_id}: {journal.outcome}, {journal.detail} (note in {conductor.CONDUCTOR_DIR / journal.conduct_id}-note.md)", file=sys.stdout if journal.outcome == conductor.DONE else sys.stderr)
    return CONDUCT_EXITS[journal.outcome]


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.handler(args)


if __name__ == "__main__":
    sys.exit(main())
