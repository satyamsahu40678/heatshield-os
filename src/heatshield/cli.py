"""Planning tools, schema migrations, provisioning, and durable app processes."""

import argparse
import getpass
import json
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description="HeatShield OS synthetic prevention workspace")
    commands = parser.add_subparsers(dest="command", required=True)
    demo = commands.add_parser("demo", help="optimize a synthetic scenario without HTTP")
    demo.add_argument("--seed", type=int, default=7)
    fixture = commands.add_parser("fixture", help="print a synthetic scenario as JSON")
    fixture.add_argument("--seed", type=int, default=7)
    benchmark = commands.add_parser("benchmark", help="compare fixed, greedy, and CP-SAT plans")
    benchmark.add_argument("--count", type=int, default=3)
    benchmark.add_argument("--seed", type=int, default=7)
    benchmark.add_argument("--suite", choices=("nominal", "robustness"), default="nominal")
    benchmark.add_argument("--output", type=Path)
    commands.add_parser("schema", help="print the OpenAPI contract")
    commands.add_parser("migrate", help="apply the tracked Alembic migrations")
    commands.add_parser("seed-demo", help="create demo-only synthetic login accounts")
    provision = commands.add_parser(
        "create-user", help="provision an identity with a private prompt"
    )
    provision.add_argument("--email", required=True)
    provision.add_argument("--name", required=True)
    provision.add_argument(
        "--role", choices=("admin", "safety_officer", "supervisor", "worker"), required=True
    )
    provision.add_argument("--organization", required=True)
    provision.add_argument("--worker-id")
    serve = commands.add_parser("serve", help="serve the API and built React client")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    serve.add_argument("--reload", action="store_true")
    worker = commands.add_parser("worker", help="process the durable database queue")
    worker.add_argument("--once", action="store_true")
    worker.add_argument("--worker-id")
    worker.add_argument("--poll-seconds", type=float, default=1.0)
    args = parser.parse_args()
    if args.command == "serve":
        import uvicorn

        uvicorn.run("heatshield.api:app", host=args.host, port=args.port, reload=args.reload)
        return
    if args.command == "worker":
        from .worker import main as worker_main

        argv = ["--poll-seconds", str(args.poll_seconds)]
        if args.once:
            argv.append("--once")
        if args.worker_id:
            argv += ["--worker-id", args.worker_id]
        worker_main(argv)
        return
    if args.command == "migrate":
        from alembic import command
        from alembic.config import Config

        path = Path("alembic.ini")
        if not path.is_file():
            parser.error("Run migrations from the project checkout containing alembic.ini")
        command.upgrade(Config(str(path)), "head")
        return
    if args.command in {"seed-demo", "create-user"}:
        from sqlalchemy import select

        from .auth import hash_password
        from .bootstrap import seed_demo
        from .database import Database
        from .identity import Organization, User
        from .settings import Settings

        settings = Settings.from_env()
        database = Database(settings.database_url)
        try:
            if settings.demo_mode:
                database.initialize()
            with database.session() as session:
                if args.command == "seed-demo":
                    users = seed_demo(session, demo_mode=settings.demo_mode)
                    print(f"Seeded {len(users)} synthetic demo accounts.")
                    return
                if "@" not in args.email or not args.name.strip():
                    parser.error("Provide an email address and a display name")
                if args.role == "worker" and not args.worker_id:
                    parser.error("Worker accounts require --worker-id")
                if session.scalar(select(User).where(User.email == args.email.strip().lower())):
                    parser.error("An identity with this email already exists")
                if not sys.stdin.isatty():
                    parser.error("Use an interactive terminal for the private password prompt")
                password = getpass.getpass("New password (at least 12 characters): ")
                if len(password) < 12 or password != getpass.getpass("Confirm password: "):
                    parser.error("Passwords must match and contain at least 12 characters")
                if session.get(Organization, args.organization) is None:
                    session.add(Organization(id=args.organization, name=args.organization))
                    session.flush()
                session.add(
                    User(
                        organization_id=args.organization,
                        email=args.email.strip().lower(),
                        display_name=args.name.strip(),
                        role=args.role,
                        worker_id=args.worker_id,
                        password_hash=hash_password(password),
                    )
                )
                print("Created the tenant-bound identity.")
        finally:
            database.dispose()
        return
    if args.command == "schema":
        from .api import app

        result = app.openapi()
    elif args.command == "benchmark":
        from .benchmark import run_benchmark

        try:
            result = run_benchmark(args.count, args.seed, suite=args.suite)
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
