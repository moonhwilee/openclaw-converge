"""Contracts for native agent orchestration.

The in-memory backend in this module is contract-test infrastructure only.  It
must never be used as a runtime substitute for OpenClaw child sessions.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol


NATIVE_BACKEND_OPENCLAW_SESSION = "openclaw_session"

SOURCE_LOCAL_CHECKS = "local_checks"
SOURCE_RUNNER_PROVIDED_PACKET = "runner_provided_packet"
SOURCE_NATIVE_AGENT_PANEL = "native_agent_panel"
SOURCE_CHILD_WORKFLOW = "child_workflow"
SOURCE_FIX_RUNNER = "fix_runner"
SOURCE_PLAN_ONLY = "plan_only"

EXECUTION_SOURCES = {
    SOURCE_LOCAL_CHECKS,
    SOURCE_NATIVE_AGENT_PANEL,
    SOURCE_CHILD_WORKFLOW,
    SOURCE_FIX_RUNNER,
}
ADVISORY_SOURCES = {
    SOURCE_RUNNER_PROVIDED_PACKET,
    SOURCE_PLAN_ONLY,
}

STATUS_LAUNCH_REQUESTED = "launch_requested"
STATUS_ACTIVE = "active"
STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"
STATUS_TIMED_OUT = "timed_out"
STATUS_CANCELLED = "cancelled"

TERMINAL_STATUSES = {STATUS_COMPLETED, STATUS_FAILED, STATUS_TIMED_OUT, STATUS_CANCELLED}

TOOL_SMOKE_PASSED = "passed"
TOOL_SMOKE_FAILED = "failed"
TOOL_SMOKE_NOT_APPLICABLE = "not_applicable"
TOOL_SMOKE_NOT_RUN = "not_run"

DEFAULT_TOOL_POLICY = {
    "policy_id": "native-reviewer-readonly-v1",
    "filesystem": "read_only",
    "shell": "status_only",
    "network": "disabled",
    "visible_messages": "forbidden",
    "workflow_state_mutation": "forbidden",
    "target_mutation": "forbidden",
    "service_restart": "forbidden",
    "external_actions": "forbidden",
    "push_or_pr": "forbidden",
    "release": "forbidden",
    "allowed_actions": ["read_files", "read_artifacts", "shell_status"],
    "native_acceptance_requires_coordinator_tool_smoke": True,
    "runtime_fake_backend_fallback": "forbidden",
}

DEFAULT_TIMEOUT_POLICY = {
    "policy_id": "native-panel-timeout-default-v1",
    "child_lease_seconds": 900,
    "panel_collection_seconds": 1800,
    "on_child_timeout": "terminal_timed_out",
    "on_late_result": "record_late_do_not_overwrite",
}

DEFAULT_BUDGET_POLICY = {
    "policy_id": "native-panel-budget-default-v1",
    "panel_size_default": 3,
    "panel_size_high_risk": 5,
    "max_rounds_default": 1,
    "max_follow_up_rounds_after_material_change": 1,
    "context_mode": "isolated_light",
    "max_input_bytes": 120_000,
    "max_findings_bytes": 24_000,
    "oversized_context": "reject_before_launch",
    "oversized_findings": "truncate_with_metadata",
}


@dataclass(frozen=True)
class NativeLaunchRequest:
    mode: str
    objective: str
    target_refs: list[dict[str, Any]]
    profile_ref: str
    context_hash: str
    idempotency_key: str
    output_schema: dict[str, Any]
    session_key: str
    request_id: str | None = None
    profile_context_refs: list[dict[str, Any]] = field(default_factory=list)
    tool_policy: dict[str, Any] = field(default_factory=lambda: dict(DEFAULT_TOOL_POLICY))
    timeout_policy: dict[str, Any] = field(default_factory=lambda: dict(DEFAULT_TIMEOUT_POLICY))
    budget_policy: dict[str, Any] = field(default_factory=lambda: dict(DEFAULT_BUDGET_POLICY))
    backend: str = NATIVE_BACKEND_OPENCLAW_SESSION
    source_classification: str = SOURCE_NATIVE_AGENT_PANEL

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class NativeChildResult:
    request_id: str
    result_id: str
    agent_session_ref: str
    session_key: str
    tool_smoke_status: str
    profile_ref: str
    context_hash: str
    status: str
    findings: list[dict[str, Any]]
    started_at: str
    deadline_at: str
    completed_at: str | None = None
    error: str | None = None
    timeout_reason: str | None = None
    tool_smoke_evidence: dict[str, Any] | None = None
    source_classification: str = SOURCE_NATIVE_AGENT_PANEL
    satisfies_native_agent_panel: bool = True

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class NativeSessionRecord:
    request: NativeLaunchRequest
    request_id: str
    agent_session_ref: str
    session_key: str
    status: str
    tool_smoke_status: str
    started_at: str
    deadline_at: str
    requested_at: str
    reuse_state: str
    result: NativeChildResult | None = None
    events: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["request"] = self.request.as_dict()
        payload["result"] = self.result.as_dict() if self.result else None
        return payload


@dataclass(frozen=True)
class PanelCollection:
    status: str
    success: bool
    required_count: int
    completed_count: int
    blocked_reason: str | None = None
    degraded: bool = False

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class OpenClawSessionAdapter(Protocol):
    """Interface that Phase C can wire to real OpenClaw sessions_spawn."""

    def launch(self, request: NativeLaunchRequest, *, now: datetime | None = None) -> NativeSessionRecord:
        ...

    def collect_result(
        self,
        request_id: str,
        *,
        findings: list[dict[str, Any]],
        now: datetime | None = None,
        status: str = STATUS_COMPLETED,
        tool_smoke_status: str = TOOL_SMOKE_PASSED,
        error: str | None = None,
    ) -> NativeChildResult:
        ...


class InMemoryOpenClawSessionBackend:
    """In-memory sessions backend for contract tests only."""

    def __init__(self) -> None:
        self.records_by_request_id: dict[str, NativeSessionRecord] = {}
        self.request_id_by_idempotency_key: dict[str, str] = {}
        self.late_results: list[NativeChildResult] = []

    def launch(self, request: NativeLaunchRequest, *, now: datetime | None = None) -> NativeSessionRecord:
        validate_native_launch_request(request.as_dict())
        current = now or _utcnow()
        existing_id = self.request_id_by_idempotency_key.get(request.idempotency_key)
        if existing_id:
            self.expire_timeouts(now=current)
            existing = self.records_by_request_id[existing_id]
            if existing.status == STATUS_ACTIVE:
                return _replace_record(existing, reuse_state="active_reuse")
            if existing.status == STATUS_COMPLETED:
                return _replace_record(existing, reuse_state="completed_reuse")
            return _replace_record(existing, reuse_state=f"{existing.status}_terminal_reuse")

        request_id = request.request_id or stable_hash(
            {
                "idempotency_key": request.idempotency_key,
                "profile_ref": request.profile_ref,
                "session_key": request.session_key,
            }
        )
        deadline = current + timedelta(seconds=int(request.timeout_policy["child_lease_seconds"]))
        record = NativeSessionRecord(
            request=request,
            request_id=request_id,
            agent_session_ref=request.session_key,
            session_key=request.session_key,
            status=STATUS_ACTIVE,
            tool_smoke_status=TOOL_SMOKE_NOT_RUN,
            started_at=_format_time(current),
            deadline_at=_format_time(deadline),
            requested_at=_format_time(current),
            reuse_state="new",
            events=(STATUS_LAUNCH_REQUESTED, STATUS_ACTIVE),
        )
        self.records_by_request_id[request_id] = record
        self.request_id_by_idempotency_key[request.idempotency_key] = request_id
        return record

    def collect_result(
        self,
        request_id: str,
        *,
        findings: list[dict[str, Any]],
        now: datetime | None = None,
        status: str = STATUS_COMPLETED,
        tool_smoke_status: str = TOOL_SMOKE_PASSED,
        error: str | None = None,
    ) -> NativeChildResult:
        record = self._record(request_id)
        current = now or _utcnow()
        if record.status in {STATUS_TIMED_OUT, STATUS_CANCELLED}:
            late = self._build_result(record, findings, current, status, tool_smoke_status, error)
            self.late_results.append(late)
            return late
        if record.status in {STATUS_COMPLETED, STATUS_FAILED} and record.result is not None:
            return record.result
        result = self._build_result(record, findings, current, status, tool_smoke_status, error)
        validate_native_child_result(result.as_dict())
        next_record = _replace_record(
            record,
            status=status,
            tool_smoke_status=tool_smoke_status,
            result=result,
            events=(*record.events, status),
        )
        self.records_by_request_id[request_id] = next_record
        return result

    def cancel(self, request_id: str, *, now: datetime | None = None, reason: str = "cancelled") -> NativeChildResult:
        record = self._record(request_id)
        if record.status in TERMINAL_STATUSES and record.result is not None:
            return record.result
        current = now or _utcnow()
        result = NativeChildResult(
            request_id=request_id,
            result_id=stable_hash({"request_id": request_id, "status": STATUS_CANCELLED}),
            agent_session_ref=record.agent_session_ref,
            session_key=record.session_key,
            tool_smoke_status=TOOL_SMOKE_NOT_RUN,
            profile_ref=record.request.profile_ref,
            context_hash=record.request.context_hash,
            status=STATUS_CANCELLED,
            findings=[],
            started_at=record.started_at,
            deadline_at=record.deadline_at,
            completed_at=_format_time(current),
            error=reason,
        )
        validate_native_child_result(result.as_dict())
        self.records_by_request_id[request_id] = _replace_record(
            record,
            status=STATUS_CANCELLED,
            result=result,
            events=(*record.events, STATUS_CANCELLED),
        )
        return result

    def expire_timeouts(self, *, now: datetime | None = None) -> list[NativeChildResult]:
        current = now or _utcnow()
        expired: list[NativeChildResult] = []
        for request_id, record in list(self.records_by_request_id.items()):
            if record.status != STATUS_ACTIVE or current <= _parse_time(record.deadline_at):
                continue
            result = NativeChildResult(
                request_id=request_id,
                result_id=stable_hash({"request_id": request_id, "status": STATUS_TIMED_OUT}),
                agent_session_ref=record.agent_session_ref,
                session_key=record.session_key,
                tool_smoke_status=record.tool_smoke_status,
                profile_ref=record.request.profile_ref,
                context_hash=record.request.context_hash,
                status=STATUS_TIMED_OUT,
                findings=[],
                started_at=record.started_at,
                deadline_at=record.deadline_at,
                completed_at=_format_time(current),
                timeout_reason="lease_expired",
            )
            validate_native_child_result(result.as_dict())
            self.records_by_request_id[request_id] = _replace_record(
                record,
                status=STATUS_TIMED_OUT,
                result=result,
                events=(*record.events, STATUS_TIMED_OUT),
            )
            expired.append(result)
        return expired

    def collect_panel(self, request_ids: list[str], *, allow_degraded: bool = False) -> PanelCollection:
        records = [self._record(request_id) for request_id in request_ids]
        completed = [
            record
            for record in records
            if record.status == STATUS_COMPLETED
            and record.result is not None
            and record.result.tool_smoke_status == TOOL_SMOKE_PASSED
            and record.result.satisfies_native_agent_panel is True
        ]
        if len(completed) == len(records):
            return PanelCollection("completed", True, len(records), len(completed))
        if allow_degraded and completed:
            return PanelCollection("degraded", True, len(records), len(completed), degraded=True)
        return PanelCollection(
            "blocked",
            False,
            len(records),
            len(completed),
            blocked_reason="partial panel failure blocks success without explicit degraded policy",
        )

    def _build_result(
        self,
        record: NativeSessionRecord,
        findings: list[dict[str, Any]],
        completed_at: datetime,
        status: str,
        tool_smoke_status: str,
        error: str | None,
    ) -> NativeChildResult:
        return NativeChildResult(
            request_id=record.request_id,
            result_id=stable_hash(
                {
                    "request_id": record.request_id,
                    "status": status,
                    "completed_at": _format_time(completed_at),
                    "findings": findings,
                }
            ),
            agent_session_ref=record.agent_session_ref,
            session_key=record.session_key,
            tool_smoke_status=tool_smoke_status,
            profile_ref=record.request.profile_ref,
            context_hash=record.request.context_hash,
            status=status,
            findings=findings,
            started_at=record.started_at,
            deadline_at=record.deadline_at,
            completed_at=_format_time(completed_at),
            error=error,
            tool_smoke_evidence=_tool_smoke_evidence(record) if tool_smoke_status == TOOL_SMOKE_PASSED else None,
        )

    def _record(self, request_id: str) -> NativeSessionRecord:
        try:
            return self.records_by_request_id[request_id]
        except KeyError as exc:
            raise ValueError(f"unknown native request_id: {request_id}") from exc


def classify_execution_source(value: str) -> dict[str, Any]:
    if value in EXECUTION_SOURCES:
        return {
            "execution_source": value,
            "satisfies_native_agent_panel": value == SOURCE_NATIVE_AGENT_PANEL,
            "advisory_only": False,
        }
    if value in ADVISORY_SOURCES:
        return {
            "execution_source": value,
            "satisfies_native_agent_panel": False,
            "advisory_only": True,
        }
    raise ValueError(f"unknown execution source: {value!r}")


def validate_native_launch_request(payload: dict[str, Any]) -> None:
    _required_member(payload, "backend", {NATIVE_BACKEND_OPENCLAW_SESSION})
    _required_member(payload, "mode", {"verify", "conv"})
    _required_member(payload, "source_classification", {SOURCE_NATIVE_AGENT_PANEL})
    for key in ("objective", "profile_ref", "context_hash", "idempotency_key", "session_key"):
        _required_string(payload, key)
    if payload["session_key"] == "current":
        raise ValueError("native launch requires explicit non-current session_key")
    target_refs = payload.get("target_refs")
    if not isinstance(target_refs, list) or not target_refs:
        raise ValueError("native launch requires non-empty target_refs")
    if not isinstance(payload.get("output_schema"), dict) or not payload["output_schema"].get("schema_ref"):
        raise ValueError("native launch requires output_schema.schema_ref")
    validate_tool_policy(payload.get("tool_policy"))
    validate_timeout_policy(payload.get("timeout_policy"))
    validate_budget_policy(payload.get("budget_policy"))


def validate_openclaw_session_payload(payload: dict[str, Any]) -> None:
    validate_native_launch_request(payload)
    for key in ("launch", "wait", "collect", "recover"):
        value = payload.get(key)
        if value in {None, "", "current"}:
            raise ValueError(f"openclaw session payload requires explicit {key} session ref")
        if value != payload["session_key"]:
            raise ValueError(f"openclaw session {key} ref must match session_key")
    if payload.get("tool_smoke_required") is not True:
        raise ValueError("openclaw session payload requires tool_smoke_required=true")


def validate_native_child_result(payload: dict[str, Any]) -> None:
    for key in ("request_id", "result_id", "agent_session_ref", "session_key", "profile_ref", "context_hash", "started_at", "deadline_at"):
        _required_string(payload, key)
    if payload["session_key"] == "current" or payload["agent_session_ref"] == "current":
        raise ValueError("native result must not use implicit current session")
    _required_member(payload, "tool_smoke_status", {TOOL_SMOKE_PASSED, TOOL_SMOKE_FAILED, TOOL_SMOKE_NOT_APPLICABLE, TOOL_SMOKE_NOT_RUN})
    _required_member(payload, "status", {STATUS_COMPLETED, STATUS_FAILED, STATUS_TIMED_OUT, STATUS_CANCELLED})
    classification = classify_execution_source(payload.get("source_classification") or SOURCE_NATIVE_AGENT_PANEL)
    if classification["execution_source"] == SOURCE_NATIVE_AGENT_PANEL:
        if payload["status"] == STATUS_COMPLETED and payload["tool_smoke_status"] != TOOL_SMOKE_PASSED:
            raise ValueError("completed native_agent_panel result requires passed tool_smoke_status")
        if payload["status"] == STATUS_COMPLETED and not isinstance(payload.get("findings"), list):
            raise ValueError("completed native result requires findings array")
        if payload["status"] == STATUS_COMPLETED and payload.get("satisfies_native_agent_panel") is True:
            _validate_tool_smoke_evidence(payload)
    elif classification["advisory_only"] and payload.get("satisfies_native_agent_panel") is True:
        raise ValueError("advisory source cannot satisfy native_agent_panel")
    if payload["status"] == STATUS_TIMED_OUT and not payload.get("timeout_reason"):
        raise ValueError("timed_out native result requires timeout_reason")
    if payload["status"] in {STATUS_FAILED, STATUS_CANCELLED} and not payload.get("error"):
        raise ValueError("failed/cancelled native result requires error")


def validate_tool_policy(policy: Any) -> None:
    if not isinstance(policy, dict):
        raise ValueError("tool_policy must be an object")
    for key in (
        "policy_id",
        "filesystem",
        "shell",
        "network",
        "visible_messages",
        "workflow_state_mutation",
        "target_mutation",
        "service_restart",
        "external_actions",
        "push_or_pr",
        "release",
    ):
        _required_string(policy, key)
    if policy["filesystem"] != "read_only":
        raise ValueError("reviewer tool_policy filesystem must be read_only")
    if policy["shell"] not in {"disabled", "status_only"}:
        raise ValueError("reviewer tool_policy shell must be disabled or status_only")
    for key in ("visible_messages", "workflow_state_mutation", "target_mutation", "service_restart", "external_actions", "push_or_pr", "release"):
        if policy[key] != "forbidden":
            raise ValueError(f"reviewer tool_policy {key} must be forbidden")
    if policy["network"] not in {"disabled", "forbidden"}:
        raise ValueError("reviewer tool_policy network must be disabled or forbidden")
    allowed = policy.get("allowed_actions")
    if not isinstance(allowed, list) or any(not isinstance(item, str) or not item for item in allowed):
        raise ValueError("tool_policy allowed_actions must be a string array")


def validate_timeout_policy(policy: Any) -> None:
    if not isinstance(policy, dict):
        raise ValueError("timeout_policy must be an object")
    for key in ("policy_id", "on_child_timeout", "on_late_result"):
        _required_string(policy, key)
    for key in ("child_lease_seconds", "panel_collection_seconds"):
        value = policy.get(key)
        if not isinstance(value, int) or value <= 0:
            raise ValueError(f"timeout_policy {key} must be a positive integer")
    if policy["panel_collection_seconds"] < policy["child_lease_seconds"]:
        raise ValueError("panel_collection_seconds must be >= child_lease_seconds")
    if policy["on_child_timeout"] != "terminal_timed_out":
        raise ValueError("timeout_policy on_child_timeout must be terminal_timed_out")
    if policy["on_late_result"] != "record_late_do_not_overwrite":
        raise ValueError("timeout_policy on_late_result must record late results without overwrite")


def validate_budget_policy(policy: Any) -> None:
    if not isinstance(policy, dict):
        raise ValueError("budget_policy must be an object")
    for key in ("policy_id", "context_mode", "oversized_context", "oversized_findings"):
        _required_string(policy, key)
    for key in ("panel_size_default", "panel_size_high_risk", "max_rounds_default", "max_follow_up_rounds_after_material_change", "max_input_bytes", "max_findings_bytes"):
        value = policy.get(key)
        if not isinstance(value, int) or value <= 0:
            raise ValueError(f"budget_policy {key} must be a positive integer")
    if policy["panel_size_default"] != 3:
        raise ValueError("default native panel size must be 3")
    if policy["panel_size_high_risk"] != 5:
        raise ValueError("high-risk native panel size must be 5")
    if policy["max_rounds_default"] != 1:
        raise ValueError("default max rounds must be 1")
    if policy["max_follow_up_rounds_after_material_change"] != 1:
        raise ValueError("material change follow-up budget must be 1")
    if policy["context_mode"] != "isolated_light":
        raise ValueError("default context mode must be isolated_light")


def validate_fix_runner_request(payload: dict[str, Any]) -> None:
    if not isinstance(payload, dict):
        raise ValueError("fix_runner request must be an object")
    for key in ("runner_id", "mode", "objective", "source_classification"):
        _required_string(payload, key)
    if payload["source_classification"] != SOURCE_FIX_RUNNER:
        raise ValueError("fix_runner request must use fix_runner source classification")
    if payload.get("agent_session_ref"):
        raise ValueError("fix_runner is coordinator-owned and must not run in reviewer agent sessions")
    changes = payload.get("accepted_change_refs")
    if not isinstance(changes, list) or not changes:
        raise ValueError("fix_runner request requires accepted_change_refs")
    _validate_fix_runner_change_refs(changes, field_name="accepted_change_refs", require_local_file_edits=True)
    policy = payload.get("tool_policy")
    validate_fix_runner_tool_policy(policy)


def validate_fix_runner_result(payload: dict[str, Any]) -> None:
    if not isinstance(payload, dict):
        raise ValueError("fix_runner result must be an object")
    for key in ("result_id", "runner_id", "mode", "workflow_id", "source_root", "source_classification", "status", "idempotency_key"):
        _required_string(payload, key)
    if payload["source_classification"] != SOURCE_FIX_RUNNER:
        raise ValueError("fix_runner result must use fix_runner source classification")
    if payload.get("agent_session_ref"):
        raise ValueError("fix_runner result is coordinator-owned and must not use reviewer agent sessions")
    if payload["status"] != STATUS_COMPLETED:
        raise ValueError("fix_runner result must be completed before follow-up validation")
    for key in ("accepted_change_refs", "applied_change_refs", "focused_check_results", "artifact_refs", "file_mutations"):
        value = payload.get(key)
        if not isinstance(value, list) or not value:
            raise ValueError(f"fix_runner result requires non-empty {key}")
    accepted_ids = _validate_fix_runner_change_refs(payload["accepted_change_refs"], field_name="accepted_change_refs", require_local_file_edits=True)
    applied_ids = _validate_fix_runner_change_refs(payload["applied_change_refs"], field_name="applied_change_refs", require_local_file_edits=True)
    if applied_ids != accepted_ids:
        raise ValueError("fix_runner result applied_change_refs must match accepted_change_refs")
    checks_by_change: dict[str, dict[str, Any]] = {}
    for check in payload["focused_check_results"]:
        if (
            not isinstance(check, dict)
            or check.get("status") != "pass"
            or not isinstance(check.get("check_id"), str)
            or not check.get("check_id")
            or check.get("change_ref") not in accepted_ids
        ):
            raise ValueError("fix_runner focused checks must be structured pass results")
        if check.get("kind") != "bounded_local_file_edit":
            raise ValueError("fix_runner focused checks must bind bounded local file edit proof")
        if check["change_ref"] in checks_by_change:
            raise ValueError("fix_runner focused checks must cover every accepted change exactly once")
        checks_by_change[check["change_ref"]] = check
    if sorted(checks_by_change) != sorted(accepted_ids):
        raise ValueError("fix_runner focused checks must cover every accepted change exactly once")
    if payload["artifact_refs"] != [payload["result_id"]]:
        raise ValueError("fix_runner result artifact_refs must bind exactly to result_id")
    if payload.get("material_change_applied") is not True:
        raise ValueError("fix_runner result material_change_applied must be true")
    mutation_ids: list[str] = []
    mutations_by_change: dict[str, list[dict[str, Any]]] = {}
    for mutation in payload["file_mutations"]:
        if not isinstance(mutation, dict):
            raise ValueError("fix_runner file_mutations entries must be objects")
        for key in ("change_ref", "path", "before_sha256", "after_sha256"):
            _required_string(mutation, key)
        if mutation["change_ref"] not in accepted_ids:
            raise ValueError("fix_runner file_mutations must bind to accepted changes")
        if mutation["path"].startswith("/") or ".." in mutation["path"].split("/"):
            raise ValueError("fix_runner file_mutations path must be safe relative")
        for key in ("before_sha256", "after_sha256"):
            if len(mutation[key]) != 64 or any(ch not in "0123456789abcdef" for ch in mutation[key]):
                raise ValueError("fix_runner file_mutations hashes must be lowercase sha256")
        if mutation["before_sha256"] == mutation["after_sha256"]:
            raise ValueError("fix_runner file_mutations must change file hashes")
        mutation_ids.append(mutation["change_ref"])
        mutations_by_change.setdefault(mutation["change_ref"], []).append(mutation)
    if sorted(set(mutation_ids)) != sorted(accepted_ids):
        raise ValueError("fix_runner file_mutations must cover every accepted change")
    for change_ref, check in checks_by_change.items():
        mutations = mutations_by_change.get(change_ref) or []
        expected_hashes = [
            {
                "path": item["path"],
                "before_sha256": item["before_sha256"],
                "after_sha256": item["after_sha256"],
            }
            for item in mutations
        ]
        if check.get("mutation_count") != len(mutations):
            raise ValueError("fix_runner focused checks must bind mutation_count to file_mutations")
        if check.get("mutation_paths") != [item["path"] for item in mutations]:
            raise ValueError("fix_runner focused checks must bind mutation_paths to file_mutations")
        if check.get("mutation_hashes") != expected_hashes:
            raise ValueError("fix_runner focused checks must bind mutation hashes to file_mutations")
    expected_idempotency_key = stable_hash(
        {
            "runner_id": payload["runner_id"],
            "workflow_id": payload["workflow_id"],
            "source_root": payload["source_root"],
            "accepted_change_refs": payload["accepted_change_refs"],
            "file_mutations": payload["file_mutations"],
        }
    )
    if payload["idempotency_key"] != expected_idempotency_key:
        raise ValueError("fix_runner result idempotency_key must match bounded mutation proof")
    side_effects = payload.get("side_effects_performed") or []
    if not isinstance(side_effects, list) or side_effects:
        raise ValueError("fix_runner result must not report external side effects")
    for key in ("gateway_restart_performed", "deploy_or_install_performed", "pr_opened", "release_performed"):
        if payload.get(key):
            raise ValueError("fix_runner result must not report forbidden side effects")
    validate_fix_runner_tool_policy(payload.get("tool_policy"))


def validate_fix_runner_tool_policy(policy: Any) -> None:
    if not isinstance(policy, dict):
        raise ValueError("fix_runner requires tool_policy")
    for key in ("policy_id", "filesystem", "target_mutation", "visible_messages", "workflow_state_mutation", "service_restart", "external_actions", "push_or_pr", "release"):
        _required_string(policy, key)
    if policy["target_mutation"] != "bounded_by_accepted_change_refs":
        raise ValueError("fix_runner tool_policy must explicitly authorize bounded target mutation")
    for key in ("visible_messages", "workflow_state_mutation", "service_restart", "external_actions", "push_or_pr", "release"):
        if policy[key] != "forbidden":
            raise ValueError(f"fix_runner tool_policy {key} must be forbidden")
    if policy["filesystem"] not in {"workspace_write", "target_write"}:
        raise ValueError("fix_runner filesystem scope must be explicitly writable")
    if policy.get("allowed_actions") != ["apply_accepted_changes", "run_focused_checks"]:
        raise ValueError("fix_runner allowed_actions must be bounded to accepted changes and checks")


def _validate_fix_runner_change_refs(items: Any, *, field_name: str, require_local_file_edits: bool = False) -> list[str]:
    if not isinstance(items, list) or not items:
        raise ValueError(f"fix_runner {field_name} must be a non-empty array")
    result: list[str] = []
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get("change_ref"), str) or not item["change_ref"]:
            raise ValueError(f"fix_runner {field_name} entries require non-empty change_ref")
        if require_local_file_edits:
            edits = item.get("local_file_edits")
            if not isinstance(edits, list) or not edits:
                raise ValueError(f"fix_runner {field_name} entries require bounded local_file_edits")
        result.append(item["change_ref"])
    if len(set(result)) != len(result):
        raise ValueError(f"fix_runner {field_name} entries must not duplicate change_ref")
    return result


def build_runner_packet_request_ref(
    *,
    request_id: str,
    profile_ref: str,
    context_hash: str,
    expected_result_count: int,
    result_ids: list[str],
) -> dict[str, Any]:
    return {
        "request_id": request_id,
        "profile_ref": profile_ref,
        "context_hash": context_hash,
        "status": STATUS_COMPLETED,
        "expected_result_count": expected_result_count,
        "result_ids": result_ids,
        "session_key": None,
        "agent_session_ref": None,
        "tool_smoke_status": TOOL_SMOKE_NOT_APPLICABLE,
        **classify_execution_source(SOURCE_RUNNER_PROVIDED_PACKET),
    }


def build_runner_packet_result_ref(
    *,
    result_id: str,
    request_id: str,
    profile_ref: str,
    context_hash: str,
    finding_id: str,
    source_provenance: str,
    evidence_refs: list[str],
) -> dict[str, Any]:
    return {
        "result_id": result_id,
        "request_id": request_id,
        "profile_ref": profile_ref,
        "context_hash": context_hash,
        "idempotency_key": stable_hash(
            {
                "finding_id": finding_id,
                "profile_ref": profile_ref,
                "request_id": request_id,
                "source_provenance": source_provenance,
            }
        ),
        "status": STATUS_COMPLETED,
        "evidence_refs": evidence_refs,
        "session_key": None,
        "agent_session_ref": None,
        "tool_smoke_status": TOOL_SMOKE_NOT_APPLICABLE,
        **classify_execution_source(SOURCE_RUNNER_PROVIDED_PACKET),
    }


def stable_hash(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _tool_smoke_evidence(record: NativeSessionRecord) -> dict[str, Any]:
    return {
        "status": TOOL_SMOKE_PASSED,
        "session_key": record.session_key,
        "agent_session_ref": record.agent_session_ref,
        "kind": "coordinator_verified_fixture",
        "checked_at": record.started_at,
    }


def _validate_tool_smoke_evidence(payload: dict[str, Any]) -> None:
    evidence = payload.get("tool_smoke_evidence")
    if not isinstance(evidence, dict):
        raise ValueError("completed native_agent_panel result requires tool_smoke_evidence")
    if evidence.get("status") != TOOL_SMOKE_PASSED:
        raise ValueError("native tool_smoke_evidence status must be passed")
    if evidence.get("session_key") != payload["session_key"]:
        raise ValueError("native tool_smoke_evidence session_key must match result")
    if evidence.get("agent_session_ref") != payload["agent_session_ref"]:
        raise ValueError("native tool_smoke_evidence agent_session_ref must match result")
    if not isinstance(evidence.get("kind"), str) or not evidence["kind"]:
        raise ValueError("native tool_smoke_evidence kind must be a non-empty string")
    if not isinstance(evidence.get("checked_at"), str) or not evidence["checked_at"]:
        raise ValueError("native tool_smoke_evidence checked_at must be a non-empty string")


def _replace_record(record: NativeSessionRecord, **changes: Any) -> NativeSessionRecord:
    payload = {
        "request": record.request,
        "request_id": record.request_id,
        "agent_session_ref": record.agent_session_ref,
        "session_key": record.session_key,
        "status": record.status,
        "tool_smoke_status": record.tool_smoke_status,
        "started_at": record.started_at,
        "deadline_at": record.deadline_at,
        "requested_at": record.requested_at,
        "reuse_state": record.reuse_state,
        "result": record.result,
        "events": record.events,
    }
    payload.update(changes)
    return NativeSessionRecord(**payload)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _format_time(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _required_string(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{key} must be a non-empty string")
    return value


def _required_member(payload: dict[str, Any], key: str, allowed: set[str]) -> str:
    value = _required_string(payload, key)
    if value not in allowed:
        raise ValueError(f"{key} must be one of {sorted(allowed)!r}")
    return value
