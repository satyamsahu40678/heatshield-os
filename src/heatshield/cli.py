"""Small CLI for running and inspecting the synthetic planning foundation."""

import argparse
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description="HeatShield OS synthetic planning prototype")
    commands = parser.add_subparsers(dest="command", required=True)
    demo = commands.add_parser("demo", help="optimize the synthetic demonstration scenario")
    demo.add_argument("--seed", type=int, default=7)
    fixture = commands.add_parser("fixture", help="print a synthetic scenario as JSON")
    fixture.add_argument("--seed", type=int, default=7)
    benchmark = commands.add_parser("benchmark", help="compare fixed, greedy, and CP-SAT plans")
    benchmark.add_argument("--count", type=int, default=3)
    benchmark.add_argument("--seed", type=int, default=7)
    benchmark.add_argument("--output", type=Path)
    commands.add_parser("schema", help="print the OpenAPI contract")
    args = parser.parse_args()
    if args.command == "schema":
        from .api import app

        result = app.openapi()
    elif args.command == "benchmark":
        from .benchmark import run_benchmark

        try:
            result = run_benchmark(args.count, args.seed)
        except ValueError as error:
            parser.error(str(error))
    else:
        from .fixtures import demo_scenario

        if not 0 <= args.seed <= 2_147_483_647:
            parser.error("seed must be between 0 and 2147483647")
        scenario = demo_scenario(args.seed)
        if args.command == "fixture":
            result = scenario.model_dump(mode="json")
        else:
            from .optimization import optimize

            result = optimize(scenario).model_dump(mode="json")
    serialized = json.dumps(result, indent=2, allow_nan=False) + "\n"
    if getattr(args, "output", None):
        args.output.write_text(serialized)
        print(f"Wrote {args.output}")
    else:
        print(serialized, end="")


if __name__ == "__main__":
    main()
