"""Temporal worker entry point."""
from __future__ import annotations

import asyncio
import logging

from temporalio.client import Client
from temporalio.worker import UnsandboxedWorkflowRunner, Worker

from src.config import settings
from src.db.models import init_db
from src.orchestrator.activities import (
    check_compliance_gates,
    determine_channel,
    execute_brain_call,
    execute_outbound_touch,
    extract_signals,
    get_backoff,
    handle_voice_outcome,
    load_lead,
    log_event,
    plan_next_touch,
    process_inbound,
    save_lead,
    schedule_callback,
    send_retainer,
    send_via_channel,
    write_to_clio,
)
from src.orchestrator.workflow import OutboundChaseWorkflow


async def run_worker() -> None:
    logging.basicConfig(level=logging.INFO)
    await init_db()

    client = await Client.connect(
        f"{settings.temporal_host}:{settings.temporal_port}",
        namespace=settings.temporal_namespace,
    )

    worker = Worker(
        client,
        task_queue="outbound-chase",
        workflows=[OutboundChaseWorkflow],
        workflow_runner=UnsandboxedWorkflowRunner(),
        activities=[
            load_lead,
            save_lead,
            check_compliance_gates,
            determine_channel,
            plan_next_touch,
            extract_signals,
            execute_brain_call,
            execute_outbound_touch,
            handle_voice_outcome,
            get_backoff,
            send_via_channel,
            log_event,
            write_to_clio,
            schedule_callback,
            send_retainer,
            process_inbound,
        ],
    )

    logging.info("Starting Temporal worker on task queue outbound-chase")
    await worker.run()


def main() -> None:
    asyncio.run(run_worker())


if __name__ == "__main__":
    main()
