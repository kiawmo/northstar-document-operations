import argparse
import json
import logging
import os
from pathlib import Path
import signal
from threading import Event

from .config import Settings
from .db import Database
from .extraction import create_provider
from .service import DocumentService
from .worker import Worker


def main() -> None:
    parser = argparse.ArgumentParser(description="Northstar durable document operations")
    parser.add_argument(
        "command",
        choices=[
            "serve",
            "worker",
            "demo",
            "chaos",
            "fixtures",
            "crash-once",
            "gateway-smoke",
            "inbox-poll",
            "ack-resolve",
        ],
    )
    parser.add_argument("--database", default=None)
    parser.add_argument("--outbox-id")
    parser.add_argument("--outcome", choices=["confirmed", "not-sent"])
    parser.add_argument("--evidence")
    parser.add_argument("--port", type=int, default=8103)
    parser.add_argument("--no-worker", action="store_true")
    parser.add_argument("--llm", choices=["fixture", "gateway"], default=os.getenv("DOCUMENT_LLM_PROVIDER", "gateway"))
    args = parser.parse_args()
    if args.command == "chaos" and args.llm != "fixture":
        parser.error("Deterministic fault injection requires --llm fixture; live Codex extraction runs via demo")
    settings = Settings(provider=args.llm)
    if args.database:
        settings.database_url = args.database
    if args.no_worker:
        settings.worker_enabled = False
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    database = Database(settings.database_url, settings.database_schema)
    database.initialize()
    service = DocumentService(database, settings)
    if args.command == "inbox-poll":
        from .mail import poll_imap

        print(json.dumps(poll_imap(service), indent=2))
    elif args.command == "ack-resolve":
        service.resolve_ack(args.outbox_id, args.outcome, args.evidence or "")
        print(json.dumps({"reconciled": args.outbox_id, "outcome": args.outcome}))
    elif args.command == "serve":
        import uvicorn
        from .api import create_app

        uvicorn.run(
            create_app(settings),
            host="127.0.0.1" if os.getenv("DOCUMENT_BIND_ALL") != "1" else "0.0.0.0",
            port=args.port,
        )
    elif args.command == "worker":
        stop = Event()
        signal.signal(signal.SIGTERM, lambda *_: stop.set())
        signal.signal(signal.SIGINT, lambda *_: stop.set())
        Worker(service, create_provider(settings)).run(stop)
    elif args.command == "crash-once":
        claim = service.claim()
        os._exit(23 if claim else 24)
    elif args.command == "fixtures":
        from .fixtures import build_fixtures

        print(json.dumps({k: str(v) for k, v in build_fixtures(Path("fixtures")).items()}, indent=2))
    elif args.command == "gateway-smoke":
        from .live_smoke import gateway_smoke

        print(json.dumps(gateway_smoke(service), indent=2))
    else:
        from .demo import chaos, happy

        settings.retry_base = 0.1
        result = happy(service, Path("evidence")) if args.command == "demo" else chaos(service, Path("evidence"))
        print(
            json.dumps(
                {
                    "command": result["command"],
                    "database": result["database"],
                    "checks": result["checks"],
                    "states": result["snapshot"]["states"],
                },
                indent=2,
            )
        )


if __name__ == "__main__":
    main()
