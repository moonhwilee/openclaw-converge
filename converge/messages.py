"""Human-readable worklog and visible report formatting."""

from __future__ import annotations

from typing import Any


VALID_VERDICTS = {"pass", "pass_with_risks", "needs_fix", "blocked", "stopped"}
ROUND_VERIFICATION_RESULTS = {
    "continuing",
    "complete_pass",
    "complete_pass_with_risks",
    "complete_needs_fix",
    "stopped",
}
ROUND_TARGET_RESULTS = {"pass", "pass_with_risks", "needs_fix", "blocked"}
ROUND_REGRESSION_RESULTS = {"pass", "pass_with_risks", "needs_fix", "blocked", "none"}
RESIDUAL_KEYS = (
    "blocking_remaining",
    "accepted_risks",
    "implementation_backlog",
    "deferred_scope",
)
RESIDUAL_LABELS = {
    "blocking_remaining": "Blocking remaining",
    "accepted_risks": "Accepted risks",
    "implementation_backlog": "Implementation backlog",
    "deferred_scope": "Deferred scope",
}
MODE_LABELS = {
    "plan": "Plan",
    "goal": "Goal",
    "verify": "Verification",
    "conv": "Convergence",
}
START_MARKERS = {f"▶ {label} start" for label in MODE_LABELS.values()}
FINAL_MARKERS = {f"■ {label} final" for label in MODE_LABELS.values()}
MAX_VISIBLE_CHARS = 3900


class MessageLintError(ValueError):
    """Raised when a visible Converge report violates the message contract."""


def checkpoint_block(summary: str, update: dict[str, Any], checkpoint_id: str, event_id: str) -> str:
    residuals = update.get("residuals") or {}
    return "\n".join(
        [
            "",
            f"## Checkpoint {checkpoint_id}",
            "",
            f"- Summary: {summary}",
            f"- Checkpoint type: {update['checkpoint_type']}",
            f"- Status after: {update['status_after']}",
            f"- Phase after: {update['phase_after']}",
            f"- Cursor: {update['cursor_before']} -> {update['cursor_after']}",
            f"- Event: {event_id}",
            f"- Step result: {update['step_result']}",
            f"- Blocking remaining: {residuals.get('blocking_remaining', [])}",
            f"- Accepted risks: {residuals.get('accepted_risks', [])}",
            f"- Implementation backlog: {residuals.get('implementation_backlog', [])}",
            f"- Deferred scope: {residuals.get('deferred_scope', [])}",
            "",
        ]
    )


def progress_block(round_number: int, summary: str) -> str:
    return f"\n## Progress {round_number}\n\n- Summary: {summary}\n\n"


def format_start(workflow: dict[str, Any]) -> str:
    label = _mode_label(workflow)
    boundary = workflow.get("approval_boundaries") or workflow.get("non_goals") or ["standard Converge local-work boundary"]
    text = "\n".join(
        [
            f"▶ {label} start",
            f"Workflow: {workflow.get('workflow_id', '<unknown>')}",
            f"Objective: {_one_line(workflow.get('objective') or workflow.get('source_request') or '')}",
            "Boundary:",
            *_bullet_lines(boundary),
        ]
    )
    lint_visible(text)
    return text


def format_round_start(workflow: dict[str, Any], round_spec: dict[str, Any]) -> str:
    round_number = _round_number(round_spec)
    text = "\n".join(
        [
            f"▶ Round {round_number} start",
            "",
            "Target:",
            *_bullet_lines(round_spec.get("target") or [workflow.get("objective") or workflow.get("source_request") or "current target"]),
            "",
            "Focus:",
            *_bullet_lines(round_spec.get("focus") or ["current accepted objective"]),
            "",
            "Gate:",
            *_bullet_lines(round_spec.get("gate") or ["evidence sufficiency"]),
        ]
    )
    lint_visible(text)
    return text


def format_round_summary(workflow: dict[str, Any], round_result: dict[str, Any]) -> str:
    round_number = _round_number(round_result)
    result = round_result.get("verification_result") or round_result.get("result") or "continuing"
    original = round_result.get("original_target")
    if original is None:
        raise MessageLintError("round summary missing original_target")
    regression = round_result.get("patch_regression") or "none"
    _validate_value("verification_result", result, ROUND_VERIFICATION_RESULTS)
    _validate_value("original_target", original, ROUND_TARGET_RESULTS)
    _validate_value("patch_regression", regression, ROUND_REGRESSION_RESULTS)
    text = "\n".join(
        [
            f"■ Round {round_number} summary",
            "",
            "Status:",
            f"- Verification result: {result}",
            f"- Original target: {original}",
            f"- Patch regression: {regression}",
            "",
            "Found:",
            *_bullet_lines(round_result.get("found") or ["none"]),
            "",
            "Accepted:",
            *_bullet_lines(round_result.get("accepted") or ["none"]),
            "",
            "Rejected / Deferred:",
            *_bullet_lines(round_result.get("rejected_deferred") or round_result.get("rejected") or ["none"]),
            "",
            "Checked:",
            *_bullet_lines(round_result.get("checked") or ["none"]),
            "",
            "Next:",
            *_bullet_lines(round_result.get("next") or ["none"]),
        ]
    )
    lint_visible(text)
    return text


def format_final(workflow: dict[str, Any]) -> str:
    label = _mode_label(workflow)
    final_status = _final_status(workflow)
    verdict = _extract_verdict(final_status)
    residuals = normalize_residuals(
        final_status.get("residuals")
        or _state_for_kind(workflow).get("residuals")
        or {
            "blocking_remaining": _state_for_kind(workflow).get("blocking_remaining", []),
            "accepted_risks": _state_for_kind(workflow).get("accepted_risks", []),
            "implementation_backlog": _state_for_kind(workflow).get("implementation_backlog", []),
            "deferred_scope": _state_for_kind(workflow).get("deferred_scope", []),
        }
    )
    lint_verdict_residuals(verdict, residuals)
    text = "\n".join(
        [
            f"■ {label} final",
            "",
            "Status:",
            f"- Result: {verdict}",
            "",
            "Done:",
            *_bullet_lines(final_status.get("done") or workflow.get("side_effects_performed") or ["none"]),
            "",
            "Checked:",
            *_bullet_lines(final_status.get("checked") or _checked_items(workflow) or ["none"]),
            "",
            format_remaining(residuals),
        ]
    )
    lint_visible(text)
    return text


def format_remaining(residuals: dict[str, Any]) -> str:
    normalized = normalize_residuals(residuals)
    if not any(normalized.values()):
        return "Remaining: none"
    lines = ["Remaining:"]
    for key in RESIDUAL_KEYS:
        lines.append(f"- {RESIDUAL_LABELS[key]}:")
        lines.extend(f"  - {_one_line(item)}" for item in normalized[key])
        if not normalized[key]:
            lines.append("  - none")
    return "\n".join(lines)


def normalize_residuals(residuals: dict[str, Any] | None) -> dict[str, list[str]]:
    residuals = residuals or {}
    unknown = sorted(set(residuals) - set(RESIDUAL_KEYS))
    if unknown:
        raise MessageLintError(f"unknown residual buckets: {unknown!r}")
    normalized: dict[str, list[str]] = {}
    for key in RESIDUAL_KEYS:
        value = residuals.get(key, [])
        if value is None:
            value = []
        if not isinstance(value, list):
            raise MessageLintError(f"{key} must be a list")
        normalized[key] = [_one_line(item) for item in value]
    return normalized


def lint_verdict_residuals(verdict: str, residuals: dict[str, Any]) -> None:
    if verdict not in VALID_VERDICTS:
        raise MessageLintError(f"unknown verdict: {verdict!r}")
    normalized = normalize_residuals(residuals)
    if verdict in {"pass", "pass_with_risks"} and normalized["blocking_remaining"]:
        raise MessageLintError(f"{verdict} cannot have blocking_remaining")


def lint_visible(text: str) -> None:
    if len(text) > MAX_VISIBLE_CHARS:
        raise MessageLintError(f"visible message exceeds {MAX_VISIBLE_CHARS} chars")
    lines = text.splitlines()
    if not lines or not lines[0].strip():
        raise MessageLintError("visible message must have a first line")
    first = lines[0].strip()
    if (
        first not in START_MARKERS
        and first not in FINAL_MARKERS
        and not (first.startswith("▶ Round ") and first.endswith(" start"))
        and not (first.startswith("■ Round ") and first.endswith(" summary"))
    ):
        raise MessageLintError(f"unknown message marker: {first!r}")
    if any(_looks_like_markdown_table(line) for line in lines):
        raise MessageLintError("markdown tables are not allowed")
    if any(_looks_like_raw_output(line) for line in lines):
        raise MessageLintError("raw logs or stack traces are not allowed")
    if first in START_MARKERS:
        _lint_start_sections(lines)
    elif first.startswith("▶ Round ") and first.endswith(" start"):
        _lint_round_marker(first, "start")
        _lint_round_start_sections(lines)
    elif first.startswith("■ Round ") and first.endswith(" summary"):
        _lint_round_marker(first, "summary")
        _lint_round_summary_sections(lines)
    elif first in FINAL_MARKERS:
        _lint_final_sections(text)


def _lint_start_sections(lines: list[str]) -> None:
    stripped = _stripped_nonempty(lines)
    if len(stripped) < 5:
        raise MessageLintError("start report missing required sections")
    if not stripped[1].startswith("Workflow:"):
        raise MessageLintError("start report missing Workflow")
    if not stripped[1].removeprefix("Workflow:").strip():
        raise MessageLintError("start report Workflow must not be empty")
    if not stripped[2].startswith("Objective:"):
        raise MessageLintError("start report missing Objective")
    if not stripped[2].removeprefix("Objective:").strip():
        raise MessageLintError("start report Objective must not be empty")
    if stripped[3] != "Boundary:":
        raise MessageLintError("start report sections are out of order")
    if not _has_bullet(stripped[4:]):
        raise MessageLintError("start report Boundary must include at least one item")
    _require_only_bullets(stripped[4:], "start Boundary")


def _lint_round_start_sections(lines: list[str]) -> None:
    stripped = _stripped_nonempty(lines)
    sections = _ordered_section_indexes(stripped, ("Target:", "Focus:", "Gate:"))
    for section, index in sections.items():
        end = _next_index(index, sections.values(), len(stripped))
        section_lines = stripped[index + 1 : end]
        if not _has_bullet(section_lines):
            raise MessageLintError(f"round start {section} must include at least one item")
        _require_only_bullets(section_lines, f"round start {section}")


def _lint_round_summary_sections(lines: list[str]) -> None:
    stripped = _stripped_nonempty(lines)
    sections = _ordered_section_indexes(
        stripped,
        ("Status:", "Found:", "Accepted:", "Rejected / Deferred:", "Checked:", "Next:"),
    )
    status_end = _next_index(sections["Status:"], sections.values(), len(stripped))
    status_lines = stripped[sections["Status:"] + 1 : status_end]
    values = _parse_prefixed_status_lines(
        status_lines,
        {
            "- Verification result:": "verification_result",
            "- Original target:": "original_target",
            "- Patch regression:": "patch_regression",
        },
    )
    _validate_value("verification_result", values["verification_result"], ROUND_VERIFICATION_RESULTS)
    _validate_value("original_target", values["original_target"], ROUND_TARGET_RESULTS)
    _validate_value("patch_regression", values["patch_regression"], ROUND_REGRESSION_RESULTS)
    for section, index in sections.items():
        if section == "Status:":
            continue
        end = _next_index(index, sections.values(), len(stripped))
        section_lines = stripped[index + 1 : end]
        if not _has_bullet(section_lines):
            raise MessageLintError(f"round summary {section} must include at least one item")
        _require_only_bullets(section_lines, f"round summary {section}")


def _lint_final_sections(text: str) -> None:
    stripped = _stripped_nonempty(text.splitlines())
    sections = _ordered_section_indexes(stripped, ("Status:", "Done:", "Checked:"))
    remaining_index = _remaining_index(stripped)
    if remaining_index <= sections["Checked:"]:
        raise MessageLintError("final report sections are out of order")
    status_lines = stripped[sections["Status:"] + 1 : sections["Done:"]]
    done_lines = stripped[sections["Done:"] + 1 : sections["Checked:"]]
    checked_lines = stripped[sections["Checked:"] + 1 : remaining_index]
    if not _has_bullet(done_lines):
        raise MessageLintError("final report Done must include at least one item")
    if not _has_bullet(checked_lines):
        raise MessageLintError("final report Checked must include at least one item")
    _require_only_bullets(done_lines, "final Done")
    _require_only_bullets(checked_lines, "final Checked")
    remaining_lines = _remaining_lines(text)
    if len(remaining_lines) == 1 and remaining_lines[0] == "Remaining: none":
        _lint_final_status_verdict(status_lines, {key: [] for key in RESIDUAL_KEYS})
    elif remaining_lines:
        for label in RESIDUAL_LABELS.values():
            if f"- {label}:" not in remaining_lines:
                raise MessageLintError(f"final report missing residual bucket: {label}")
        _lint_final_status_verdict(status_lines, _parse_remaining_lines(remaining_lines))
    else:
        raise MessageLintError("final report missing Remaining section")


def _lint_final_status_verdict(status_lines: list[str], residuals: dict[str, list[str]]) -> None:
    verdict = _parse_result_text(status_lines)
    lint_verdict_residuals(verdict, residuals)


def _parse_result_text(lines: list[str]) -> str:
    matches = [line.removeprefix("- Result:").strip() for line in lines if line.startswith("- Result:")]
    if len(matches) != 1 or not matches[0]:
        raise MessageLintError("final report missing result")
    unexpected = [line for line in lines if not line.startswith("- Result:")]
    if unexpected:
        raise MessageLintError("final Status has unknown fields")
    return matches[0]


def _remaining_lines(text: str) -> list[str]:
    lines = text.splitlines()
    start_index: int | None = None
    for index, line in enumerate(lines):
        stripped = line.strip()
        if stripped == "Remaining:" or stripped == "Remaining: none":
            start_index = index
            break
    if start_index is None:
        return []
    return [line.strip() for line in lines[start_index:] if line.strip()]


def _parse_remaining_lines(lines: list[str]) -> dict[str, list[str]]:
    residuals: dict[str, list[str]] = {key: [] for key in RESIDUAL_KEYS}
    active_key: str | None = None
    label_to_key = {label: key for key, label in RESIDUAL_LABELS.items()}
    for stripped in lines:
        if stripped == "Remaining:":
            continue
        if stripped == "Remaining: none":
            raise MessageLintError("Remaining: none cannot be mixed with residual buckets")
        if stripped.startswith("- ") and stripped.endswith(":"):
            label = stripped[2:-1]
            active_key = label_to_key.get(label)
            if active_key is None:
                raise MessageLintError(f"unknown residual bucket: {label!r}")
            continue
        if active_key and stripped.startswith("- "):
            item = stripped[2:].strip()
            if item and item != "none":
                residuals[active_key].append(item)
        elif stripped.startswith("- "):
            raise MessageLintError("residual item has no bucket")
        else:
            raise MessageLintError("residual line must be a known bucket or item")
    return residuals


def _stripped_nonempty(lines: list[str]) -> list[str]:
    return [line.strip() for line in lines if line.strip()]


def _lint_round_marker(first: str, suffix: str) -> None:
    parts = first.split()
    if len(parts) != 4 or parts[0] not in {"▶", "■"} or parts[1] != "Round" or parts[3] != suffix:
        raise MessageLintError("round marker has invalid shape")
    try:
        number = int(parts[2])
    except ValueError as exc:
        raise MessageLintError("round marker number must be an integer") from exc
    if number < 1:
        raise MessageLintError("round marker number must be positive")


def _find_line(lines: list[str], target: str) -> int:
    try:
        return lines.index(target)
    except ValueError as exc:
        raise MessageLintError(f"missing section: {target}") from exc


def _ordered_section_indexes(lines: list[str], required: tuple[str, ...]) -> dict[str, int]:
    indexes = {section: _find_line(lines, section) for section in required}
    last = -1
    for section in required:
        index = indexes[section]
        if index <= last:
            raise MessageLintError("message sections are out of order")
        last = index
    return indexes


def _remaining_index(lines: list[str]) -> int:
    for index, line in enumerate(lines):
        if line in {"Remaining:", "Remaining: none"}:
            return index
    raise MessageLintError("final report missing Remaining section")


def _next_index(current: int, indexes: Any, default: int) -> int:
    later = [index for index in indexes if index > current]
    return min(later) if later else default


def _has_bullet(lines: list[str]) -> bool:
    return any(line.startswith("- ") for line in lines)


def _require_only_bullets(lines: list[str], section: str) -> None:
    if any(not line.startswith("- ") for line in lines):
        raise MessageLintError(f"{section} contains non-bullet content")


def _parse_prefixed_status_lines(lines: list[str], prefixes: dict[str, str]) -> dict[str, str]:
    values: dict[str, str] = {}
    for prefix, name in prefixes.items():
        matches = [line.removeprefix(prefix).strip() for line in lines if line.startswith(prefix)]
        if len(matches) != 1 or not matches[0]:
            raise MessageLintError(f"round summary status missing {name}")
        values[name] = matches[0]
    if len(lines) != len(prefixes):
        raise MessageLintError("round summary Status has unknown fields")
    return values


def _mode_label(workflow: dict[str, Any]) -> str:
    kind = workflow.get("kind")
    if kind not in MODE_LABELS:
        raise MessageLintError(f"unknown workflow kind: {kind!r}")
    return MODE_LABELS[kind]


def _round_number(payload: dict[str, Any]) -> int:
    value = payload.get("round") or payload.get("round_number")
    if not isinstance(value, int) or value < 1:
        raise MessageLintError("round number must be a positive integer")
    return value


def _bullet_lines(value: Any) -> list[str]:
    items = value if isinstance(value, list) else [value]
    return [f"- {_one_line(item)}" for item in items]


def _one_line(value: Any) -> str:
    text = str(value).strip().replace("\n", " ")
    return " ".join(text.split()) or "none"


def _final_status(workflow: dict[str, Any]) -> dict[str, Any]:
    final_status = workflow.get("final_status")
    if isinstance(final_status, dict):
        return final_status
    raise MessageLintError("workflow final_status must be an object")


def _extract_verdict(final_status: dict[str, Any]) -> str:
    verdict = final_status.get("result")
    if not isinstance(verdict, str):
        raise MessageLintError("final_status must include result")
    return verdict


def _state_for_kind(workflow: dict[str, Any]) -> dict[str, Any]:
    state = workflow.get(f"{workflow.get('kind')}_state")
    return state if isinstance(state, dict) else {}


def _checked_items(workflow: dict[str, Any]) -> list[str]:
    verification = workflow.get("verification")
    if isinstance(verification, dict):
        checked = verification.get("checked") or verification.get("evidence")
        if isinstance(checked, list):
            return [_one_line(item) for item in checked]
    return []


def _looks_like_markdown_table(line: str) -> bool:
    stripped = line.strip()
    return stripped.startswith("|") and stripped.endswith("|") and stripped.count("|") >= 2


def _looks_like_raw_output(line: str) -> bool:
    stripped = line.strip()
    if stripped.startswith("- "):
        stripped = stripped[2:].strip()
    lowered = stripped.lower()
    if "traceback (most recent call last):" in lowered or lowered.startswith("file \""):
        return True
    if lowered.startswith("```"):
        return True
    raw_prefixes = ("stdout=", "stderr=", "stdout:", "stderr:", "stdout>", "stderr>")
    return any(lowered.startswith(prefix) for prefix in raw_prefixes)


def _validate_value(name: str, value: Any, allowed: set[str]) -> None:
    if not isinstance(value, str) or value not in allowed:
        raise MessageLintError(f"unknown {name}: {value!r}")
