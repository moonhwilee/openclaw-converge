#!/usr/bin/env python3
"""Smoke coverage for C5 recovery scan/watchdog/recover commands."""

from __future__ import annotations

import tempfile
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path


try:
    from smoke_helpers import TEST_VISIBLE_DELIVERY, assert_true, run, run_fail, workflow, write_workflow
except ModuleNotFoundError:
    from tests.smoke.smoke_helpers import TEST_VISIBLE_DELIVERY, assert_true, run, run_fail, workflow, write_workflow

from converge.artifacts import manifest_entry


def old_iso(hours: int = 3) -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=hours)).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def future_iso(hours: int = 3) -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=hours)).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def make_stale(payload: dict, *, status: str = "running") -> dict:
    payload["status"] = status
    payload["last_activity_at"] = old_iso()
    payload["stale_after_seconds"] = 1
    return payload


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="converge-recovery-smoke-") as tmp:
        state_root = Path(tmp)
        wrapper_args = (
            "--owner-session-key",
            "session:test",
            "--visible-delivery",
            TEST_VISIBLE_DELIVERY,
        )

        empty_scan = run("scan", state_root=state_root)
        assert_true(empty_scan["status"] == "clean", "empty state should scan clean")
        empty_watchdog = run("watchdog-check", state_root=state_root)
        assert_true(not empty_watchdog["needs_wake"], "empty state should not wake")

        run("start", "--kind", "conv", "--text", "Recover stale conv", "--workflow-id", "stale-conv", *wrapper_args, state_root=state_root)
        stale_conv = make_stale(workflow(state_root, "stale-conv"))
        write_workflow(state_root, "stale-conv", stale_conv)
        stale_scan = run("scan", state_root=state_root)
        stale_record = next(item for item in stale_scan["workflows"] if item["workflow_id"] == "stale-conv")
        assert_true(stale_record["needs_recovery"] and stale_record["reason"] == "stale_active", "stale active workflow should need recovery")
        assert_true(stale_record["source_of_truth"]["owner"] == "converge", "recovery scan source of truth should be Converge")
        assert_true("Work Ledger" in stale_record["source_of_truth"]["not_source_of_truth"], "Work Ledger should not be recovery source of truth")
        stale_watchdog = run("watchdog-check", state_root=state_root)
        assert_true(stale_watchdog["needs_wake"], "stale active workflow should wake")
        stale_packet = next(item for item in stale_watchdog["recoveries"] if item["workflow_id"] == "stale-conv")
        assert_true(stale_packet["source_of_truth"]["state"] == "workflow_state", "watchdog packet should point at Converge workflow state")
        recovered = run("recover", "--workflow-id", "stale-conv", "--holder", "smoke", state_root=state_root)
        assert_true(recovered["recovered"], "recover should acquire a lease")
        leased = workflow(state_root, "stale-conv")["active_recovery_lease"]
        assert_true(leased["lease_type"] == "recovery" and leased["holder"] == "smoke", "recovery lease should persist")
        bad_conv_resume = run_fail("conv", "--text", "Recover stale conv", "--workflow-id", "stale-conv", state_root=state_root)
        assert_true(
            "active recovery lease requires matching recovery_lease_id" in bad_conv_resume["error"],
            "mode resume without lease args should fail before artifact mutation",
        )
        assert_true(
            not (state_root / "workflows" / "stale-conv" / "artifacts" / "conv-report.md").exists(),
            "failed leased mode resume should not write a mode artifact",
        )
        duplicate_recover = run("recover", "--workflow-id", "stale-conv", "--holder", "smoke-2", state_root=state_root)
        assert_true(duplicate_recover["blocked"] and duplicate_recover["reason"] == "active_recovery_lease_exists", "second recover should block on active lease")
        leased_watchdog = run("watchdog-check", state_root=state_root)
        assert_true(
            not any(item["workflow_id"] == "stale-conv" for item in leased_watchdog["recoveries"]),
            "watchdog should not wake a workflow with an active recovery lease",
        )
        conv_resume = run(
            "conv",
            "--text",
            "Recover stale conv",
            "--workflow-id",
            "stale-conv",
            "--recovery-lease-id",
            leased["lease_id"],
            "--recovery-lease-holder",
            leased["holder"],
            state_root=state_root,
        )
        assert_true(conv_resume["workflow"]["active_recovery_lease"] is None, "mode resume should clear matching recovery lease")

        for kind in ("plan", "verify", "goal"):
            workflow_id = f"stale-{kind}"
            run("start", "--kind", kind, "--text", f"Recover stale {kind}", "--workflow-id", workflow_id, *wrapper_args, state_root=state_root)
            stale_mode = make_stale(workflow(state_root, workflow_id))
            write_workflow(state_root, workflow_id, stale_mode)
            mode_scan = run("scan", state_root=state_root)
            mode_record = next(item for item in mode_scan["workflows"] if item["workflow_id"] == workflow_id)
            assert_true(mode_record["reason"] == "stale_active", f"{kind} interrupted/stale fixture should need recovery")
            mode_recover = run("recover", "--workflow-id", workflow_id, "--holder", "smoke", state_root=state_root)
            mode_lease = mode_recover["lease"]
            resumed = run(
                kind,
                "--text",
                f"Recover stale {kind}",
                "--workflow-id",
                workflow_id,
                "--recovery-lease-id",
                mode_lease["lease_id"],
                "--recovery-lease-holder",
                mode_lease["holder"],
                state_root=state_root,
            )
            assert_true(resumed["workflow"]["active_recovery_lease"] is None, f"{kind} resume should clear matching recovery lease")

        plan = run("plan", "--text", "Terminal recovery", "--workflow-id", "terminal-plan", *wrapper_args, state_root=state_root)
        assert_true(plan["workflow"]["status"] == "completed_unreported", "plan fixture should be terminal unreported")
        terminal_workflow = workflow(state_root, "terminal-plan")
        terminal_scan = run("scan", state_root=state_root)
        terminal_record = next(item for item in terminal_scan["workflows"] if item["workflow_id"] == "terminal-plan")
        assert_true(
            terminal_record["reason"] == "terminal_unreported",
            "real terminal unreported workflow should need reporting recovery",
        )
        terminal_recover = run("recover", "--workflow-id", "terminal-plan", "--holder", "smoke", state_root=state_root)
        assert_true(
            terminal_recover["blocked"] and terminal_recover["reason"] == "terminal_delivery_requires_reserve_delivery",
            "terminal unreported recovery should route through reserve-delivery, not a recovery lease",
        )
        assert_true(
            workflow(state_root, "terminal-plan").get("active_recovery_lease") is None,
            "terminal unreported recover should not persist a recovery lease",
        )
        reservation = run(
            "reserve-delivery",
            "--workflow-id",
            "terminal-plan",
            "--terminal-status",
            "completed",
            "--visible-delivery",
            TEST_VISIBLE_DELIVERY,
            "--summary",
            "reserve terminal recovery delivery",
            "--final-status",
            json.dumps(terminal_workflow["final_status"]),
            state_root=state_root,
        )
        assert_true(reservation["send_authority"] == "converge.reserve-delivery", "reserve-delivery should own send authority")
        assert_true(reservation["source_of_truth"] == "converge.workflow", "reserve-delivery should source from Converge workflow state")
        reserved_workflow = workflow(state_root, "terminal-plan")
        assert_true(
            reserved_workflow["active_delivery_reservation"]["send_authority"] == "converge.reserve-delivery"
            and reserved_workflow["active_delivery_reservation"]["source_of_truth"] == "converge.workflow",
            "terminal recovery reservation should persist Converge authority metadata",
        )
        reserved_event = [
            event
            for event in (json.loads(line) for line in (state_root / "workflows" / "terminal-plan" / "events.jsonl").read_text(encoding="utf-8").splitlines() if line.strip())
            if event["event_type"] == "delivery_reserved"
        ][-1]
        assert_true(
            reserved_event["payload"]["send_authority"] == "converge.reserve-delivery"
            and reserved_event["payload"]["source_of_truth"] == "converge.workflow",
            "terminal recovery delivery event should persist Converge authority metadata",
        )
        reserved_scan = run("scan", state_root=state_root)
        reserved_record = next(item for item in reserved_scan["workflows"] if item["workflow_id"] == "terminal-plan")
        assert_true(
            reserved_record["reason"] == "terminal_unreported",
            "delivery reservation event should not hide terminal recovery",
        )
        proof = run(
            "report-proof",
            "--workflow-id",
            "terminal-plan",
            "--reservation-id",
            reservation["reservation_id"],
            "--delivery-message-id",
            "telegram-terminal-plan-proof",
            "--visible-delivery",
            TEST_VISIBLE_DELIVERY,
            state_root=state_root,
        )
        assert_true(proof["proof"]["proof_authority"] == "converge.report-proof", "report-proof should own proof authority")
        assert_true(proof["proof"]["source_of_truth"] == "converge.workflow", "report-proof should source from Converge workflow state")
        proof_scan = run("scan", state_root=state_root)
        proof_record = next(item for item in proof_scan["workflows"] if item["workflow_id"] == "terminal-plan")
        assert_true(
            proof_record["reason"] == "terminal_unreported",
            "report-proof event should not hide terminal recovery before reported completion",
        )
        reported = run(
            "complete-reported",
            "--workflow-id",
            "terminal-plan",
            "--reservation-id",
            reservation["reservation_id"],
            "--delivery-message-id",
            "telegram-terminal-plan-proof",
            "--visible-delivery",
            TEST_VISIBLE_DELIVERY,
            state_root=state_root,
        )
        reported_state = workflow(state_root, "terminal-plan")["visible_delivery_state"]["reported"]
        assert_true(reported["status"] == "reported", "complete-reported should report the workflow")
        assert_true(reported_state["report_authority"] == "converge.complete-reported", "complete-reported should own reported transition")
        assert_true(reported_state["source_of_truth"] == "converge.workflow", "complete-reported should source from Converge workflow state")
        reported_scan = run("scan", state_root=state_root)
        reported_record = next(item for item in reported_scan["workflows"] if item["workflow_id"] == "terminal-plan")
        assert_true(
            not reported_record["needs_recovery"] and reported_record["reason"] == "clean",
            "reported workflow should not wake recovery on report pipeline events",
        )

        run("plan", "--text", "Worklog mismatch recovery", "--workflow-id", "worklog-mismatch-plan", *wrapper_args, state_root=state_root)
        (state_root / "workflows" / "worklog-mismatch-plan" / "worklog.md").write_text(
            "# Converge Worklog\n\n",
            encoding="utf-8",
        )
        worklog_mismatch = run("recover", "--workflow-id", "worklog-mismatch-plan", "--holder", "smoke", state_root=state_root)
        assert_true(
            worklog_mismatch["blocked"] and worklog_mismatch["reason"] == "worklog_mismatch",
            "missing checkpoint worklog block should block recovery",
        )

        run("start", "--kind", "goal", "--text", "Waiting recovery", "--workflow-id", "waiting-goal", *wrapper_args, state_root=state_root)
        waiting = workflow(state_root, "waiting-goal")
        waiting["status"] = "waiting_user"
        waiting["last_visible_update_at"] = old_iso()
        waiting["reminder_after_seconds"] = 1
        write_workflow(state_root, "waiting-goal", waiting)
        waiting_scan = run("scan", state_root=state_root)
        waiting_record = next(item for item in waiting_scan["workflows"] if item["workflow_id"] == "waiting-goal")
        assert_true(waiting_record["reason"] == "waiting_user_reminder_due", "waiting_user should need a stale reminder")

        context_file = state_root / "context.txt"
        context_file.write_text("original\n", encoding="utf-8")
        run("start", "--kind", "goal", "--text", "Context recovery", "--workflow-id", "context-goal", *wrapper_args, state_root=state_root)
        context_goal = make_stale(workflow(state_root, "context-goal"))
        context_goal["context_manifest"] = [manifest_entry(context_file)]
        context_file.write_text("changed\n", encoding="utf-8")
        write_workflow(state_root, "context-goal", context_goal)
        context_recover = run("recover", "--workflow-id", "context-goal", "--holder", "smoke", state_root=state_root)
        assert_true(context_recover["blocked"] and context_recover["reason"] == "context_manifest_stale", "stale context should block recovery")

        run("start", "--kind", "goal", "--text", "Checkpoint mismatch recovery", "--workflow-id", "mismatch-goal", *wrapper_args, state_root=state_root)
        mismatch_goal = make_stale(workflow(state_root, "mismatch-goal"))
        mismatch_goal["continuation_plan"]["rolling_state"]["last_checkpoint_id"] = "chk-missing"
        write_workflow(state_root, "mismatch-goal", mismatch_goal)
        mismatch_recover = run("recover", "--workflow-id", "mismatch-goal", "--holder", "smoke", state_root=state_root)
        assert_true(mismatch_recover["blocked"] and mismatch_recover["reason"] == "checkpoint_state_mismatch", "checkpoint disagreement should block recovery")

        run("start", "--kind", "goal", "--text", "Recovery event mismatch", "--workflow-id", "recovery-event-mismatch", *wrapper_args, state_root=state_root)
        event_mismatch = make_stale(workflow(state_root, "recovery-event-mismatch"))
        write_workflow(state_root, "recovery-event-mismatch", event_mismatch)
        events_path = state_root / "workflows" / "recovery-event-mismatch" / "events.jsonl"
        with events_path.open("a", encoding="utf-8") as fh:
            fh.write(
                json.dumps(
                    {
                        "schema_version": 1,
                        "event_id": "evt-recovery-orphan",
                        "workflow_id": "recovery-event-mismatch",
                        "event_type": "recovery_lease_acquired",
                        "created_at": old_iso(),
                        "payload": {"lease_id": "recovery-orphan", "holder": "smoke"},
                    },
                    sort_keys=True,
                )
                + "\n"
            )
        event_mismatch_recover = run("recover", "--workflow-id", "recovery-event-mismatch", "--holder", "smoke", state_root=state_root)
        assert_true(
            event_mismatch_recover["blocked"] and event_mismatch_recover["reason"] == "recovery_lease_transaction_mismatch",
            "orphan recovery lease event should block recovery",
        )

        run("start", "--kind", "goal", "--text", "Recovery workflow mismatch", "--workflow-id", "recovery-workflow-mismatch", *wrapper_args, state_root=state_root)
        workflow_mismatch = make_stale(workflow(state_root, "recovery-workflow-mismatch"))
        workflow_mismatch["active_recovery_lease"] = {
            "lease_id": "recovery-missing-event",
            "lease_type": "recovery",
            "cursor": workflow_mismatch["next_safe_action"]["cursor"],
            "holder": "smoke",
            "acquired_at": old_iso(),
            "lease_expires_at": (datetime.now(timezone.utc) + timedelta(hours=1)).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            "checkpoint_id": "no-checkpoint",
        }
        write_workflow(state_root, "recovery-workflow-mismatch", workflow_mismatch)
        workflow_mismatch_recover = run("recover", "--workflow-id", "recovery-workflow-mismatch", "--holder", "smoke", state_root=state_root)
        assert_true(
            workflow_mismatch_recover["blocked"] and workflow_mismatch_recover["reason"] == "recovery_lease_transaction_mismatch",
            "workflow-only recovery lease should block recovery",
        )

        run("start", "--kind", "goal", "--text", "Pending recovery", "--workflow-id", "pending-recovery-goal", *wrapper_args, state_root=state_root)
        pending_goal = make_stale(workflow(state_root, "pending-recovery-goal"))
        write_workflow(state_root, "pending-recovery-goal", pending_goal)
        pending_dir = state_root / "workflows" / "pending-recovery-goal"
        (pending_dir / ".pending-recovery-stale.json").write_text(
            json.dumps({"workflow_id": "pending-recovery-goal", "lease_id": "recovery-stale"}, sort_keys=True),
            encoding="utf-8",
        )
        pending_recover = run("recover", "--workflow-id", "pending-recovery-goal", "--holder", "smoke", state_root=state_root)
        assert_true(
            pending_recover["blocked"] and pending_recover["reason"] == "pending_recovery_lease",
            "pending recovery marker should block new recovery",
        )
        pending_resume = run_fail(
            "goal",
            "--text",
            "Pending recovery",
            "--workflow-id",
            "pending-recovery-goal",
            state_root=state_root,
        )
        assert_true(
            "pending recovery lease transaction requires reconcile" in pending_resume["error"],
            "pending recovery marker should block normal mode checkpoint",
        )
        assert_true(
            not (pending_dir / "artifacts" / "goal-plan.md").exists(),
            "pending recovery marker should fail before artifact mutation",
        )

        fake_lease_plan = run_fail(
            "plan",
            "--text",
            "Fake lease",
            "--workflow-id",
            "fake-lease-plan",
            "--recovery-lease-id",
            "recovery-fake",
            "--recovery-lease-holder",
            "smoke",
            state_root=state_root,
        )
        assert_true(
            "recovery lease args require an existing workflow" in fake_lease_plan["error"],
            "fake recovery lease args should not checkpoint a normal mode run",
        )
        assert_true(
            not (state_root / "workflows" / "fake-lease-plan" / "artifacts" / "plan.md").exists(),
            "fake recovery lease args should fail before artifact mutation",
        )

        run("start", "--kind", "plan", "--text", "Expired direct resume", "--workflow-id", "expired-direct-plan", *wrapper_args, state_root=state_root)
        expired_direct = make_stale(workflow(state_root, "expired-direct-plan"))
        write_workflow(state_root, "expired-direct-plan", expired_direct)
        expired_direct_recover = run("recover", "--workflow-id", "expired-direct-plan", "--holder", "old", state_root=state_root)
        expired_direct_lease = expired_direct_recover["lease"]
        expired_direct = workflow(state_root, "expired-direct-plan")
        expired_direct["active_recovery_lease"]["lease_expires_at"] = old_iso()
        write_workflow(state_root, "expired-direct-plan", expired_direct)
        expired_direct_resume = run_fail(
            "plan",
            "--text",
            "Expired direct resume",
            "--workflow-id",
            "expired-direct-plan",
            "--recovery-lease-id",
            expired_direct_lease["lease_id"],
            "--recovery-lease-holder",
            expired_direct_lease["holder"],
            state_root=state_root,
        )
        assert_true(
            "active recovery lease expired; run recover again" in expired_direct_resume["error"],
            "expired recovery lease should fail before artifact mutation",
        )
        assert_true(
            not (state_root / "workflows" / "expired-direct-plan" / "artifacts" / "plan.md").exists(),
            "expired recovery lease should fail before artifact mutation",
        )

        run("start", "--kind", "goal", "--text", "Expired lease recovery", "--workflow-id", "expired-lease-goal", *wrapper_args, state_root=state_root)
        expired_goal = make_stale(workflow(state_root, "expired-lease-goal"))
        write_workflow(state_root, "expired-lease-goal", expired_goal)
        old_recover = run("recover", "--workflow-id", "expired-lease-goal", "--holder", "old", state_root=state_root)
        old_lease = old_recover["lease"]
        expired_goal = workflow(state_root, "expired-lease-goal")
        expired_goal["active_recovery_lease"]["lease_expires_at"] = old_iso()
        expired_goal["last_activity_at"] = old_iso(6)
        write_workflow(state_root, "expired-lease-goal", expired_goal)
        new_recover = run("recover", "--workflow-id", "expired-lease-goal", "--holder", "new", state_root=state_root)
        new_lease = new_recover["lease"]
        assert_true(new_lease["lease_id"] != old_lease["lease_id"], "expired recovery lease should be superseded")
        expired_resume = run(
            "goal",
            "--text",
            "Expired lease recovery",
            "--workflow-id",
            "expired-lease-goal",
            "--recovery-lease-id",
            new_lease["lease_id"],
            "--recovery-lease-holder",
            new_lease["holder"],
            state_root=state_root,
        )
        assert_true(expired_resume["workflow"]["active_recovery_lease"] is None, "superseded recovery lease should resume with the new lease")
        expired_scan = run("scan", state_root=state_root)
        expired_record = next(item for item in expired_scan["workflows"] if item["workflow_id"] == "expired-lease-goal")
        assert_true(
            expired_record["reason"] == "terminal_unreported",
            "superseded expired lease should not mask terminal-unreported routing",
        )

        run("start", "--kind", "goal", "--text", "Cursor mismatch recovery", "--workflow-id", "cursor-mismatch-goal", *wrapper_args, state_root=state_root)
        cursor_mismatch_goal = make_stale(workflow(state_root, "cursor-mismatch-goal"))
        cursor_mismatch_goal["next_safe_action"]["cursor"] = "wrong-cursor"
        write_workflow(state_root, "cursor-mismatch-goal", cursor_mismatch_goal)
        cursor_mismatch_recover = run("recover", "--workflow-id", "cursor-mismatch-goal", "--holder", "smoke", state_root=state_root)
        assert_true(
            cursor_mismatch_recover["blocked"] and cursor_mismatch_recover["reason"] == "next_safe_action_cursor_mismatch",
            "next_safe_action cursor disagreement should block recovery",
        )

        run("start", "--kind", "goal", "--text", "Side effect recovery", "--workflow-id", "side-effect-goal", *wrapper_args, state_root=state_root)
        side_effect_goal = make_stale(workflow(state_root, "side-effect-goal"))
        side_effect_key = side_effect_goal["next_safe_action"]["side_effect_key"]
        side_effect_goal["next_safe_action"]["idempotency_policy"] = "reconcile_first"
        side_effect_goal["side_effects_performed"] = [{"side_effect_key": side_effect_key, "idempotency_policy": "reconcile_first"}]
        write_workflow(state_root, "side-effect-goal", side_effect_goal)
        side_effect_recover = run("recover", "--workflow-id", "side-effect-goal", "--holder", "smoke", state_root=state_root)
        assert_true(side_effect_recover["blocked"] and side_effect_recover["reason"] == "side_effect_reconcile_required", "repeated non-repeatable side effect should block")

        run("start", "--kind", "goal", "--text", "Risky recovery", "--workflow-id", "risky-goal", *wrapper_args, state_root=state_root)
        risky_goal = make_stale(workflow(state_root, "risky-goal"))
        risky_goal["next_safe_action"]["risk_class"] = "external"
        risky_goal["next_safe_action"]["requires_approval"] = True
        risky_goal["next_safe_action"]["side_effect_key"] = "external:risky-goal"
        write_workflow(state_root, "risky-goal", risky_goal)
        risky_recover = run("recover", "--workflow-id", "risky-goal", "--holder", "smoke", state_root=state_root)
        assert_true(risky_recover["blocked"] and risky_recover["reason"] == "risky_side_effect_requires_approval", "risky side effect should block without approval")

        def prepare_risky_recovery(workflow_id: str, approvals: list[dict]) -> dict:
            run("start", "--kind", "goal", "--text", f"Risky recovery {workflow_id}", "--workflow-id", workflow_id, *wrapper_args, state_root=state_root)
            payload = make_stale(workflow(state_root, workflow_id))
            payload["next_safe_action"]["risk_class"] = "gateway_runtime"
            payload["next_safe_action"]["requires_approval"] = True
            payload["next_safe_action"]["side_effect_key"] = f"gateway:{workflow_id}"
            payload["next_safe_action"]["approval_ref"] = f"approval:{workflow_id}"
            payload["approvals"] = approvals
            write_workflow(state_root, workflow_id, payload)
            return run("recover", "--workflow-id", workflow_id, "--holder", "smoke", state_root=state_root)

        expired_recover = prepare_risky_recovery(
            "expired-approval-goal",
            [
                {
                    "approval_id": "approval-expired",
                    "side_effect_key": "gateway:expired-approval-goal",
                    "scope": "approval:expired-approval-goal",
                    "expires_at": old_iso(),
                    "consumed_by_event_id": None,
                }
            ],
        )
        assert_true(expired_recover["blocked"] and expired_recover["reason"] == "risky_side_effect_requires_approval", "expired approval should not authorize recovery")

        consumed_recover = prepare_risky_recovery(
            "consumed-approval-goal",
            [
                {
                    "approval_id": "approval-consumed",
                    "side_effect_key": "gateway:consumed-approval-goal",
                    "scope": "approval:consumed-approval-goal",
                    "expires_at": future_iso(),
                    "consumed_by_event_id": "evt-consumed",
                }
            ],
        )
        assert_true(consumed_recover["blocked"] and consumed_recover["reason"] == "risky_side_effect_requires_approval", "consumed approval should not authorize recovery")

        broad_recover = prepare_risky_recovery(
            "broad-approval-goal",
            [
                {
                    "approval_id": "approval-broad",
                    "side_effect_key": "gateway:*",
                    "scope": "approval:broad",
                    "expires_at": future_iso(),
                    "consumed_by_event_id": None,
                }
            ],
        )
        assert_true(broad_recover["blocked"] and broad_recover["reason"] == "risky_side_effect_requires_approval", "broad approval should not authorize exact recovery")

        valid_recover = prepare_risky_recovery(
            "valid-approval-goal",
            [
                {
                    "approval_id": "approval-valid",
                    "side_effect_key": "gateway:valid-approval-goal",
                    "scope": "approval:valid-approval-goal",
                    "expires_at": future_iso(),
                    "consumed_by_event_id": None,
                }
            ],
        )
        assert_true(valid_recover.get("recovered") is True, "valid exact approval should authorize recovery")
        valid_workflow = workflow(state_root, "valid-approval-goal")
        valid_approval = valid_workflow["approvals"][0]
        assert_true(
            valid_approval.get("consumed_by_event_id", "").startswith("evt-recovery-"),
            "used approval should be consumed by the recovery event",
        )

    print('{"ok": true, "checked": "recovery scan/watchdog/recover"}')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
