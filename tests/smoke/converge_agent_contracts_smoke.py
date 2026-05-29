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
    NATIVE_INLINE_TARGET_MAX_BYTES,
    NATIVE_INLINE_TARGET_MAX_LINES,
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
    NativePanelBlockedError,
    OPENCLAW_AGENT_COMMAND_TIMEOUT_CAP_SECONDS,
    OpenClawAgentCliBackend,
    OpenClawNativePanelCliBackend,
    SUBAGENT_SPAWN_TIMEOUT,
    build_child_prompt,
    validate_openclaw_agent_session_key,
)
from converge.target_refs import default_converge_target_refs, load_target_refs_file, merge_inline_target_ref  # noqa: E402


def expect_error(func, contains: str) -> None:
    try:
        func()
    except ValueError as exc:
        assert_true(contains in str(exc), f"expected error containing {contains!r}, got {exc!s}")
        return
    raise AssertionError(f"expected ValueError containing {contains!r}")


def expect_native_panel_blocked(func, contains: str) -> NativePanelBlockedError:
    try:
        func()
    except NativePanelBlockedError as exc:
        assert_true(contains in str(exc), f"expected blocked error containing {contains!r}, got {exc!s}")
        return exc
    raise AssertionError(f"expected NativePanelBlockedError containing {contains!r}")


def launch_request(profile_ref: str = "reviewer-contract", key_suffix: str = "a") -> NativeLaunchRequest:
    source_root = str(Path.cwd().resolve())
    return NativeLaunchRequest(
        mode="verify",
        objective="Audit the native adapter boundary",
        target_refs=[
            {"kind": "verify_target", "text": "Audit the native adapter boundary", "source_root": source_root},
            {"kind": "file", "path": "converge/agents/contracts.py", "source_root": source_root},
        ],
        profile_ref=profile_ref,
        context_hash="sha256:context",
        idempotency_key=f"sha256:task-context-profile-{key_suffix}",
        output_schema={"schema_ref": "native_agent_result.v1"},
        session_key=f"session:child:{key_suffix}",
        profile_context_refs=[{"kind": "profile", "id": profile_ref}],
    )


def structured_finding(
    finding_id: str = "inspection-passed",
    *,
    evidence: str = "agent_session_ref:session:child:contract",
    severity: str = "p3",
    confidence: float = 0.9,
) -> dict[str, object]:
    return {
        "finding_id": finding_id,
        "finding": "Read-only native inspection completed.",
        "severity": severity,
        "confidence": confidence,
        "evidence": evidence,
        "why_it_matters": "Native panel parity requires concrete specialist output.",
        "minimal_fix_or_test": "Keep native child result validation bound to this schema.",
        "scope_risk": "native-child-result-contract",
        "failure_mode": "none_observed",
    }


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

    missing_inline_target = {**payload, "target_refs": [{"kind": "file", "path": "converge/agents/contracts.py", "source_root": str(Path.cwd().resolve())}]}
    expect_error(lambda: validate_native_launch_request(missing_inline_target), "must start with verify_target")

    duplicate_inline_target = {
        **payload,
        "target_refs": [
            payload["target_refs"][0],
            {"kind": "conv_target", "text": "bad", "source_root": str(Path.cwd().resolve())},
        ],
    }
    expect_error(lambda: validate_native_launch_request(duplicate_inline_target), "only one inline")

    escaped_file_ref = {
        **payload,
        "target_refs": [
            payload["target_refs"][0],
            {"kind": "file", "path": "../contracts.py", "source_root": str(Path.cwd().resolve())},
        ],
    }
    expect_error(lambda: validate_native_launch_request(escaped_file_ref), "relative")

    oversized_inline_target = {
        **payload,
        "target_refs": [
            {**payload["target_refs"][0], "text": "x" * (NATIVE_INLINE_TARGET_MAX_BYTES + 1)},
            payload["target_refs"][1],
        ],
    }
    expect_error(lambda: validate_native_launch_request(oversized_inline_target), "too large")

    multiline_inline_target = {
        **payload,
        "target_refs": [
            {**payload["target_refs"][0], "text": "\n".join(["line"] * (NATIVE_INLINE_TARGET_MAX_LINES + 1))},
            payload["target_refs"][1],
        ],
    }
    expect_error(lambda: validate_native_launch_request(multiline_inline_target), "too many lines")

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
        findings=[structured_finding()],
        started_at="2026-05-28T00:00:00Z",
        deadline_at="2026-05-28T00:15:00Z",
        completed_at="2026-05-28T00:01:00Z",
        tool_smoke_evidence={
            "status": "passed",
            "session_key": "session:child:contract",
            "agent_session_ref": "session:child:contract",
            "kind": "coordinator_verified_fixture",
            "checked_at": "2026-05-28T00:01:00Z",
            "read_action": "read_files",
            "status_action": "shell_status",
        },
    ).as_dict()
    validate_native_child_result(result)

    no_smoke = {**result, "tool_smoke_status": "not_run"}
    expect_error(lambda: validate_native_child_result(no_smoke), "passed tool_smoke_status")

    no_smoke_evidence = {**result, "tool_smoke_evidence": None}
    expect_error(lambda: validate_native_child_result(no_smoke_evidence), "tool_smoke_evidence")

    arbitrary_kind_without_read_status = {
        **result,
        "tool_smoke_evidence": {
            key: value for key, value in result["tool_smoke_evidence"].items() if key not in {"read_action", "status_action"}
        },
    }
    expect_error(lambda: validate_native_child_result(arbitrary_kind_without_read_status), "read_action")

    coordinator_without_child_read_action = {
        **result,
        "tool_smoke_evidence": {
            **result["tool_smoke_evidence"],
            "kind": "coordinator_verified_child_tool_smoke_session_and_trajectory_binding",
        },
    }
    expect_error(lambda: validate_native_child_result(coordinator_without_child_read_action), "child_read_action")

    empty_findings = {**result, "findings": []}
    expect_error(lambda: validate_native_child_result(empty_findings), "non-empty findings")

    missing_required_field = {**result, "findings": [{key: value for key, value in structured_finding().items() if key != "why_it_matters"}]}
    expect_error(lambda: validate_native_child_result(missing_required_field), "why_it_matters")

    string_confidence = {**result, "findings": [{**structured_finding(), "confidence": "high"}]}
    expect_error(lambda: validate_native_child_result(string_confidence), "confidence")

    evidence_array = {**result, "findings": [{**structured_finding(), "evidence": ["file:a"]}]}
    expect_error(lambda: validate_native_child_result(evidence_array), "evidence")

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


def assert_target_refs_manifest_validation() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        good_file = root / "converge" / "modes" / "verify.py"
        good_file.parent.mkdir(parents=True)
        good_file.write_text("print('target')\n", encoding="utf-8")
        manifest = root / "target-refs.json"

        def write_refs(refs: list[dict[str, object]]) -> None:
            manifest.write_text(json.dumps({"schema_version": 1, "target_refs": refs}), encoding="utf-8")

        write_refs([{"kind": "file", "path": "converge/modes/verify.py", "role": "mode"}])
        refs = load_target_refs_file(manifest, source_root=root)
        assert_true(
            refs == [{"kind": "file", "path": "converge/modes/verify.py", "source_root": str(root.resolve()), "role": "mode"}],
            "target refs manifest should normalize valid relative file refs",
        )
        merged = merge_inline_target_ref("verify", "Audit target refs", refs, source_root=root)
        assert_true(merged[0] == {"kind": "verify_target", "text": "Audit target refs", "source_root": str(root.resolve())}, "inline target ref should be first and source-rooted")
        assert_true(merged[1] == refs[0], "manifest file ref should remain source-rooted after merge")
        expect_error(lambda: merge_inline_target_ref("goal", "Audit target refs", refs, source_root=root), "verify or conv")
        expect_error(lambda: merge_inline_target_ref("verify", "x" * (NATIVE_INLINE_TARGET_MAX_BYTES + 1), refs, source_root=root), "too large")
        expect_error(lambda: merge_inline_target_ref("verify", "\n".join(["line"] * (NATIVE_INLINE_TARGET_MAX_LINES + 1)), refs, source_root=root), "too many lines")
        expect_error(
            lambda: merge_inline_target_ref("verify", "Audit target refs", [{"kind": "conv_target", "text": "bad"}], source_root=root),
            "must not contain inline",
        )

        write_refs([{"kind": "artifact", "path": "converge/modes/verify.py"}])
        expect_error(lambda: load_target_refs_file(manifest, source_root=root), "only kind=file")

        write_refs([{"kind": "file", "path": "/tmp/verify.py"}])
        expect_error(lambda: load_target_refs_file(manifest, source_root=root), "relative")

        write_refs([{"kind": "file", "path": "../verify.py"}])
        expect_error(lambda: load_target_refs_file(manifest, source_root=root), "relative")

        write_refs([{"kind": "file", "path": "missing.py"}])
        expect_error(lambda: load_target_refs_file(manifest, source_root=root), "does not exist")


def assert_default_converge_target_refs_are_concrete_and_bounded() -> None:
    root = Path.cwd().resolve()
    conv_refs = default_converge_target_refs("conv", source_root=root)
    verify_refs = default_converge_target_refs("verify", source_root=root)
    assert_true(conv_refs, "broad native conv should receive concrete default file refs")
    assert_true(verify_refs, "broad native verify should receive concrete default file refs")
    assert_true(
        all(item["kind"] == "file" and item["source_root"] == str(root) for item in conv_refs + verify_refs),
        "default Converge refs should be concrete file refs bound to source_root",
    )
    assert_true(
        {"converge/modes/conv.py", "converge/agents/openclaw_cli.py", "converge/target_refs.py"}.issubset(
            {item["path"] for item in conv_refs}
        ),
        "default conv refs should cover mode, native launch, and target ref contracts",
    )
    assert_true(
        {"converge/modes/verify.py", "converge/modes/specialist_panel.py", "converge/target_refs.py"}.issubset(
            {item["path"] for item in verify_refs}
        ),
        "default verify refs should cover mode, native panel, and target ref contracts",
    )


def assert_fake_backend_lifecycle_idempotency_and_timeout() -> None:
    backend = InMemoryOpenClawSessionBackend()
    now = datetime(2026, 5, 28, 0, 0, tzinfo=timezone.utc)
    first = backend.launch(launch_request(), now=now)
    assert_true(first.status == "active", "new launch should become active")
    assert_true(first.events == ("launch_requested", "active"), "launch should record requested then active lifecycle")

    active_reuse = backend.launch(launch_request(), now=now + timedelta(seconds=1))
    assert_true(active_reuse.request_id == first.request_id, "matching active idempotency key should reuse request")
    assert_true(active_reuse.reuse_state == "active_reuse", "active idempotency key should attach to lease")

    completed = backend.collect_result(first.request_id, findings=[structured_finding("fake-completed")], now=now + timedelta(seconds=2))
    assert_true(completed.status == STATUS_COMPLETED, "completed fake child should report completed")
    completed_reuse = backend.launch(launch_request(), now=now + timedelta(seconds=3))
    assert_true(completed_reuse.reuse_state == "completed_reuse", "completed idempotency key should be reused")
    assert_true(completed_reuse.result == completed, "completed reuse should preserve original result")

    timeout_request = launch_request(profile_ref="reviewer-timeout", key_suffix="timeout")
    timeout_record = backend.launch(timeout_request, now=now)
    expired = backend.expire_timeouts(now=now + timedelta(seconds=901))
    assert_true(len(expired) == 1 and expired[0].status == STATUS_TIMED_OUT, "active lease should expire as terminal timeout")
    late = backend.collect_result(timeout_record.request_id, findings=[structured_finding("fake-late")], now=now + timedelta(seconds=902))
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
    backend.collect_result(records[0].request_id, findings=[structured_finding("panel-1")], now=now + timedelta(seconds=1))
    backend.collect_result(records[1].request_id, findings=[structured_finding("panel-2")], now=now + timedelta(seconds=1))
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
        assert_true(
            timeout_seconds == OPENCLAW_AGENT_COMMAND_TIMEOUT_CAP_SECONDS,
            "adapter should cap default openclaw agent command waits",
        )
        assert_true(
            command[command.index("--timeout") + 1] == str(OPENCLAW_AGENT_COMMAND_TIMEOUT_CAP_SECONDS),
            "adapter should pass the bounded command wait to openclaw agent",
        )
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
    assert_true(
        "not whether your review found risks" in prompt,
        "child prompt should distinguish tool smoke from review verdict",
    )
    assert_true(
        "If tool_smoke_status is passed, error must be null or omitted" in prompt
        and "Avoid shell variables named status" in prompt,
        "child prompt should prevent recovered smoke attempts from being reported as terminal errors",
    )
    assert_true(
        "confidence must be a JSON number" in prompt,
        "child prompt should require schema-compatible numeric confidence",
    )
    assert_true(
        "evidence must be one non-empty string" in prompt and '"evidence":"agent_session_ref:agent:main:converge-example"' in prompt,
        "child prompt should include a schema-compatible finding example",
    )
    assert_true(
        "read_action must be read_files or read_artifacts" in prompt and "status_action must be shell_status" in prompt,
        "child prompt should require structured read/status smoke proof",
    )

    missing_smoke = subprocess.CompletedProcess(
        ["openclaw"],
        0,
        stdout=json_dumps_response({"findings": [structured_finding("missing-smoke")]}),
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

    short_timeout_request = NativeLaunchRequest(
        **{
            **launch_request().as_dict(),
            "session_key": "agent:main:child-short-timeout",
            "timeout_policy": {
                **DEFAULT_TIMEOUT_POLICY,
                "child_lease_seconds": 42,
                "panel_collection_seconds": DEFAULT_TIMEOUT_POLICY["panel_collection_seconds"],
            },
        }
    )

    def short_timeout_runner(command: list[str], timeout_seconds: int) -> subprocess.CompletedProcess[str]:
        assert_true(timeout_seconds == 42, "adapter should not raise short leases to the command cap")
        assert_true(command[command.index("--timeout") + 1] == "42", "adapter should pass short leases unchanged")
        return subprocess.CompletedProcess(command, 0, stdout=_cli_child_stdout(command[3]), stderr="")

    OpenClawAgentCliBackend(runner=short_timeout_runner).run_review(short_timeout_request)

    def timeout_runner(command: list[str], timeout_seconds: int) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(command, timeout_seconds, output="waiting for subagent", stderr="sessions are full")

    blocked = expect_native_panel_blocked(
        lambda: OpenClawAgentCliBackend(runner=timeout_runner).run_review(request),
        "timed out",
    )
    assert_true(blocked.reason == SUBAGENT_SPAWN_TIMEOUT, "command timeout should block as a retryable spawn timeout")


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
            and item.tool_smoke_evidence["read_action"] == "read_files"
            and item.tool_smoke_evidence["status_action"] == "shell_status"
            and item.tool_smoke_evidence["child_read_action"] == "read_files"
            and item.tool_smoke_evidence["child_status_action"] == "shell_status"
            and item.tool_smoke_evidence["session_store_proof"]["session_key"] == item.session_key
            and item.tool_smoke_evidence["trajectory_proof"]["session_key"] == item.session_key
            and item.tool_smoke_evidence["trajectory_proof"]["tool_call_count"] >= 1
            for item in results
        ),
        "native CLI panel should replace child evidence with coordinator-verified smoke, session, and trajectory proof",
    )

    missing_evidence = json_dumps_response(
        {"tool_smoke_status": "passed", "findings": [structured_finding("missing-evidence")], "error": None}
    )
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
    blocked = expect_native_panel_blocked(lambda: broken_backend.run_panel(requests), "tool_smoke_evidence")
    assert_true(blocked.reason == "subagent_proof_failed", "missing child evidence should be structured blocked")

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
                            "read_action": "read_files",
                            "status_action": "shell_status",
                        },
                        "findings": [structured_finding("failed-smoke")],
                        "error": None,
                    }
                    )
                ),
                stderr="",
            )
        )
    )
    blocked = expect_native_panel_blocked(lambda: failed_smoke.run_panel(requests), "did not complete")
    assert_true(blocked.reason == "subagent_proof_failed", "failed smoke should be recorded as structured proof blockage")

    missing_structured_smoke_action = OpenClawNativePanelCliBackend(
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
                            "tool_smoke_status": "passed",
                            "tool_smoke_evidence": {
                                "status": "passed",
                                "kind": "child_file_read_and_status_check",
                                "checked_at": "2026-05-29T00:00:00Z",
                                "session_key": command[3],
                                "agent_session_ref": command[3],
                            },
                            "findings": [structured_finding("missing-structured-smoke-action")],
                            "error": None,
                        }
                    )
                ),
                stderr="",
            )
        )
    )
    blocked = expect_native_panel_blocked(lambda: missing_structured_smoke_action.run_panel(requests), "read_action")
    assert_true(blocked.reason == "subagent_proof_failed", "unstructured child smoke should be recorded as proof blockage")

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
    blocked = expect_native_panel_blocked(lambda: missing_session_store_proof.run_panel(requests), "session_key")
    assert_true(blocked.reason == "subagent_proof_failed", "missing session proof should be structured blocked")

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
    blocked = expect_native_panel_blocked(lambda: missing_trajectory_proof.run_panel(requests), "trajectory")
    assert_true(blocked.reason == "subagent_proof_failed", "missing trajectory proof should be structured blocked")

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
    blocked = expect_native_panel_blocked(lambda: wrong_session_tool_events.run_panel(requests), "tool.call")
    assert_true(blocked.reason == "subagent_proof_failed", "wrong-session trajectory proof should be structured blocked")


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
            "read_action": "read_files",
            "status_action": "shell_status",
        },
        "findings": [
            structured_finding(
                "cli-finding-" + session_key.rsplit(":", 1)[-1],
                evidence=f"agent_session_ref:{session_key}",
            )
        ],
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
    assert_target_refs_manifest_validation()
    assert_default_converge_target_refs_are_concrete_and_bounded()
    assert_fake_backend_lifecycle_idempotency_and_timeout()
    assert_panel_collection_blocks_partial_failure()
    assert_source_and_fix_runner_contracts()
    assert_risk_intent_classifier_defaults()
    assert_openclaw_cli_backend_uses_explicit_session_and_structured_result()
    assert_openclaw_native_panel_cli_backend_requires_coordinator_verified_smoke()
    print("converge_agent_contracts_smoke: ok")


if __name__ == "__main__":
    main()
