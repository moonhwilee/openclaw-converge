"""OpenClaw CLI-backed native session adapter.

This is an experimental Phase C command-shape seam.  It uses explicit OpenClaw
session keys and a command runner seam so tests can prove command shape and
result parsing without spawning live sessions.  It must not satisfy
native_agent_panel parity until coordinator-verified tool-smoke evidence exists.
"""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .contracts import (
    NativeChildResult,
    NativeLaunchRequest,
    STATUS_COMPLETED,
    STATUS_FAILED,
    TOOL_SMOKE_PASSED,
    validate_native_child_result,
    validate_native_launch_request,
)


CommandRunner = Callable[[list[str], int], subprocess.CompletedProcess[str]]

SUBAGENT_CAPACITY_EXHAUSTED = "subagent_capacity_exhausted"
SUBAGENT_SPAWN_TIMEOUT = "subagent_spawn_timeout"
SUBAGENT_CLI_CONTRACT_CHANGED = "subagent_cli_contract_changed"
SUBAGENT_SPAWN_FAILED = "subagent_spawn_failed"
SUBAGENT_PROOF_FAILED = "subagent_proof_failed"
OPENCLAW_AGENT_COMMAND_TIMEOUT_CAP_SECONDS = 300
READ_ACTION_TOOL_NAMES = frozenset(
    {
        "bash",
        "exec_command",
        "functions.exec_command",
        "read",
        "view_image",
        "functions.view_image",
    }
)
STATUS_ACTION_TOOL_NAMES = frozenset({"bash", "exec_command", "functions.exec_command"})
MAX_TRAJECTORY_TOOL_CALL_REFS = 80
MAX_TRAJECTORY_ARGUMENT_TEXT_BYTES = 4_000


@dataclass(frozen=True)
class OpenClawToolCallRef:
    tool_call_id: str
    name: str
    cwd: str | None
    argument_text: str

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "tool_call_id": self.tool_call_id,
            "name": self.name,
            "argument_text": self.argument_text,
        }
        if self.cwd:
            payload["cwd"] = self.cwd
        return payload


@dataclass(frozen=True)
class NativePanelBlockedError(RuntimeError):
    """Structured native panel launch/collection blockage.

    This is intentionally not a successful child result. It lets modes persist
    pending request refs and recovery pointers without pretending that a native
    specialist panel ran to completion.
    """

    reason: str
    request: NativeLaunchRequest
    message: str
    pending_request_ids: list[str] = field(default_factory=list)
    partial_results: list[NativeChildResult] = field(default_factory=list)
    raw_stdout: str = ""
    raw_stderr: str = ""

    def __post_init__(self) -> None:
        RuntimeError.__init__(self, self.message)

    @property
    def blocked_request_id(self) -> str:
        return self.request.request_id or self.request.idempotency_key

    @property
    def blocked_session_key(self) -> str:
        return self.request.session_key


@dataclass(frozen=True)
class OpenClawSessionProof:
    session_key: str
    session_id: str
    updated_at: int | None
    agent_id: str | None
    kind: str | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "session_key": self.session_key,
            "session_id": self.session_id,
            "updated_at": self.updated_at,
            "agent_id": self.agent_id,
            "kind": self.kind,
        }


@dataclass(frozen=True)
class OpenClawTrajectoryProof:
    session_key: str
    output_dir: str
    event_count: int
    runtime_event_count: int
    transcript_event_count: int
    tool_call_count: int
    tool_result_count: int
    tool_names: list[str]
    tool_call_refs: list[OpenClawToolCallRef] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "session_key": self.session_key,
            "output_dir": self.output_dir,
            "event_count": self.event_count,
            "runtime_event_count": self.runtime_event_count,
            "transcript_event_count": self.transcript_event_count,
            "tool_call_count": self.tool_call_count,
            "tool_result_count": self.tool_result_count,
            "tool_names": self.tool_names,
            "tool_call_refs": [item.as_dict() for item in self.tool_call_refs],
        }


class OpenClawSessionStoreProofChecker:
    """Verify that OpenClaw persisted the explicit child session key.

    This is a runtime session-store proof, not an independent proof that the
    child actually ran every claimed tool. Tool-smoke still comes from the
    child result and is separately bound to this explicit session key.
    """

    def __init__(
        self,
        *,
        openclaw_bin: str = "openclaw",
        runner: CommandRunner | None = None,
        timeout_seconds: int = 30,
    ) -> None:
        self.openclaw_bin = openclaw_bin
        self.runner = runner or _default_runner
        self.timeout_seconds = timeout_seconds

    def prove_session(self, request: NativeLaunchRequest) -> OpenClawSessionProof:
        command = [self.openclaw_bin, "sessions", "--json", "--all-agents", "--limit", "all"]
        completed = self.runner(command, self.timeout_seconds)
        if completed.returncode != 0:
            raise ValueError(completed.stderr.strip() or "openclaw sessions proof command failed")
        payload = json.loads(completed.stdout)
        sessions = payload.get("sessions") if isinstance(payload, dict) else None
        if not isinstance(sessions, list):
            raise ValueError("openclaw sessions proof output must include sessions array")
        for session in sessions:
            if not isinstance(session, dict) or session.get("key") != request.session_key:
                continue
            session_id = session.get("sessionId")
            if not isinstance(session_id, str) or not session_id.strip():
                raise ValueError("openclaw sessions proof requires sessionId")
            updated_at = session.get("updatedAt")
            return OpenClawSessionProof(
                session_key=request.session_key,
                session_id=session_id,
                updated_at=updated_at if isinstance(updated_at, int) else None,
                agent_id=session.get("agentId") if isinstance(session.get("agentId"), str) else None,
                kind=session.get("kind") if isinstance(session.get("kind"), str) else None,
            )
        raise ValueError(f"openclaw sessions proof missing exact session_key: {request.session_key}")


class OpenClawTrajectoryProofChecker:
    """Verify redacted trajectory contains tool-call evidence for the child.

    This proves the exported child transcript contains at least one tool.call
    and tool.result event. It deliberately does not prove every child-reported
    claim or raw tool output content, because the export is redacted.
    """

    def __init__(
        self,
        *,
        openclaw_bin: str = "openclaw",
        runner: CommandRunner | None = None,
        timeout_seconds: int = 30,
        workspace_dir: str | None = None,
    ) -> None:
        self.openclaw_bin = openclaw_bin
        self.runner = runner or _default_runner
        self.timeout_seconds = timeout_seconds
        self.workspace_dir = workspace_dir or str(Path.cwd())

    def prove_tool_events(self, request: NativeLaunchRequest) -> OpenClawTrajectoryProof:
        output_name = _trajectory_output_name(request)
        command = [
            self.openclaw_bin,
            "sessions",
            "export-trajectory",
            "--session-key",
            request.session_key,
            "--output",
            output_name,
            "--workspace",
            self.workspace_dir,
            "--json",
        ]
        completed = self.runner(command, self.timeout_seconds)
        if completed.returncode != 0:
            raise ValueError(completed.stderr.strip() or "openclaw trajectory proof command failed")
        summary = json.loads(completed.stdout)
        if not isinstance(summary, dict):
            raise ValueError("openclaw trajectory proof output must be an object")
        output_dir = summary.get("outputDir")
        if not isinstance(output_dir, str) or not output_dir.strip():
            raise ValueError("openclaw trajectory proof requires outputDir")
        events_path = Path(output_dir) / "events.jsonl"
        if not events_path.is_file():
            raise ValueError("openclaw trajectory proof requires events.jsonl")
        tool_call_count = 0
        tool_result_count = 0
        tool_names: set[str] = set()
        tool_call_refs: list[OpenClawToolCallRef] = []
        session_key_seen = False
        for raw_line in events_path.read_text(encoding="utf-8").splitlines():
            if not raw_line.strip():
                continue
            event = json.loads(raw_line)
            if not isinstance(event, dict):
                continue
            if event.get("sessionKey") != request.session_key:
                continue
            session_key_seen = True
            event_type = event.get("type")
            if event_type == "tool.call":
                tool_call_count += 1
                data = event.get("data")
                if isinstance(data, dict) and isinstance(data.get("name"), str) and data["name"].strip():
                    tool_name = data["name"]
                    tool_names.add(tool_name)
                    if len(tool_call_refs) < MAX_TRAJECTORY_TOOL_CALL_REFS:
                        arguments = data.get("arguments")
                        tool_call_refs.append(
                            OpenClawToolCallRef(
                                name=tool_name,
                                tool_call_id=_tool_call_id(data, fallback=f"tool-call-{tool_call_count}"),
                                cwd=_tool_call_cwd(arguments),
                                argument_text=_tool_call_argument_text(arguments),
                            )
                        )
            elif event_type == "tool.result":
                tool_result_count += 1
        if not session_key_seen:
            raise ValueError("openclaw trajectory proof requires matching sessionKey in exported events")
        if tool_call_count < 1 or tool_result_count < 1:
            raise ValueError("openclaw trajectory proof requires at least one tool.call and tool.result event")
        return OpenClawTrajectoryProof(
            session_key=request.session_key,
            output_dir=output_dir,
            event_count=_int_summary(summary, "eventCount"),
            runtime_event_count=_int_summary(summary, "runtimeEventCount"),
            transcript_event_count=_int_summary(summary, "transcriptEventCount"),
            tool_call_count=tool_call_count,
            tool_result_count=tool_result_count,
            tool_names=sorted(tool_names),
            tool_call_refs=tool_call_refs,
        )


@dataclass(frozen=True)
class OpenClawCliRun:
    command: list[str]
    result: NativeChildResult
    raw_stdout: str
    raw_stderr: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "command": self.command,
            "result": self.result.as_dict(),
            "raw_stdout": self.raw_stdout,
            "raw_stderr": self.raw_stderr,
        }


class OpenClawAgentCliBackend:
    """Run one explicit OpenClaw child session through `openclaw agent`.

    The adapter is intentionally synchronous for the first live wiring slice:
    the OpenClaw process owns the child turn and returns a structured response.
    Later Phase C work can split launch/wait/collect if the runtime exposes a
    stable nonblocking CLI for session spawning.
    """

    def __init__(
        self,
        *,
        openclaw_bin: str = "openclaw",
        runner: CommandRunner | None = None,
        command_timeout_cap_seconds: int | None = OPENCLAW_AGENT_COMMAND_TIMEOUT_CAP_SECONDS,
    ) -> None:
        self.openclaw_bin = openclaw_bin
        self.runner = runner or _default_runner
        if command_timeout_cap_seconds is not None and command_timeout_cap_seconds <= 0:
            raise ValueError("command_timeout_cap_seconds must be positive when provided")
        self.command_timeout_cap_seconds = command_timeout_cap_seconds

    def run_review(
        self,
        request: NativeLaunchRequest,
        *,
        timeout_seconds: int | None = None,
        satisfies_native_agent_panel: bool = False,
        coordinator_tool_smoke_evidence: dict[str, Any] | None = None,
    ) -> OpenClawCliRun:
        validate_native_launch_request(request.as_dict())
        validate_openclaw_agent_session_key(request.session_key)
        timeout = timeout_seconds or int(request.timeout_policy["child_lease_seconds"])
        if timeout_seconds is None and self.command_timeout_cap_seconds is not None:
            timeout = min(timeout, self.command_timeout_cap_seconds)
        command = [
            self.openclaw_bin,
            "agent",
            "--session-key",
            request.session_key,
            "--message",
            build_child_prompt(request),
            "--json",
            "--timeout",
            str(timeout),
        ]
        try:
            completed = self.runner(command, timeout)
            if completed.returncode != 0:
                stderr = completed.stderr.strip()
                raise NativePanelBlockedError(
                    reason=_classify_failure_text(completed.stdout, completed.stderr),
                    request=request,
                    message=stderr or f"openclaw agent exited with {completed.returncode}",
                    raw_stdout=completed.stdout,
                    raw_stderr=completed.stderr,
                )
            result = _result_from_completed_process(
                request,
                completed,
                satisfies_native_agent_panel=satisfies_native_agent_panel,
                coordinator_tool_smoke_evidence=coordinator_tool_smoke_evidence,
            )
        except subprocess.TimeoutExpired as exc:
            stdout = _timeout_text(exc.stdout)
            stderr = _timeout_text(exc.stderr)
            raise NativePanelBlockedError(
                reason=SUBAGENT_SPAWN_TIMEOUT,
                request=request,
                message=f"openclaw agent timed out after {timeout} seconds before returning a child result",
                raw_stdout=stdout,
                raw_stderr=stderr,
            ) from exc
        except (json.JSONDecodeError, ValueError) as exc:
            raise NativePanelBlockedError(
                reason=SUBAGENT_CLI_CONTRACT_CHANGED,
                request=request,
                message=str(exc),
            ) from exc
        return OpenClawCliRun(command=command, result=result, raw_stdout=completed.stdout, raw_stderr=completed.stderr)


def build_child_prompt(request: NativeLaunchRequest) -> str:
    payload = {
        "request_id": request.request_id,
        "mode": request.mode,
        "objective": request.objective,
        "target_refs": request.target_refs,
        "profile_ref": request.profile_ref,
        "profile_context_refs": request.profile_context_refs,
        "context_hash": request.context_hash,
        "output_schema": request.output_schema,
        "tool_policy": request.tool_policy,
        "budget_policy": request.budget_policy,
        "session_key": request.session_key,
        "agent_session_ref": request.session_key,
    }
    return (
        "You are a read-only Converge native specialist child session.\n"
        "Inspect only the provided target refs. The first target ref is a "
        "brief inline objective, not document content; documents must be "
        "inspected through file refs relative to their source_root. "
        "Do not send visible messages, "
        "mutate files, restart services, push, open PRs, release, or perform "
        "external actions.\n"
        "Return one JSON object only with keys: tool_smoke_status, findings, "
        "tool_smoke_evidence, error. tool_smoke_status must be passed only "
        "after an allowed file or artifact read and a harmless status/shell "
        "check when shell is in scope. tool_smoke_status describes whether you "
        "performed that tool smoke, not whether your review found risks; use "
        "passed after successful inspection even when findings report problems. "
        "tool_smoke_evidence must include status, "
        "kind, checked_at, session_key, agent_session_ref, read_action, "
        "status_action, and read_target_refs. read_target_refs must be an "
        "array of every file target ref you actually inspected, with path and "
        "source_root copied from REQUEST_JSON.target_refs. Use a separate "
        "harmless status tool call, such as git status --short or pwd, after "
        "the target ref reads; do not rely on the same read command as the "
        "status check. read_action must be read_files or read_artifacts; "
        "status_action must be shell_status. Set session_key "
        "and agent_session_ref exactly to REQUEST_JSON.session_key. If "
        "tool_smoke_status is passed, error must be null or omitted; use error "
        "only when you cannot complete the requested inspection. Avoid shell "
        "variables named status because zsh treats status as reserved. findings "
        "must contain at least one structured finding. If no defect is found, "
        "return one p3 informational finding that records the passed inspection. "
        "Each finding must include: finding_id, finding, severity, confidence, "
        "evidence, why_it_matters, minimal_fix_or_test, scope_risk, and "
        "failure_mode. confidence must be a JSON number from 0.0 to 1.0, "
        "not a string such as high or medium. evidence must be one non-empty "
        "string, not an array, and must use a concrete anchor such as "
        "agent_session_ref:<REQUEST_JSON.session_key>. Example finding shape: "
        '{"finding_id":"inspection_passed","finding":"Read-only inspection '
        'completed.","severity":"p3","confidence":0.9,"evidence":"agent_session_ref:agent:main:converge-example",'
        '"why_it_matters":"Confirms the target was inspected.",'
        '"minimal_fix_or_test":"No fix required.","scope_risk":"Limited to the '
        'provided target refs.","failure_mode":"none_observed"}.\n'
        f"REQUEST_JSON:\n{json.dumps(payload, ensure_ascii=True, sort_keys=True)}\n"
    )


def _result_from_completed_process(
    request: NativeLaunchRequest,
    completed: subprocess.CompletedProcess[str],
    *,
    satisfies_native_agent_panel: bool,
    coordinator_tool_smoke_evidence: dict[str, Any] | None,
) -> NativeChildResult:
    now = _format_time(datetime.now(timezone.utc))
    child_payload: dict[str, Any] = {}
    status = STATUS_COMPLETED
    error = None
    if completed.returncode != 0:
        status = STATUS_FAILED
        error = completed.stderr.strip() or f"openclaw agent exited with {completed.returncode}"
    else:
        child_payload = _extract_child_payload(completed.stdout)
        if child_payload.get("error"):
            status = STATUS_FAILED
            error = str(child_payload["error"])

    tool_smoke_status = child_payload.get("tool_smoke_status") or "not_run"
    if status == STATUS_COMPLETED and tool_smoke_status != TOOL_SMOKE_PASSED:
        status = STATUS_FAILED
        error = "coordinator-verified tool_smoke_status=passed is required before accepting CLI child output"
    findings = child_payload.get("findings") if isinstance(child_payload.get("findings"), list) else []
    child_tool_smoke_evidence = child_payload.get("tool_smoke_evidence")
    tool_smoke_evidence = child_tool_smoke_evidence if isinstance(child_tool_smoke_evidence, dict) else None
    if status == STATUS_COMPLETED and satisfies_native_agent_panel:
        if not isinstance(coordinator_tool_smoke_evidence, dict):
            status = STATUS_FAILED
            error = "native CLI child output requires coordinator-verified tool_smoke_evidence"
        else:
            tool_smoke_evidence = {
                **coordinator_tool_smoke_evidence,
                "status": coordinator_tool_smoke_evidence.get("status") or tool_smoke_status,
                "session_key": request.session_key,
                "agent_session_ref": request.session_key,
            }
    result = NativeChildResult(
        request_id=request.request_id or request.idempotency_key,
        result_id=_stable_result_id(request, completed.stdout, completed.stderr),
        agent_session_ref=request.session_key,
        session_key=request.session_key,
        tool_smoke_status=tool_smoke_status,
        profile_ref=request.profile_ref,
        context_hash=request.context_hash,
        status=status,
        findings=findings,
        started_at=now,
        deadline_at=now,
        completed_at=now,
        error=error,
        tool_smoke_evidence=tool_smoke_evidence if isinstance(tool_smoke_evidence, dict) else None,
        satisfies_native_agent_panel=satisfies_native_agent_panel and status == STATUS_COMPLETED,
        target_refs=[dict(item) for item in request.target_refs],
    )
    validate_native_child_result(result.as_dict())
    return result


class OpenClawNativePanelCliBackend:
    """Run a required native panel through explicit OpenClaw child sessions."""

    def __init__(
        self,
        *,
        child_backend: OpenClawAgentCliBackend | None = None,
        session_proof_checker: OpenClawSessionStoreProofChecker | None = None,
        trajectory_proof_checker: OpenClawTrajectoryProofChecker | None = None,
    ) -> None:
        self.child_backend = child_backend or OpenClawAgentCliBackend()
        self.session_proof_checker = session_proof_checker or OpenClawSessionStoreProofChecker(
            openclaw_bin=self.child_backend.openclaw_bin,
            runner=self.child_backend.runner,
        )
        self.trajectory_proof_checker = trajectory_proof_checker or OpenClawTrajectoryProofChecker(
            openclaw_bin=self.child_backend.openclaw_bin,
            runner=self.child_backend.runner,
        )

    def run_panel(self, requests: list[NativeLaunchRequest]) -> list[NativeChildResult]:
        if len(requests) not in {3, 5}:
            raise ValueError("native OpenClaw CLI panel requires exactly 3 or 5 child requests")
        results: list[NativeChildResult] = []
        for request in requests:
            try:
                run = self.child_backend.run_review(request)
            except NativePanelBlockedError as exc:
                raise replace(
                    exc,
                    pending_request_ids=_pending_request_ids(requests, request),
                    partial_results=list(results),
                ) from exc
            try:
                _validate_child_smoke_claim(request, run.result)
                session_proof = self.session_proof_checker.prove_session(request)
                trajectory_proof = self.trajectory_proof_checker.prove_tool_events(request)
                evidence = coordinator_verified_tool_smoke_evidence(
                    request,
                    run.result,
                    session_proof=session_proof,
                    trajectory_proof=trajectory_proof,
                )
                verified = replace(
                    run.result,
                    tool_smoke_evidence=evidence,
                    satisfies_native_agent_panel=True,
                    status=STATUS_COMPLETED,
                    error=None,
                )
                validate_native_child_result(verified.as_dict())
            except ValueError as exc:
                raise NativePanelBlockedError(
                    reason=SUBAGENT_PROOF_FAILED,
                    request=request,
                    message=str(exc),
                    pending_request_ids=_pending_request_ids(requests, request),
                    partial_results=list(results),
                    raw_stdout=run.raw_stdout,
                    raw_stderr=run.raw_stderr,
                ) from exc
            results.append(verified)
        return results


def coordinator_verified_tool_smoke_evidence(
    request: NativeLaunchRequest,
    result: NativeChildResult,
    *,
    session_proof: OpenClawSessionProof | None = None,
    trajectory_proof: OpenClawTrajectoryProof | None = None,
) -> dict[str, Any]:
    """Bind child-reported smoke proof to explicit refs plus runtime proofs."""

    evidence = _validate_child_smoke_claim(request, result)
    if session_proof is None:
        raise ValueError("native CLI child requires OpenClaw session-store proof")
    if session_proof.session_key != request.session_key:
        raise ValueError("native CLI session-store proof session_key must match requested session_key")
    if trajectory_proof is None:
        raise ValueError("native CLI child requires OpenClaw trajectory tool-event proof")
    if trajectory_proof.session_key != request.session_key:
        raise ValueError("native CLI trajectory proof session_key must match requested session_key")
    now = _format_time(datetime.now(timezone.utc))
    read_manifest = _validate_child_read_manifest(request, evidence)
    action_binding = _validate_trajectory_supports_child_actions(
        trajectory_proof,
        evidence,
        read_manifest=read_manifest,
    )
    return {
        "status": TOOL_SMOKE_PASSED,
        "kind": "coordinator_verified_child_tool_smoke_session_and_trajectory_binding",
        "checked_at": now,
        "session_key": request.session_key,
        "agent_session_ref": result.agent_session_ref,
        "verification_scope": "child_claim_bound_to_explicit_session_refs_with_openclaw_session_store_and_action_matched_trajectory_tool_events",
        "policy_enforcement": "prompt_and_coordinator_validation_only",
        "lifecycle_model": "synchronous_serial_openclaw_agent_child_process",
        "lifecycle_scope": "launch_and_collect_are_executed_by_one_bounded_openclaw_agent_command; wait is the bounded command wait",
        "child_tool_smoke_kind": evidence["kind"],
        "child_tool_smoke_checked_at": evidence["checked_at"],
        "read_action": evidence["read_action"],
        "status_action": evidence["status_action"],
        "child_read_action": evidence["read_action"],
        "child_status_action": evidence["status_action"],
        "target_ref_read_manifest": read_manifest,
        "trajectory_action_binding": action_binding,
        "session_store_proof": session_proof.as_dict(),
        "trajectory_proof": trajectory_proof.as_dict(),
        "launch_ref": request.session_key,
        "wait_ref": request.session_key,
        "collect_ref": request.session_key,
    }


def _validate_child_smoke_claim(request: NativeLaunchRequest, result: NativeChildResult) -> dict[str, Any]:
    if result.status != STATUS_COMPLETED:
        raise ValueError(f"native CLI child {request.session_key} did not complete: {result.error or result.status}")
    if result.tool_smoke_status != TOOL_SMOKE_PASSED:
        raise ValueError(f"native CLI child {request.session_key} did not pass tool smoke")
    if result.session_key != request.session_key or result.agent_session_ref != request.session_key:
        raise ValueError("native CLI child result session refs do not match requested session_key")
    evidence = result.tool_smoke_evidence
    if not isinstance(evidence, dict):
        raise ValueError("native CLI child result requires child tool_smoke_evidence before coordinator acceptance")
    if evidence.get("status") != TOOL_SMOKE_PASSED:
        raise ValueError("native CLI child tool_smoke_evidence.status must be passed")
    if evidence.get("session_key") != request.session_key:
        raise ValueError("native CLI child tool_smoke_evidence.session_key must match requested session_key")
    if evidence.get("agent_session_ref") != result.agent_session_ref:
        raise ValueError("native CLI child tool_smoke_evidence.agent_session_ref must match result agent_session_ref")
    if not isinstance(evidence.get("kind"), str) or not evidence["kind"].strip():
        raise ValueError("native CLI child tool_smoke_evidence.kind is required")
    if not isinstance(evidence.get("checked_at"), str) or not evidence["checked_at"].strip():
        raise ValueError("native CLI child tool_smoke_evidence.checked_at is required")
    if evidence.get("read_action") not in {"read_files", "read_artifacts"}:
        raise ValueError("native CLI child tool_smoke_evidence.read_action must be read_files or read_artifacts")
    if evidence.get("status_action") != "shell_status":
        raise ValueError("native CLI child tool_smoke_evidence.status_action must be shell_status")
    return evidence


def _validate_child_read_manifest(request: NativeLaunchRequest, evidence: dict[str, Any]) -> dict[str, Any]:
    required = [
        {"path": item.get("path"), "source_root": item.get("source_root")}
        for item in request.target_refs
        if item.get("kind") == "file"
    ]
    required = [
        {"path": item["path"], "source_root": item["source_root"]}
        for item in required
        if isinstance(item.get("path"), str) and item["path"] and isinstance(item.get("source_root"), str) and item["source_root"]
    ]
    raw_read_refs = evidence.get("read_target_refs")
    if not required:
        return {"required_count": 0, "read_count": 0, "missing": [], "read_target_refs": []}
    if not isinstance(raw_read_refs, list):
        raise ValueError("native CLI child tool_smoke_evidence requires read_target_refs for file target refs")
    read_refs: list[dict[str, str]] = []
    for item in raw_read_refs:
        if isinstance(item, str):
            raise ValueError("native CLI child read_target_refs entries require source_root and path objects")
        if not isinstance(item, dict):
            raise ValueError("native CLI child read_target_refs entries must be objects")
        path = item.get("path")
        source_root = item.get("source_root")
        if not isinstance(path, str) or not path:
            raise ValueError("native CLI child read_target_refs entries require path")
        if not isinstance(source_root, str) or not source_root:
            raise ValueError("native CLI child read_target_refs entries require source_root")
        read_refs.append({"path": path, "source_root": source_root})
    read_keys = {(item["source_root"], item["path"]) for item in read_refs}
    missing = [
        item
        for item in required
        if (item["source_root"], item["path"]) not in read_keys
    ]
    if missing:
        missing_paths = ", ".join(item["path"] for item in missing[:5])
        raise ValueError(f"native CLI child read_target_refs missing requested target refs: {missing_paths}")
    return {
        "required_count": len(required),
        "read_count": len(read_refs),
        "missing": [],
        "read_target_refs": read_refs,
    }


def _validate_trajectory_supports_child_actions(
    trajectory_proof: OpenClawTrajectoryProof,
    evidence: dict[str, Any],
    *,
    read_manifest: dict[str, Any],
) -> dict[str, Any]:
    tool_names = set(trajectory_proof.tool_names)
    if not tool_names:
        raise ValueError("native CLI trajectory proof requires named tool events")
    read_action = evidence["read_action"]
    if read_action not in {"read_files", "read_artifacts"}:
        raise ValueError("native CLI child read_action must be read_files or read_artifacts")
    read_supported = bool(tool_names & READ_ACTION_TOOL_NAMES)
    status_supported = bool(tool_names & STATUS_ACTION_TOOL_NAMES)
    if not read_supported:
        raise ValueError("native CLI trajectory proof lacks a tool event compatible with read_files/read_artifacts")
    if not status_supported:
        raise ValueError("native CLI trajectory proof lacks a tool event compatible with shell_status")
    target_ref_read_binding = _validate_trajectory_read_manifest(trajectory_proof, read_manifest)
    status_action_binding = _validate_trajectory_status_action(
        trajectory_proof,
        excluded_tool_call_ids=set(target_ref_read_binding["matched_tool_call_ids"]),
    )
    allowed_tool_binding = _validate_trajectory_has_no_unexpected_tool_calls(
        trajectory_proof,
        read_manifest=read_manifest,
        status_tool_call_id=status_action_binding["tool_call_id"],
    )
    return {
        "read_action": read_action,
        "status_action": evidence["status_action"],
        "tool_names": sorted(tool_names),
        "read_action_bound_by_tool_names": read_supported,
        "status_action_bound_by_tool_names": status_supported,
        "target_ref_read_binding": target_ref_read_binding,
        "status_action_binding": status_action_binding,
        "allowed_tool_binding": allowed_tool_binding,
    }


def _validate_trajectory_read_manifest(
    trajectory_proof: OpenClawTrajectoryProof,
    read_manifest: dict[str, Any],
) -> dict[str, Any]:
    required_refs = read_manifest.get("read_target_refs")
    if not isinstance(required_refs, list) or not required_refs:
        return {"required_count": 0, "matched_count": 0, "missing": [], "matched_tool_call_ids": []}
    read_calls = [
        item
        for item in trajectory_proof.tool_call_refs
        if item.name in READ_ACTION_TOOL_NAMES
    ]
    missing: list[dict[str, str]] = []
    matched_tool_call_ids: set[str] = set()
    for item in required_refs:
        if not isinstance(item, dict):
            continue
        path = item.get("path")
        source_root = item.get("source_root")
        if not isinstance(path, str) or not isinstance(source_root, str):
            continue
        match = next(
            (call for call in read_calls if _tool_call_ref_covers_target_ref(call, path=path, source_root=source_root)),
            None,
        )
        if match is None:
            missing.append({"path": path, "source_root": source_root})
        else:
            matched_tool_call_ids.add(match.tool_call_id)
    if missing:
        missing_paths = ", ".join(item["path"] for item in missing[:5])
        raise ValueError(f"native CLI trajectory proof lacks target_ref read argument: {missing_paths}")
    return {
        "required_count": len(required_refs),
        "matched_count": len(required_refs),
        "missing": [],
        "proof": "read_tool_call_arguments",
        "matched_tool_call_ids": sorted(matched_tool_call_ids),
    }


def _validate_trajectory_status_action(
    trajectory_proof: OpenClawTrajectoryProof,
    *,
    excluded_tool_call_ids: set[str],
) -> dict[str, Any]:
    for call in trajectory_proof.tool_call_refs:
        if call.name not in STATUS_ACTION_TOOL_NAMES or call.tool_call_id in excluded_tool_call_ids:
            continue
        if _tool_call_ref_is_harmless_status(call):
            return {
                "proof": "separate_status_tool_call_argument",
                "tool_call_id": call.tool_call_id,
                "tool_name": call.name,
            }
    raise ValueError("native CLI trajectory proof lacks a separate harmless shell_status tool argument")


def _validate_trajectory_has_no_unexpected_tool_calls(
    trajectory_proof: OpenClawTrajectoryProof,
    *,
    read_manifest: dict[str, Any],
    status_tool_call_id: str,
) -> dict[str, Any]:
    allowed_count = 0
    for call in trajectory_proof.tool_call_refs:
        if _tool_call_ref_is_harmless_status(call):
            allowed_count += 1
            continue
        if call.name in READ_ACTION_TOOL_NAMES and _tool_call_ref_is_allowed_startup_read(call):
            allowed_count += 1
            continue
        if call.name in READ_ACTION_TOOL_NAMES and _tool_call_ref_covers_any_read_target(call, read_manifest):
            allowed_count += 1
            continue
        raise ValueError(f"native CLI trajectory proof contains unexpected tool call: {call.name}")
    return {
        "proof": "all_tool_calls_match_read_or_status_policy",
        "checked_tool_call_count": len(trajectory_proof.tool_call_refs),
        "allowed_tool_call_count": allowed_count,
    }


def _tool_call_ref_covers_any_read_target(call: OpenClawToolCallRef, read_manifest: dict[str, Any]) -> bool:
    read_refs = read_manifest.get("read_target_refs")
    if not isinstance(read_refs, list):
        return False
    for item in read_refs:
        if not isinstance(item, dict):
            continue
        path = item.get("path")
        source_root = item.get("source_root")
        if isinstance(path, str) and isinstance(source_root, str) and _tool_call_ref_covers_target_ref(
            call,
            path=path,
            source_root=source_root,
        ):
            return True
    return False


def _tool_call_ref_is_harmless_status(call: OpenClawToolCallRef) -> bool:
    text = call.argument_text.lower()
    harmless_markers = (
        "git status --short",
        "git status --porcelain",
        "\"pwd\"",
        "'pwd'",
        " pwd",
        "test -",
        "true",
    )
    return any(marker in text for marker in harmless_markers)


def _tool_call_ref_is_allowed_startup_read(call: OpenClawToolCallRef) -> bool:
    text = call.argument_text
    cwd = call.cwd or ""
    combined = f"{cwd}\n{text}"
    if "$WORKSPACE_DIR" not in combined and "/.openclaw/workspace" not in combined:
        return False
    allowed_exact = (
        "SOUL.md",
        "USER.md",
        "MEMORY.md",
        "AGENTS.md",
        "TOOLS.md",
    )
    if any(path in text for path in allowed_exact):
        return True
    return re.search(r"memory/20\d{2}-\d{2}-\d{2}\.md", text) is not None


def _tool_call_ref_covers_target_ref(call: OpenClawToolCallRef, *, path: str, source_root: str) -> bool:
    argument_text = call.argument_text
    cwd = call.cwd or ""
    combined = f"{cwd}\n{argument_text}"
    if path not in combined:
        return False
    if source_root in combined:
        return True
    if _source_root_placeholder(source_root) in combined:
        return True
    try:
        return bool(cwd) and Path(cwd).expanduser().resolve() == Path(source_root).expanduser().resolve()
    except (OSError, RuntimeError):
        return False


def _source_root_placeholder(source_root: str) -> str:
    normalized = source_root.rstrip("/")
    if normalized.endswith("/.openclaw/converge"):
        return "$OPENCLAW_STATE_DIR/converge"
    if normalized.endswith("/.openclaw/workspace"):
        return "$WORKSPACE_DIR"
    return source_root


def _tool_call_cwd(arguments: Any) -> str | None:
    if isinstance(arguments, dict):
        value = arguments.get("cwd") or arguments.get("workdir")
        if isinstance(value, str) and value.strip():
            return value
    return None


def _tool_call_id(data: dict[str, Any], *, fallback: str) -> str:
    value = data.get("toolCallId") or data.get("tool_call_id") or data.get("id")
    if isinstance(value, str) and value.strip():
        return value
    return fallback


def _tool_call_argument_text(arguments: Any) -> str:
    if isinstance(arguments, (dict, list)):
        text = json.dumps(arguments, ensure_ascii=False, sort_keys=True)
    elif arguments is None:
        text = ""
    else:
        text = str(arguments)
    encoded = text.encode("utf-8")
    if len(encoded) <= MAX_TRAJECTORY_ARGUMENT_TEXT_BYTES:
        return text
    return encoded[:MAX_TRAJECTORY_ARGUMENT_TEXT_BYTES].decode("utf-8", errors="ignore")


def validate_openclaw_agent_session_key(value: str) -> None:
    if not isinstance(value, str) or not value.startswith("agent:") or value.count(":") < 2:
        raise ValueError("openclaw CLI session_key must use live-safe agent:<id>:<key> form")


def _extract_child_payload(stdout: str) -> dict[str, Any]:
    outer = json.loads(stdout)
    if not isinstance(outer, dict):
        raise ValueError("openclaw agent --json output must be an object")
    candidates: list[Any] = []
    for key in ("response", "reply", "message", "content", "text", "output"):
        candidates.append(outer.get(key))
    result = outer.get("result")
    if isinstance(result, dict):
        candidates.extend([result.get("finalAssistantRawText"), result.get("finalAssistantVisibleText")])
        payloads = result.get("payloads")
        if isinstance(payloads, list):
            for payload in payloads:
                if isinstance(payload, dict):
                    candidates.append(payload.get("text"))
    for value in candidates:
        if isinstance(value, dict):
            return value
        if isinstance(value, str) and value.strip():
            try:
                parsed = json.loads(value)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                return parsed
    if {"tool_smoke_status", "findings"} <= set(outer):
        return outer
    raise ValueError("openclaw agent output did not contain structured child JSON")


def _stable_result_id(request: NativeLaunchRequest, stdout: str, stderr: str) -> str:
    from .contracts import stable_hash

    return stable_hash(
        {
            "request_id": request.request_id or request.idempotency_key,
            "session_key": request.session_key,
            "stdout": stdout,
            "stderr": stderr,
        }
    )


def _trajectory_output_name(request: NativeLaunchRequest) -> str:
    safe = "".join(char if char.isalnum() or char in {"-", "_"} else "-" for char in request.session_key)
    return f"converge-native-proof-{safe}"[:160]


def _int_summary(summary: dict[str, Any], key: str) -> int:
    value = summary.get(key)
    if isinstance(value, int) and value >= 0:
        return value
    raise ValueError(f"openclaw trajectory proof requires non-negative {key}")


def _default_runner(command: list[str], timeout_seconds: int) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, text=True, capture_output=True, check=False, timeout=timeout_seconds)


def _timeout_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _pending_request_ids(requests: list[NativeLaunchRequest], blocked_request: NativeLaunchRequest) -> list[str]:
    blocked_id = blocked_request.request_id or blocked_request.idempotency_key
    ids = [request.request_id or request.idempotency_key for request in requests]
    if blocked_id not in ids:
        return ids
    return ids[ids.index(blocked_id) :]


def _classify_failure_text(*values: str) -> str:
    text = " ".join(
        item.lower()
        for item in values
        if isinstance(item, str)
    )
    capacity_markers = (
        "capacity",
        "slot",
        "slots",
        "concurrent",
        "concurrency",
        "maxchildren",
        "max children",
        "sessions are full",
        "too many",
        "limit",
    )
    if any(marker in text for marker in capacity_markers):
        return SUBAGENT_CAPACITY_EXHAUSTED
    return SUBAGENT_SPAWN_FAILED


def _format_time(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
