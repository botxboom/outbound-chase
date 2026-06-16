"""Main entry point for Outbound-Chase Agent."""
from __future__ import annotations

import argparse
import logging

import uvicorn

from src.config import settings


def main() -> None:
    parser = argparse.ArgumentParser(description="Outbound-Chase Agent")
    parser.add_argument(
        "--mode",
        choices=["server", "worker"],
        default="server",
        help="Run webhook server or Temporal worker",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if settings.debug else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    if args.mode == "worker":
        from src.worker import main as worker_main

        worker_main()
        return

    uvicorn.run(
        "src.api.webhooks:app",
        host="0.0.0.0",
        port=8000,
        reload=settings.debug,
        log_level="debug" if settings.debug else "info",
    )


if __name__ == "__main__":
    main()
