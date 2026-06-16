"""Temporal workflow for lead chase orchestration."""
from __future__ import annotations

from datetime import timedelta

from temporalio import workflow

with workflow.unsafe.imports_passed_through():
    from src.models.enums import Disposition, MessageGoal
    from src.orchestrator.activities import (
        check_compliance_gates,
        execute_outbound_touch,
        load_lead,
        log_event,
        plan_next_touch,
        process_inbound,
        save_lead,
        write_to_clio,
    )


@workflow.defn
class OutboundChaseWorkflow:
    """Durable orchestrator for a single lead chase."""

    def __init__(self) -> None:
        self._pending_inbound: dict | None = None
        self._stop_chase = False

    @workflow.signal
    async def inbound_event(self, event_data: dict) -> None:
        self._pending_inbound = event_data

    @workflow.signal
    async def stop_chase(self) -> None:
        self._stop_chase = True
        self._pending_inbound = {"event_type": "stop"}

    @workflow.signal
    async def opt_out(self) -> None:
        self._stop_chase = True
        self._pending_inbound = {"event_type": "opt_out", "channel": "voice", "message": "opt out"}

    @workflow.run
    async def run(self, lead_id: str, intake_record: dict) -> dict:
        record = intake_record
        trigger = (
            intake_record.get("engagement", {}).get("trigger")
            or intake_record.get("trigger")
            or "web_form"
        )

        # Ingress-aware first touch
        initial_plan = await workflow.execute_activity(
            plan_next_touch,
            args=[lead_id, True, trigger],
            start_to_close_timeout=timedelta(seconds=15),
        )
        record = await self._execute_plan(lead_id, record, initial_plan)
        if self._is_terminal(record):
            return self._summary(lead_id, record)

        wait_seconds = int(initial_plan.get("wait_seconds", 0))
        if wait_seconds > 0:
            await self._race_timer_or_inbound(wait_seconds)
            if self._pending_inbound:
                record = await self._handle_inbound(lead_id, record)
                if self._is_terminal(record):
                    return self._summary(lead_id, record)

        while not self._is_terminal(record):
            if self._stop_chase:
                break

            plan = await workflow.execute_activity(
                plan_next_touch,
                args=[lead_id, False, None],
                start_to_close_timeout=timedelta(seconds=15),
            )

            if plan.get("terminal_action") == "give_up":
                await workflow.execute_activity(
                    execute_outbound_touch,
                    args=[
                        lead_id,
                        record,
                        plan.get("channel", "sms"),
                        MessageGoal.GIVE_UP.value,
                        0,
                        plan.get("reason", ""),
                        plan.get("constraints") or [],
                    ],
                    start_to_close_timeout=timedelta(seconds=30),
                )
                record = await workflow.execute_activity(
                    load_lead,
                    args=[lead_id],
                    start_to_close_timeout=timedelta(seconds=10),
                )
                break

            record = await self._execute_plan(lead_id, record, plan)

            last_action = record.get("_last_action")
            if last_action in ("stop", "escalate", "wrong_number"):
                terminal = {
                    "stop": Disposition.OPTED_OUT,
                    "escalate": Disposition.HANDOFF,
                    "wrong_number": Disposition.WRONG_NUMBER,
                }[last_action]
                await self._terminate(lead_id, terminal)
                record = await workflow.execute_activity(
                    load_lead,
                    args=[lead_id],
                    start_to_close_timeout=timedelta(seconds=10),
                )
                break

            if record.get("disposition") == Disposition.SIGNED.value:
                await workflow.execute_activity(
                    write_to_clio,
                    args=[record, lead_id, "SIGNED"],
                    start_to_close_timeout=timedelta(seconds=30),
                )
                break

            wait_seconds = int(plan.get("wait_seconds", 3600))
            await self._race_timer_or_inbound(wait_seconds)

            if self._pending_inbound:
                record = await self._handle_inbound(lead_id, record)
                if record.get("disposition") in (
                    Disposition.SIGNED.value,
                    Disposition.OPTED_OUT.value,
                    Disposition.WRONG_NUMBER.value,
                    Disposition.HANDOFF.value,
                ):
                    break

        if not self._is_terminal(record):
            await self._terminate(lead_id, Disposition.GAVE_UP)
            record = await workflow.execute_activity(
                load_lead,
                args=[lead_id],
                start_to_close_timeout=timedelta(seconds=10),
            )

        return self._summary(lead_id, record)

    async def _execute_plan(self, lead_id: str, record: dict, plan: dict) -> dict:
        """Run outbound touch from a strategy plan."""
        touch_result = await workflow.execute_activity(
            execute_outbound_touch,
            args=[
                lead_id,
                record,
                plan.get("channel", "sms"),
                plan.get("message_goal", MessageGoal.FOLLOW_UP.value),
                0,
                plan.get("reason", ""),
                plan.get("constraints") or [],
            ],
            start_to_close_timeout=timedelta(seconds=90),
        )
        updated = touch_result.get("record", record)
        action = touch_result.get("action") or touch_result.get("brain_result", {}).get("action")
        if action:
            updated["_last_action"] = action
        return updated

    async def _handle_inbound(self, lead_id: str, record: dict) -> dict:
        event_data = self._pending_inbound or {}
        self._pending_inbound = None

        if event_data.get("event_type") == "stop":
            self._stop_chase = True
            return record

        result = await workflow.execute_activity(
            process_inbound,
            args=[lead_id, event_data],
            start_to_close_timeout=timedelta(seconds=90),
        )

        if result.get("action") == "retainer_signed":
            await workflow.execute_activity(
                write_to_clio,
                args=[result.get("record", record), lead_id, "SIGNED"],
                start_to_close_timeout=timedelta(seconds=30),
            )
            return result.get("record", record)

        await workflow.execute_activity(
            log_event,
            args=[lead_id, "inbound.engaged", event_data.get("channel"), {"trigger": "inbound"}],
            start_to_close_timeout=timedelta(seconds=10),
        )

        updated = await workflow.execute_activity(
            load_lead,
            args=[lead_id],
            start_to_close_timeout=timedelta(seconds=10),
        )
        return updated or record

    async def _race_timer_or_inbound(self, wait_seconds: int) -> None:
        if wait_seconds <= 0:
            return
        try:
            await workflow.wait_condition(
                lambda: self._pending_inbound is not None or self._stop_chase,
                timeout=timedelta(seconds=wait_seconds),
            )
        except TimeoutError:
            pass

    def _is_terminal(self, record: dict) -> bool:
        if self._stop_chase:
            return True
        terminal = {
            Disposition.SIGNED.value,
            Disposition.GAVE_UP.value,
            Disposition.OPTED_OUT.value,
            Disposition.WRONG_NUMBER.value,
            Disposition.HANDOFF.value,
        }
        return record.get("disposition") in terminal

    def _summary(self, lead_id: str, record: dict) -> dict:
        return {
            "lead_id": lead_id,
            "disposition": record.get("disposition", "UNKNOWN"),
            "total_attempts": sum(record.get("engagement", {}).get("attempts_by_channel", {}).values()),
            "language": record.get("engagement", {}).get("language_observed", "en"),
        }

    async def _terminate(self, lead_id: str, state: Disposition) -> None:
        record = await workflow.execute_activity(
            load_lead,
            args=[lead_id],
            start_to_close_timeout=timedelta(seconds=10),
        )
        if record:
            record["disposition"] = state.value
            await workflow.execute_activity(
                save_lead,
                args=[record],
                start_to_close_timeout=timedelta(seconds=10),
            )

        await workflow.execute_activity(
            write_to_clio,
            args=[record or {}, lead_id, state.value],
            start_to_close_timeout=timedelta(seconds=30),
        )
        await workflow.execute_activity(
            log_event,
            args=[lead_id, f"terminal.{state.value}", None, {"disposition": state.value}],
            start_to_close_timeout=timedelta(seconds=10),
        )
