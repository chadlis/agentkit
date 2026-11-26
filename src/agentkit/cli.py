"""The `agentkit` command."""

import argparse
import sys
from pathlib import Path

from agentkit.adapters.scripted import ScriptedAdapter
from agentkit.cases import WorkloadError
from agentkit.export import export

EXIT_INVALID_INPUT = 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agentkit", description="Run a free-text agent over a Workload's Cases and export its answers for Hypotrail.")
    commands = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")
    export = commands.add_parser("export", help="run an adapter over one set and write answers.jsonl and population.json for `hypotrail import agent`")
    export.add_argument("--workload-dir", required=True, type=Path, metavar="DIR", help="the Workload directory of a Hypotrail checkout (for example hypotrail/examples/chinook_prose)")
    export.add_argument("--set", required=True, choices=["dev"], dest="partition", help="the Partition to run; the held-out set runs only in the evaluator job")
    export.add_argument("--adapter", required=True, choices=["scripted"], help="scripted: replays the answers of an authored fixture, with no model")
    export.add_argument("--fixture", required=True, metavar="NAME", help="the fixture the scripted adapter replays: fixtures/answers-NAME.jsonl of the Workload")
    export.add_argument("--out", required=True, type=Path, metavar="OUT", help="the directory to write answers.jsonl and population.json in")
    export.set_defaults(handler=_export)
    return parser


def _export(args: argparse.Namespace) -> int:
    try:
        adapter = ScriptedAdapter(args.workload_dir / "fixtures" / f"answers-{args.fixture}.jsonl")
        counts = export(args.workload_dir, args.partition, adapter, args.out)
    except WorkloadError as error:
        print(f"invalid input: {error}", file=sys.stderr)
        return EXIT_INVALID_INPUT
    print(f"Exported {counts.total()} lines ({counts['produced']} produced, {counts['failed']} failed, {counts['missing']} missing) to {args.out}")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.handler(args)


if __name__ == "__main__":
    sys.exit(main())
