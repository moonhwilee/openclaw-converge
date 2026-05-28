#!/usr/bin/env python3
"""Smoke coverage for native orchestration adapter contracts."""

from __future__ import annotations

import copy
import hashlib
import json
import subprocess
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    from smoke_helpers import assert_true
except ModuleNotFoundError:
    from tests.smoke.smoke_helpers import assert_true

from converge.agents.classifier import classify_panel_decision  # noqa: E402
from converge.agents.contracts import (  # noqa: E402
    DEFAULT_BUDGET_POLICY,
    DEFAULT_TIMEOUT_POLICY,
    DEFAULT_TOOL_POLICY,
    SOURCE_NATIVE_AGENT_PANEL,
    SOURCE_RUNNER_PROVIDED_PACKET,
    STATUS_COMPLETED,
    STATUS_TIMED_OUT,
    TOOL_SMOKE_NOT_APPLICABLE,
    InMemoryOpenClawSessionBackend,
    NativeChildResult,
    NativeLaunchRequest,
    classify_execution_source,
    stable_hash,
    validate_budget_policy,
    validate_fix_runner_request,
    validate_fix_runner_result,
    validate_native_child_result,
    validate_native_launch_request,
    validate_openclaw_session_payload,
    validate_timeout_policy,
    validate_tool_policy,
)
from converge.agents.openclaw_cli import (  # noqa: E402
    OpenClawAgentCliBackend,
    OpenClawNativePanelCliBackend,
    build_child_prompt,
    validate_openclaw_agent_session_key,
)


def expect_error(func, contains: str) -> None:
    try:
        func()
    except ValueError as exc:
        assert_true(contains in str(exc), f"expected error containing {contains!r}, got {exc!s}")
        return
    raise AssertionError(f"expected ValueError containing {contains!r}")


def launch_request(profile_ref: str = "reviewer-contract", key_suffix: str = "a") -> NativeLaunchRequest:
    return NativeLaunchRequest(
        mode="verify",
        objective="Audit the native adapter boundary",
        target_refs=[{"kind": "file", "path": "converge/agents/contracts.py"}],
        profile_ref=profile_ref,
        context_hash="sha256:context",
        idempotency_key=f"sha256:task-context-profile-{key_suffix}",
        output_schema={"schema_ref": "native_agent_result.v1"},
        session_key=f"session:child:{key_suffix}",
        profile_context_refs=[{"kind": "profile", "id": profile_ref}],
    )


def assert_default_policies_are_enforced() -> None:
    validate_tool_policy(DEFAULT_TOOL_POLICY)
    validate_timeout_policy(DEFAULT_TIMEOUT_POLICY)
    validate_budget_policy(DEFAULT_BUDGET_POLICY)

    bad_tool_policy = copy.deepcopy(DEFAULT_TOOL_POLICY)
    bad_tool_policy["visible_messages"] = "allowed"
    expect_error(lambda: validate_tool_policy(bad_tool_policy), "visible_messages")

    bad_timeout = copy.deepcopy(DEFAULT_TIMEOUT_POLICY)
    bad_timeout["panel_collection_seconds"] = 1
    expect_error(lambda: validate_timeout_policy(bad_timeout), "panel_collection_seconds")

    bad_budget = copy.deepcopy(DEFAULT_BUDGET_POLICY)
    bad_budget["panel_size_default"] = 4
    expect_error(lambda: validate_budget_policy(bad_budget), "default native panel size")


def assert_openclaw_session_contract_requires_explicit_session_refs() -> None:
    payload = launch_request().as_dict()
    validate_native_launch_request(payload)
    session_payload = {
        **payload,
        "launch": "session:child:a",
        "wait": "session:child:a",
        "collect": "session:child:a",
        "recover": "session:child:a",
        "tool_smoke_required": True,
    }
    validate_openclaw_session_payload(session_payload)

    current_payload = {**payload, "session_key": "current"}
    expect_error(lambda: validate_native_launch_request(current_payload), "non-current session_key")

    implicit_wait = {**session_payload, "wait": "current"}
    expect_error(lambda: validate_openclaw_session_payload(implicit_wait), "explicit wait session ref")


def assert_native_result_schema_requires_tool_smoke() -> None:
    result = NativeChildResult(
        request_id="req-1",
        result_id="res-1",
        agent_session_ref="session:child:contract",
        session_key="session:child:contract",
        tool_smoke_status="passed",
        profile_ref="reviewer-contract",
        context_hash="sha256:context",
        status=STATUS_COMPLETED,
        findings=[],
        started_at="2026-05-28T00:00:00Z",
        deadline_at="2026-05-28T00:15:00Z",
        completed_at="2026-05-28T00:01:00Z",
        tool_smoke_evidence={
            "status": "passed",
            "session_key": "session:child:contract",
            "agent_session_ref": "session:child:contract",
            "kind": "coordinator_verified_fixture",
            "checked_at": "2026-05-28T00:01:00Z",
        },
    ).as_dict()
    validate_native_child_result(result)

    no_smoke = {**result, "tool_smoke_status": "not_run"}
    expect_error(lambda: validate_native_child_result(no_smoke), "passed tool_smoke_status")

    no_smoke_evidence = {**result, "tool_smoke_evidence": None}
    expect_error(lambda: validate_native_child_result(no_smoke_evidence), "tool_smoke_evidence")

    runner_packet = {
        **result,
        "source_classification": SOURCE_RUNNER_PROVIDED_PACKET,
        "tool_smoke_status": TOOL_SMOKE_NOT_APPLICABLE,
        "satisfies_native_agent_panel": True,
    }
    expect_error(lambda: validate_native_child_result(runner_packet), "advisory source")

    timed_out = {
        **result,
        "status": STATUS_TIMED_OUT,
        "tool_smoke_status": "not_run",
        "timeout_reason": "lease_expired",
    }
    validate_native_child_result(timed_out)


def assert_fake_backend_lifecycle_idempotency_and_timeout() -> None:
    backend = InMemoryOpenClawSessionBackend()
    now = datetime(2026, 5, 28, 0, 0, tzinfo=timezone.utc)
    first = backend.launch(launch_request(), now=now)
    assert_true(first.status == "active", "new launch should become active")
    assert_true(first.events == ("launch_requested", "active"), "launch should record requested then active lifecycle")

    active_reuse = backend.launch(launch_request(), now=now + timedelta(seconds=1))
    assert_true(active_reuse.request_id == first.request_id, "matching active idempotency key should reuse request")
    assert_true(active_reuse.reuse_state == "active_reuse", "active idempotency key should attach to lease")

    completed = backend.collect_result(first.request_id, findings=[], now=now + timedelta(seconds=2))
    assert_true(completed.status == STATUS_COMPLETED, "completed fake child should report completed")
    completed_reuse = backend.launch(launch_request(), now=now + timedelta(seconds=3))
    assert_true(completed_reuse.reuse_state == "completed_reuse", "completed idempotency key should be reused")
    assert_true(completed_reuse.result == completed, "completed reuse should preserve original result")

    timeout_request = launch_request(profile_ref="reviewer-timeout", key_suffix="timeout")
    timeout_record = backend.launch(timeout_request, now=now)
    expired = backend.expire_timeouts(now=now + timedelta(seconds=901))
    assert_true(len(expired) == 1 and expired[0].status == STATUS_TIMED_OUT, "active lease should expire as terminal timeout")
    late = backend.collect_result(timeout_record.request_id, findings=[], now=now + timedelta(seconds=902))
    stored = backend.records_by_request_id[timeout_record.request_id]
    assert_true(len(backend.late_results) == 1 and backend.late_results[0] == late, "late result should be recorded separately")
    assert_true(stored.status == STATUS_TIMED_OUT, "late result must not overwrite terminal timeout")
    assert_true(stored.result is not None and stored.result.status == STATUS_TIMED_OUT, "stored result should remain timeout")


def assert_panel_collection_blocks_partial_failure() -> None:
    backend = InMemoryOpenClawSessionBackend()
    now = datetime(2026, 5, 28, 0, 0, tzinfo=timezone.utc)
    records = [
        backend.launch(launch_request(profile_ref=f"reviewer-{idx}", key_suffix=str(idx)), now=now)
        for idx in range(3)
    ]
    backend.collect_result(records[0].request_id, findings=[], now=now + timedelta(seconds=1))
    backend.collect_result(records[1].request_id, findings=[], now=now + timedelta(seconds=1))
    backend.collect_result(records[2].request_id, findings=[], now=now + timedelta(seconds=1), status="failed", error="tool smoke failed")

    blocked = backend.collect_panel([record.request_id for record in records])
    assert_true(blocked.success is False and blocked.status == "blocked", "partial panel failure should block success")
    degraded = backend.collect_panel([record.request_id for record in records], allow_degraded=True)
    assert_true(degraded.success is True and degraded.degraded is True, "explicit degraded policy should be visible")


def assert_source_and_fix_runner_contracts() -> None:
    native = classify_execution_source(SOURCE_NATIVE_AGENT_PANEL)
    runner = classify_execution_source(SOURCE_RUNNER_PROVIDED_PACKET)
    assert_true(native["satisfies_native_agent_panel"] is True, "native source should satisfy native panel")
    assert_true(runner["advisory_only"] is True, "runner packets should be advisory for native parity")
    assert_true(runner["satisfies_native_agent_panel"] is False, "runner packets must not satisfy native parity")
    expect_error(lambda: classify_execution_source("delegated_agents"), "unknown execution source")

    fix_policy = {
        "policy_id": "fix-runner-bounded-v1",
        "filesystem": "workspace_write",
        "target_mutation": "bounded_by_accepted_change_refs",
        "visible_messages": "forbidden",
        "workflow_state_mutation": "forbidden",
        "service_restart": "forbidden",
        "external_actions": "forbidden",
        "push_or_pr": "forbidden",
        "release": "forbidden",
        "allowed_actions": ["apply_accepted_changes", "run_focused_checks"],
    }
    def fix_result(
        *,
        accepted_change_refs: list[dict[str, str]] | None = None,
        applied_change_refs: list[dict[str, str]] | None = None,
        focused_check_results: list[dict[str, str]] | None = None,
        file_mutations: list[dict[str, str]] | None = None,
        **overrides: object,
    ) -> dict[str, object]:
        accepted = accepted_change_refs or [
            {
                "change_ref": "accepted-change-1",
                "local_file_edits": [{"path": "target.txt", "old": "before", "new": "after"}],
            }
        ]
        applied = applied_change_refs or accepted
        before_sha = hashlib.sha256("before".encode("utf-8")).hexdigest()
        after_sha = hashlib.sha256("after".encode("utf-8")).hexdigest()
        checks = focused_check_results or [
            {
                "check_id": "focused-check-1",
                "change_ref": "accepted-change-1",
                "kind": "bounded_local_file_edit",
                "status": "pass",
                "mutation_count": 1,
                "mutation_paths": ["target.txt"],
                "mutation_hashes": [{"path": "target.txt", "before_sha256": before_sha, "after_sha256": after_sha}],
            }
        ]
        mutations = file_mutations or [
            {
                "change_ref": "accepted-change-1",
                "path": "target.txt",
                "before_sha256": before_sha,
                "after_sha256": after_sha,
            }
        ]
        payload: dict[str, object] = {
            "result_id": "fix-runner-local-v1-result",
            "runner_id": "fix-runner-local-v1",
            "mode": "conv",
            "workflow_id": "conv-fix",
            "source_root": "/tmp/converge-fix-runner-source",
            "source_classification": "fix_runner",
            "status": "completed",
            "accepted_change_refs": accepted,
            "applied_change_refs": applied,
            "focused_check_results": checks,
            "material_change_applied": True,
            "artifact_refs": ["fix-runner-local-v1-result"],
            "tool_policy": fix_policy,
            "side_effects_performed": [],
            "file_mutations": mutations,
        }
        payload.update(overrides)
        payload.setdefault(
            "idempotency_key",
            stable_hash(
                {
                    "runner_id": payload["runner_id"],
                    "workflow_id": payload["workflow_id"],
                    "source_root": payload["source_root"],
                    "accepted_change_refs": payload["accepted_change_refs"],
                    "file_mutations": payload["file_mutations"],
                }
            ),
        )
        return payload
    validate_fix_runner_request(
        {
            "runner_id": "fix-runner-local-v1",
            "mode": "conv",
            "objective": "Apply accepted local changes",
            "source_classification": "fix_runner",
            "accepted_change_refs": [
                {
                    "change_ref": "accepted-change-1",
                    "local_file_edits": [{"path": "target.txt", "old": "before", "new": "after"}],
                }
            ],
            "tool_policy": fix_policy,
        }
    )
    validate_fix_runner_result(fix_result())
    expect_error(
        lambda: validate_fix_runner_request(
            {
                "runner_id": "fix-runner-local-v1",
                "mode": "conv",
                "objective": "Bad reviewer mutation",
                "source_classification": "fix_runner",
                "agent_session_ref": "session:reviewer",
                "accepted_change_refs": [
                    {
                        "change_ref": "accepted-change-1",
                        "local_file_edits": [{"path": "target.txt", "old": "before", "new": "after"}],
                    }
                ],
                "tool_policy": fix_policy,
            }
        ),
        "coordinator-owned",
    )
    expect_error(
        lambda: validate_fix_runner_request(
            {
                "runner_id": "fix-runner-local-v1",
                "mode": "conv",
                "objective": "Malformed change ref",
                "source_classification": "fix_runner",
                "accepted_change_refs": [{"accepted_change_id": "accepted-change-1"}],
                "tool_policy": fix_policy,
            }
        ),
        "change_ref",
    )
    expect_error(
        lambda: validate_fix_runner_result(fix_result(agent_session_ref="session:reviewer")),
        "coordinator-owned",
    )
    expect_error(
        lambda: validate_fix_runner_result(
            fix_result(
                accepted_change_refs=[{"accepted_change_id": "accepted-change-1"}],  # type: ignore[list-item]
                applied_change_refs=[{"accepted_change_id": "accepted-change-1"}],  # type: ignore[list-item]
                focused_check_results=[{"check_id": "focused-check-1", "status": "pass"}],
            )
        ),
        "change_ref",
    )
    expect_error(
        lambda: validate_fix_runner_result(
            fix_result(focused_check_results=[{"check_id": "focused-check-1", "change_ref": "other-change", "status": "pass"}])
        ),
        "focused checks",
    )
    expect_error(
        lambda: validate_fix_runner_result(fix_result(material_change_applied=False)),
        "material_change_applied",
    )
    expect_error(
        lambda: validate_fix_runner_result(fix_result(gateway_restart_performed=True)),
        "forbidden side effects",
    )
    expect_error(
        lambda: validate_fix_runner_result(fix_result(artifact_refs=["other-artifact"])),
        "artifact_refs",
    )
    expect_error(
        lambda: validate_fix_runner_result(fix_result(source_root="/tmp/other-root", idempotency_key="stale-idempotency")),
        "idempotency_key",
    )
    expect_error(
        lambda: validate_fix_runner_result(
            fix_result(
                accepted_change_refs=[
                    {
                        "change_ref": "accepted-change-1",
                        "local_file_edits": [{"path": "target.txt", "old": "before", "new": "after"}],
                    },
                    {
                        "change_ref": "accepted-change-2",
                        "local_file_edits": [{"path": "target2.txt", "old": "before", "new": "after"}],
                    },
                ],
                applied_change_refs=[
                    {
                        "change_ref": "accepted-change-1",
                        "local_file_edits": [{"path": "target.txt", "old": "before", "new": "after"}],
                    },
                    {
                        "change_ref": "accepted-change-2",
                        "local_file_edits": [{"path": "target2.txt", "old": "before", "new": "after"}],
                    },
                ],
                focused_check_results=[{"check_id": "focused-check-1", "change_ref": "accepted-change-1", "status": "pass"}],
                file_mutations=[
                    {
                        "change_ref": "accepted-change-1",
                        "path": "target.txt",
                        "before_sha256": "0" * 64,
                        "after_sha256": "1" * 64,
                    },
                    {
                        "change_ref": "accepted-change-2",
                        "path": "target2.txt",
                        "before_sha256": "2" * 64,
                        "after_sha256": "3" * 64,
                    },
                ],
            )
        ),
        "focused checks",
    )
    expect_error(
        lambda: validate_fix_runner_request(
            {
                "runner_id": "fix-runner-local-v1",
                "mode": "conv",
                "objective": "Duplicate change refs",
                "source_classification": "fix_runner",
                "accepted_change_refs": [
                    {
                        "change_ref": "accepted-change-1",
                        "local_file_edits": [{"path": "target.txt", "old": "before", "new": "after"}],
                    },
                    {
                        "change_ref": "accepted-change-1",
                        "local_file_edits": [{"path": "target.txt", "old": "before", "new": "after"}],
                    },
                ],
                "tool_policy": fix_policy,
            }
        ),
        "duplicate",
    )


def assert_risk_intent_classifier_defaults() -> None:
    low = classify_panel_decision("Read-only audit this file", mode="verify")
    assert_true(low.requires_panel is False and low.panel_size == 3, "low-risk verify should not force panel")

    high = classify_panel_decision("Deploy and restart Gateway after review", mode="verify")
    assert_true(high.requires_panel is True and high.panel_size == 5, "high-risk terms should force five specialists")

    conv = classify_panel_decision("Implement and improve this convergence loop", mode="conv")
    assert_true(conv.requires_panel is True and conv.panel_size == 3, "material conv intent should require panel")


def assert_openclaw_cli_backend_uses_explicit_session_and_structured_result() -> None:
    request = NativeLaunchRequest(
        **{
            **launch_request().as_dict(),
            "session_key": "agent:main:child-a",
        }
    )

    def runner(command: list[str], timeout_seconds: int) -> subprocess.CompletedProcess[str]:
        assert_true(command[:3] == ["openclaw", "agent", "--session-key"], "adapter should call openclaw agent with session key")
        assert_true(command[3] == "agent:main:child-a", "adapter should target the explicit live-safe child session")
        assert_true("--json" in command, "adapter should request machine-readable output")
        assert_true("--message" in command, "adapter should pass a child prompt")
        prompt = command[command.index("--message") + 1]
        assert_true("REQUEST_JSON" in prompt and "native specialist child session" in prompt, "adapter prompt should carry native child request")
        assert_true(timeout_seconds == request.timeout_policy["child_lease_seconds"], "adapter should use child lease timeout")
        return subprocess.CompletedProcess(command, 0, stdout=_cli_child_stdout(command[3]), stderr="")

    result = OpenClawAgentCliBackend(runner=runner).run_review(request)
    assert_true(result.result.session_key == "agent:main:child-a", "native CLI result should preserve explicit session key")
    assert_true(result.result.tool_smoke_status == "passed", "native CLI result should require passed tool smoke")
    assert_true(result.result.tool_smoke_evidence is not None, "CLI seam should preserve child smoke evidence for coordinator review")
    assert_true(result.result.satisfies_native_agent_panel is False, "experimental CLI seam should not satisfy native agent panel")
    prompt = build_child_prompt(request)
    assert_true("visible messages" in prompt.lower(), "child prompt should restate visible-message restriction")
    assert_true(
        '"session_key": "agent:main:child-a"' in prompt and '"agent_session_ref": "agent:main:child-a"' in prompt,
        "child prompt should provide exact session refs for tool-smoke evidence",
    )
    assert_true(
        "at least one structured finding" in prompt and "p3 informational finding" in prompt,
        "child prompt should make native finding shape explicit",
    )

    missing_smoke = subprocess.CompletedProcess(
        ["openclaw"],
        0,
        stdout='{"response":"{\\"findings\\":[]}"}',
        stderr="",
    )
    seam_result = OpenClawAgentCliBackend(runner=lambda _command, _timeout: missing_smoke).run_review(request)
    assert_true(seam_result.result.status == "failed", "CLI seam should fail child output without tool smoke")
    assert_true(seam_result.result.tool_smoke_status == "not_run", "CLI seam must not default missing tool smoke to passed")
    assert_true(seam_result.result.satisfies_native_agent_panel is False, "missing coordinator smoke must not satisfy native panel")

    wrapped_result = OpenClawAgentCliBackend(
        runner=lambda command, _timeout: subprocess.CompletedProcess(
            command,
            0,
            stdout=_cli_child_stdout(command[3], openclaw_agent_json=True),
            stderr="",
        )
    ).run_review(request)
    assert_true(
        wrapped_result.result.tool_smoke_status == "passed",
        "CLI seam should parse OpenClaw 2026.5.27 result.payloads text",
    )
    expect_error(lambda: validate_openclaw_agent_session_key("session:child:a"), "agent:<id>:<key>")


def assert_openclaw_native_panel_cli_backend_requires_coordinator_verified_smoke() -> None:
    requests = [
        NativeLaunchRequest(
            **{
                **launch_request(profile_ref=f"reviewer-contract-{index}", key_suffix=str(index)).as_dict(),
                "session_key": f"agent:main:child-{index}",
                "request_id": f"request-{index}",
            }
        )
        for index in range(1, 4)
    ]

    def runner(command: list[str], timeout_seconds: int) -> subprocess.CompletedProcess[str]:
        if command[:2] == ["openclaw", "sessions"]:
            if command[2:3] == ["export-trajectory"]:
                return _trajectory_completed_process(command)
            return subprocess.CompletedProcess(command, 0, stdout=_sessions_stdout([request.session_key for request in requests]), stderr="")
        return subprocess.CompletedProcess(command, 0, stdout=_cli_child_stdout(command[3]), stderr="")

    results = OpenClawNativePanelCliBackend(child_backend=OpenClawAgentCliBackend(runner=runner)).run_panel(requests)
    assert_true(len(results) == 3, "native CLI panel should collect every child result")
    assert_true(all(item.satisfies_native_agent_panel for item in results), "coordinator-verified CLI results should satisfy native panel")
    assert_true(
        all(
            item.tool_smoke_evidence
            and item.tool_smoke_evidence["kind"] == "coordinator_verified_child_tool_smoke_session_and_trajectory_binding"
            and item.tool_smoke_evidence["session_store_proof"]["session_key"] == item.session_key
            and item.tool_smoke_evidence["trajectory_proof"]["session_key"] == item.session_key
            and item.tool_smoke_evidence["trajectory_proof"]["tool_call_count"] >= 1
            for item in results
        ),
        "native CLI panel should replace child evidence with coordinator-verified smoke, session, and trajectory proof",
    )

    missing_evidence = '{"response":"{\\"tool_smoke_status\\":\\"passed\\",\\"findings\\":[],\\"error\\":null}"}'
    broken_backend = OpenClawNativePanelCliBackend(
        child_backend=OpenClawAgentCliBackend(
            runner=lambda command, timeout_seconds: subprocess.CompletedProcess(
                command,
                0,
                stdout=(
                    _trajectory_completed_process(command).stdout
                    if command[:3] == ["openclaw", "sessions", "export-trajectory"]
                    else _sessions_stdout([request.session_key for request in requests])
                    if command[:2] == ["openclaw", "sessions"]
                    else missing_evidence
                ),
                stderr="",
            )
        )
    )
    expect_error(lambda: broken_backend.run_panel(requests), "tool_smoke_evidence")

    failed_smoke = OpenClawNativePanelCliBackend(
        child_backend=OpenClawAgentCliBackend(
            runner=lambda command, timeout_seconds: subprocess.CompletedProcess(
                command,
                0,
                stdout=(
                    _trajectory_completed_process(command).stdout
                    if command[:3] == ["openclaw", "sessions", "export-trajectory"]
                    else _sessions_stdout([request.session_key for request in requests])
                    if command[:2] == ["openclaw", "sessions"]
                    else json_dumps_response(
                    {
                        "tool_smoke_status": "failed",
                        "tool_smoke_evidence": {
                            "status": "failed",
                            "kind": "child_file_read_and_status_check",
                            "checked_at": "2026-05-29T00:00:00Z",
                            "session_key": command[3],
                            "agent_session_ref": command[3],
                        },
                        "findings": [],
                        "error": None,
                    }
                    )
                ),
                stderr="",
            )
        )
    )
    expect_error(lambda: failed_smoke.run_panel(requests), "did not complete")

    missing_session_store_proof = OpenClawNativePanelCliBackend(
        child_backend=OpenClawAgentCliBackend(
            runner=lambda command, timeout_seconds: subprocess.CompletedProcess(
                command,
                0,
                stdout=(
                    _trajectory_completed_process(command).stdout
                    if command[:3] == ["openclaw", "sessions", "export-trajectory"]
                    else _sessions_stdout([])
                    if command[:2] == ["openclaw", "sessions"]
                    else _cli_child_stdout(command[3])
                ),
                stderr="",
            )
        )
    )
    expect_error(lambda: missing_session_store_proof.run_panel(requests), "session_key")

    missing_trajectory_proof = OpenClawNativePanelCliBackend(
        child_backend=OpenClawAgentCliBackend(
            runner=lambda command, timeout_seconds: subprocess.CompletedProcess(
                command,
                0 if command[:3] != ["openclaw", "sessions", "export-trajectory"] else 1,
                stdout=(
                    _sessions_stdout([request.session_key for request in requests])
                    if command[:2] == ["openclaw", "sessions"] and command[:3] != ["openclaw", "sessions", "export-trajectory"]
                    else _cli_child_stdout(command[3])
                    if command[:2] != ["openclaw", "sessions"]
                    else ""
                ),
                stderr="trajectory export unavailable",
            )
        )
    )
    expect_error(lambda: missing_trajectory_proof.run_panel(requests), "trajectory")

    wrong_session_tool_events = OpenClawNativePanelCliBackend(
        child_backend=OpenClawAgentCliBackend(
            runner=lambda command, timeout_seconds: subprocess.CompletedProcess(
                command,
                0,
                stdout=(
                    _trajectory_completed_process(command, tool_event_session_key="agent:converge:other").stdout
                    if command[:3] == ["openclaw", "sessions", "export-trajectory"]
                    else _sessions_stdout([request.session_key for request in requests])
                    if command[:2] == ["openclaw", "sessions"]
                    else _cli_child_stdout(command[3])
                ),
                stderr="",
            )
        )
    )
    expect_error(lambda: wrong_session_tool_events.run_panel(requests), "tool.call")


def _sessions_stdout(session_keys: list[str]) -> str:
    return json.dumps(
        {
            "sessions": [
                {
                    "key": session_key,
                    "sessionId": "session-id-" + session_key.rsplit(":", 1)[-1],
                    "updatedAt": 1779981795923,
                    "agentId": "main",
                    "kind": "spawn-child",
                }
                for session_key in session_keys
            ]
        },
        sort_keys=True,
    )


def _trajectory_completed_process(
    command: list[str],
    *,
    include_tool_result: bool = True,
    tool_event_session_key: str | None = None,
) -> subprocess.CompletedProcess[str]:
    session_key = command[command.index("--session-key") + 1]
    tool_session_key = tool_event_session_key or session_key
    output_name = command[command.index("--output") + 1]
    output_dir = Path(tempfile.mkdtemp(prefix=f"{output_name}-"))
    events = [
        {
            "traceSchema": "openclaw-trajectory",
            "schemaVersion": 1,
            "traceId": "trace-contract",
            "source": "transcript",
            "type": "message",
            "sessionKey": session_key,
            "data": {"role": "assistant", "content": "ready"},
        },
        {
            "traceSchema": "openclaw-trajectory",
            "schemaVersion": 1,
            "traceId": "trace-contract",
            "source": "transcript",
            "type": "tool.call",
            "sessionKey": tool_session_key,
            "data": {"toolCallId": "call-1", "name": "exec_command", "arguments": {"cmd": "pwd"}},
        },
    ]
    if include_tool_result:
        events.append(
            {
                "traceSchema": "openclaw-trajectory",
                "schemaVersion": 1,
                "traceId": "trace-contract",
                "source": "transcript",
                "type": "tool.result",
                "sessionKey": tool_session_key,
                "data": {"toolCallId": "call-1", "name": "exec_command"},
            }
        )
    (output_dir / "events.jsonl").write_text("\n".join(json.dumps(event, sort_keys=True) for event in events) + "\n", encoding="utf-8")
    summary = {
        "outputDir": str(output_dir),
        "displayPath": output_name,
        "sessionId": "session-id-" + session_key.rsplit(":", 1)[-1],
        "eventCount": len(events),
        "runtimeEventCount": 0,
        "transcriptEventCount": len(events),
        "files": ["events.jsonl"],
    }
    return subprocess.CompletedProcess(command, 0, stdout=json.dumps(summary, sort_keys=True), stderr="")


def _cli_child_stdout(session_key: str, *, openclaw_agent_json: bool = False) -> str:
    child = {
        "tool_smoke_status": "passed",
        "tool_smoke_evidence": {
            "status": "passed",
            "kind": "child_file_read_and_status_check",
            "checked_at": "2026-05-29T00:00:00Z",
            "session_key": session_key,
            "agent_session_ref": session_key,
        },
        "findings": [{"severity": "p2", "evidence_refs": ["file:a"]}],
        "error": None,
    }
    if openclaw_agent_json:
        text = json.dumps(child, sort_keys=True)
        return json.dumps(
            {
                "runId": "run-shape-smoke",
                "status": "ok",
                "summary": "completed",
                "result": {
                    "payloads": [{"text": text, "mediaUrl": None}],
                    "finalAssistantRawText": text,
                    "finalAssistantVisibleText": text,
                },
            },
            sort_keys=True,
        )
    return json_dumps_response(child)


def json_dumps_response(payload: dict) -> str:
    import json

    return json.dumps({"response": json.dumps(payload, sort_keys=True)}, sort_keys=True)


def main() -> None:
    assert_default_policies_are_enforced()
    assert_openclaw_session_contract_requires_explicit_session_refs()
    assert_native_result_schema_requires_tool_smoke()
    assert_fake_backend_lifecycle_idempotency_and_timeout()
    assert_panel_collection_blocks_partial_failure()
    assert_source_and_fix_runner_contracts()
    assert_risk_intent_classifier_defaults()
    assert_openclaw_cli_backend_uses_explicit_session_and_structured_result()
    assert_openclaw_native_panel_cli_backend_requires_coordinator_verified_smoke()
    print("converge_agent_contracts_smoke: ok")


if __name__ == "__main__":
    main()
