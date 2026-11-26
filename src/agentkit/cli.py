"""The `agentkit` command. The run ledger is `ledger/` of the working directory."""

import argparse
import sys
from pathlib import Path

from agentkit import harness, ledger
from agentkit.adapters.scripted import ScriptedAdapter
from agentkit.cases import WorkloadError
from agentkit.evaluator import HeldOutRefused
from agentkit.export import export

EXIT_INVALID_INPUT = 2
EXIT_HELD_OUT_REFUSED = 9
RUN_OPTIONS = ("workload_dir", "partition", "adapter", "fixture")


def add_run_options(parser: argparse.ArgumentParser, required: bool) -> None:
    parser.add_argument("--workload-dir", required=required, type=Path, metavar="DIR", help="the Workload directory of a Hypotrail checkout (for example hypotrail/examples/chinook_prose)")
    parser.add_argument("--set", required=required, choices=["dev", "public_check"], dest="partition", help="the Partition to run; the held-out set (public_check) is refused with exit 9 outside the evaluator job")
    parser.add_argument("--adapter", required=required, choices=["scripted"], help="scripted: replays the answers of an authored fixture, with no model")
    parser.add_argument("--fixture", required=required, metavar="NAME", help="the fixture the scripted adapter replays: fixtures/answers-NAME.jsonl of the Workload")


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
    status = commands.add_parser("status", help="print, read-only, the ledger's runs per set and per status")
    status.set_defaults(handler=_status)
    return parser


def _start(args: argparse.Namespace) -> str:
    # Before the adapter is built: a held-out run is refused even when its fixture cannot be read, and records no configuration.
    harness.refuse_held_out(args.partition, args.adapter, None)
    adapter = ScriptedAdapter(args.workload_dir / "fixtures" / f"answers-{args.fixture}.jsonl")
    return harness.run(args.workload_dir, args.partition, adapter)


def _run(args: argparse.Namespace) -> int:
    try:
        run_id = _start(args)
    except HeldOutRefused as error:
        print(f"refused: {error}", file=sys.stderr)
        return EXIT_HELD_OUT_REFUSED
    except (WorkloadError, ledger.LedgerError) as error:
        print(f"invalid input: {error}", file=sys.stderr)
        return EXIT_INVALID_INPUT
    print(f"Run {run_id} completed (in {ledger.runs_file(ledger.LEDGER_DIR)})")
    return 0


def _export(args: argparse.Namespace) -> int:
    given = [name for name in RUN_OPTIONS if getattr(args, name) is not None]
    if args.run is not None and given:
        print("invalid input: --run exports an existing run and takes none of --workload-dir, --set, --adapter, --fixture", file=sys.stderr)
        return EXIT_INVALID_INPUT
    if args.run is None and len(given) != len(RUN_OPTIONS):
        print("invalid input: give --run, or all of --workload-dir, --set, --adapter, --fixture", file=sys.stderr)
        return EXIT_INVALID_INPUT
    try:
        run_id = args.run if args.run is not None else _start(args)
        counts = export(run_id, args.out)
    except HeldOutRefused as error:
        print(f"refused: {error}", file=sys.stderr)
        return EXIT_HELD_OUT_REFUSED
    except (WorkloadError, ledger.LedgerError) as error:
        print(f"invalid input: {error}", file=sys.stderr)
        return EXIT_INVALID_INPUT
    print(f"Exported run {run_id}: {counts.total()} lines ({counts['produced']} produced, {counts['failed']} failed, {counts['missing']} missing) to {args.out}")
    return 0


def _status(args: argparse.Namespace) -> int:
    try:
        table = ledger.counts(ledger.read_records())
    except ledger.LedgerError as error:
        print(f"invalid input: {error}", file=sys.stderr)
        return EXIT_INVALID_INPUT
    if not table:
        print(f"No runs in {ledger.runs_file(ledger.LEDGER_DIR)}")
    for partition, row in table.items():
        total = sum(row.values())
        detail = ", ".join(f"{count} {status}" for status, count in row.items())
        print(f"{partition}: {total} {'run' if total == 1 else 'runs'} ({detail})")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.handler(args)


if __name__ == "__main__":
    sys.exit(main())
