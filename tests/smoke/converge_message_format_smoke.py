#!/usr/bin/env python3
"""Smoke coverage for Slice 2 visible message formatting."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from converge.messages import (  # noqa: E402
    MessageLintError,
    MODE_LABELS,
    format_final,
    format_round_start,
    format_round_summary,
    format_start,
    lint_visible,
)


def run(*args: str, state_root: Path) -> dict:
    result = subprocess.run(
        [sys.executable, "-m", "converge.cli", "--state-root", str(state_root), *args],
        cwd=str(ROOT),
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise AssertionError(f"command failed: {' '.join(args)}\nstdout={result.stdout}\nstderr={result.stderr}")
    return json.loads(result.stdout)


def assert_true(value: bool, message: str) -> None:
    if not value:
        raise AssertionError(message)


def assert_lint_error(fn, message: str) -> None:
    try:
        fn()
    except MessageLintError:
        return
    raise AssertionError(message)


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="converge-message-smoke-") as tmp:
        state_root = Path(tmp)
        workflow = run(
            "start",
            "--kind",
            "conv",
            "--text",
            "Converge message formatter",
            "--workflow-id",
            "conv-message-smoke",
            "--visible-delivery",
            '{"channel":"telegram","target":"test"}',
            state_root=state_root,
        )["workflow"]
        workflow["approval_boundaries"] = ["local files only", "no deploy"]

        start_text = format_start(workflow)
        assert_true(start_text.startswith("▶ Convergence start"), "start marker mismatch")
        assert_true("Boundary:" in start_text, "start boundary missing")
        assert_lint_error(lambda: lint_visible("▶ Convergence start"), "bare start marker should fail")
        assert_lint_error(
            lambda: lint_visible("▶ Convergence start\nWorkflow:\nObjective: obj\nBoundary:\n- local only"),
            "empty workflow should fail",
        )
        assert_lint_error(
            lambda: lint_visible("▶ Convergence start\nBoundary:\n- local only\nWorkflow: wf\nObjective: obj"),
            "start sections should be ordered",
        )

        round_start = format_round_start(
            workflow,
            {
                "round": 2,
                "target": ["messages.py"],
                "focus": ["visible report shape"],
                "gate": ["lint smoke passes"],
            },
        )
        assert_true(round_start.startswith("▶ Round 2 start"), "round start marker mismatch")
        assert_true("Target:" in round_start and "Focus:" in round_start and "Gate:" in round_start, "round start sections missing")
        assert_lint_error(lambda: lint_visible("▶ Round 2 start\n\nTarget:\n- target"), "incomplete round start should fail")
        assert_lint_error(
            lambda: lint_visible("▶ Round two start\n\nTarget:\n- target\n\nFocus:\n- focus\n\nGate:\n- gate"),
            "round start marker number must be numeric",
        )
        assert_lint_error(
            lambda: lint_visible("▶ Round 0 start\n\nTarget:\n- target\n\nFocus:\n- focus\n\nGate:\n- gate"),
            "round start marker number must be positive",
        )

        round_summary = format_round_summary(
            workflow,
            {
                "round": 2,
                "verification_result": "continuing",
                "original_target": "pass",
                "patch_regression": "none",
                "found": ["message formatter missing"],
                "accepted": ["add formatter"],
                "rejected_deferred": ["slash migration"],
                "checked": ["docs/converge/implementation-structure.md"],
                "next": ["run smoke"],
            },
        )
        assert_true(round_summary.startswith("■ Round 2 summary"), "round summary marker mismatch")
        assert_true("Rejected / Deferred:" in round_summary, "round summary rejected/deferred missing")
        assert_lint_error(
            lambda: lint_visible(
                "\n".join(
                    [
                        "■ Round 2 summary",
                        "",
                        "Status:",
                        "- Verification result: almost_done",
                        "- Original target: pass",
                        "- Patch regression: none",
                        "",
                        "Found:",
                        "- none",
                        "",
                        "Accepted:",
                        "- none",
                        "",
                        "Rejected / Deferred:",
                        "- none",
                        "",
                        "Checked:",
                        "- checked",
                        "",
                        "Next:",
                        "- none",
                    ]
                )
            ),
            "hand-built round summary unknown vocab should fail",
        )
        assert_lint_error(
            lambda: lint_visible(
                "\n".join(
                    [
                        "■ Round two summary",
                        "",
                        "Status:",
                        "- Verification result: continuing",
                        "- Original target: pass",
                        "- Patch regression: none",
                        "",
                        "Found:",
                        "- none",
                        "",
                        "Accepted:",
                        "- none",
                        "",
                        "Rejected / Deferred:",
                        "- none",
                        "",
                        "Checked:",
                        "- checked",
                        "",
                        "Next:",
                        "- none",
                    ]
                )
            ),
            "round summary marker number must be numeric",
        )
        assert_lint_error(
            lambda: lint_visible(
                "\n".join(
                    [
                        "■ Round 2 summary",
                        "",
                        "Status:",
                        "- Verification result: continuing",
                        "- Original target: pass",
                        "- Patch regression: none",
                        "- Extra status: ignored",
                        "",
                        "Found:",
                        "- none",
                        "",
                        "Accepted:",
                        "- none",
                        "",
                        "Rejected / Deferred:",
                        "- none",
                        "",
                        "Checked:",
                        "- checked",
                        "",
                        "Next:",
                        "- none",
                    ]
                )
            ),
            "round summary extra status fields should fail",
        )
        assert_lint_error(
            lambda: lint_visible(
                "\n".join(
                    [
                        "■ Round 2 summary",
                        "",
                        "Status:",
                        "- Verification result: continuing",
                        "- Original target: pass",
                        "- Patch regression: none",
                        "",
                        "Found:",
                        "- none",
                        "",
                        "Accepted:",
                        "- none",
                        "",
                        "Rejected / Deferred:",
                        "- none",
                        "",
                        "Checked:",
                        "- checked",
                        "",
                        "Next:",
                        "- none",
                        "Raw tail:",
                    ]
                )
            ),
            "round summary trailing non-bullet content should fail",
        )
        assert_lint_error(
            lambda: format_round_summary(
                workflow,
                {
                    "round": 2,
                    "verification_result": "almost_done",
                    "original_target": "pass",
                    "patch_regression": "none",
                },
            ),
            "unknown round verification result should fail",
        )
        assert_lint_error(
            lambda: format_round_summary(
                workflow,
                {
                    "round": 2,
                    "verification_result": "continuing",
                    "patch_regression": "none",
                },
            ),
            "missing original target should fail",
        )
        assert_lint_error(
            lambda: format_round_summary(
                workflow,
                {
                    "round": 2,
                    "verification_result": "continuing",
                    "original_target": "pass",
                    "patch_regression": "mostly_safe",
                },
            ),
            "unknown patch regression result should fail",
        )

        workflow["final_status"] = {
            "result": "pass_with_risks",
            "done": ["formatter implemented"],
            "checked": ["message formatter smoke"],
            "residuals": {
                "blocking_remaining": [],
                "accepted_risks": ["mode handlers will fill richer data later"],
                "implementation_backlog": ["wire formatter into future mode commands"],
                "deferred_scope": ["slash command migration"],
            },
        }
        final_text = format_final(workflow)
        assert_true(final_text.startswith("■ Convergence final"), "final marker mismatch")
        assert_true("Remaining:" in final_text, "remaining container missing")
        assert_true("- Blocking remaining:" in final_text, "blocking bucket missing")
        assert_true("- Accepted risks:" in final_text, "accepted risk bucket missing")
        assert_true("- Implementation backlog:" in final_text, "backlog bucket missing")
        assert_true("- Deferred scope:" in final_text, "deferred bucket missing")
        assert_true("mode handlers will fill richer data later" in final_text, "accepted risk item missing")

        for kind, label in MODE_LABELS.items():
            mode_workflow = dict(workflow)
            mode_workflow["kind"] = kind
            mode_workflow[f"{kind}_state"] = {}
            mode_workflow["final_status"] = {
                "result": "pass",
                "done": ["done"],
                "checked": ["checked"],
                "residuals": {
                    "blocking_remaining": [],
                    "accepted_risks": [],
                    "implementation_backlog": [],
                    "deferred_scope": [],
                },
            }
            assert_true(format_start(mode_workflow).startswith(f"▶ {label} start"), f"{kind} start marker mismatch")
            assert_true(format_final(mode_workflow).startswith(f"■ {label} final"), f"{kind} final marker mismatch")

        empty_remaining = dict(workflow)
        empty_remaining["final_status"] = {
            "result": "pass",
            "done": ["done"],
            "checked": ["checked"],
            "residuals": {
                "blocking_remaining": [],
                "accepted_risks": [],
                "implementation_backlog": [],
                "deferred_scope": [],
            },
        }
        assert_true("Remaining: none" in format_final(empty_remaining), "empty residuals should render Remaining: none")

        string_final = dict(workflow)
        string_final["final_status"] = "pass"
        assert_lint_error(lambda: format_final(string_final), "string final_status should fail")

        verdict_alias_final = dict(workflow)
        verdict_alias_final["final_status"] = {
            "verdict": "pass",
            "done": ["done"],
            "checked": ["checked"],
            "residuals": {},
        }
        assert_lint_error(lambda: format_final(verdict_alias_final), "verdict final_status alias should fail")

        blocked_pass = dict(workflow)
        blocked_pass["final_status"] = {
            "result": "pass",
            "done": ["done"],
            "checked": ["checked"],
            "residuals": {
                "blocking_remaining": ["unresolved blocker"],
                "accepted_risks": [],
                "implementation_backlog": [],
                "deferred_scope": [],
            },
        }
        assert_lint_error(lambda: format_final(blocked_pass), "pass with blocking remaining should fail")
        assert_lint_error(
            lambda: lint_visible(
                "\n".join(
                    [
                        "■ Convergence final",
                        "",
                        "Status:",
                        "- Result: pass",
                        "",
                        "Done:",
                        "- done",
                        "",
                        "Checked:",
                        "- checked",
                        "",
                        "Remaining:",
                        "- Blocking remaining:",
                        "  - unresolved blocker",
                        "- Accepted risks:",
                        "  - none",
                        "- Implementation backlog:",
                        "  - none",
                        "- Deferred scope:",
                        "  - none",
                    ]
                )
            ),
            "lint_visible should reject pass with blocking remaining",
        )
        assert_lint_error(
            lambda: lint_visible(
                "\n".join(
                    [
                        "■ Convergence final",
                        "",
                        "Status:",
                        "- Result: pass",
                        "",
                        "Done:",
                        "- done",
                        "",
                        "Checked:",
                        "- checked",
                        "",
                        "Remaining: none",
                        "- Blocking remaining:",
                        "  - hidden blocker",
                        "- Accepted risks:",
                        "  - none",
                        "- Implementation backlog:",
                        "  - none",
                        "- Deferred scope:",
                        "  - none",
                    ]
                )
            ),
            "Remaining: none mixed with residual buckets should fail",
        )
        assert_lint_error(
            lambda: lint_visible(
                "\n".join(
                    [
                        "■ Convergence final",
                        "",
                        "Status:",
                        "- Result: pass",
                        "",
                        "Done:",
                        "- done",
                        "",
                        "Checked:",
                        "- checked",
                        "",
                        "Remaining:",
                        "- Blocking remaining:",
                        "  - none",
                        "- Accepted risks:",
                        "  - none",
                        "- Implementation backlog:",
                        "  - none",
                        "- Deferred scope:",
                        "  - none",
                        "- Surprise bucket:",
                        "  - hidden item",
                    ]
                )
            ),
            "unknown residual buckets in visible text should fail",
        )
        assert_lint_error(
            lambda: lint_visible(
                "\n".join(
                    [
                        "■ Convergence final",
                        "",
                        "Status:",
                        "",
                        "Done:",
                        "- Result: pass",
                        "",
                        "Checked:",
                        "- checked",
                        "",
                        "Remaining: none",
                    ]
                )
            ),
            "final result outside Status should fail",
        )
        assert_lint_error(
            lambda: lint_visible(
                "\n".join(
                    [
                        "■ Convergence final",
                        "",
                        "Status:",
                        "- Result: pass",
                        "- Result: blocked",
                        "",
                        "Done:",
                        "- done",
                        "",
                        "Checked:",
                        "- checked",
                        "",
                        "Remaining: none",
                    ]
                )
            ),
            "duplicate final result should fail",
        )
        assert_lint_error(
            lambda: lint_visible(
                "\n".join(
                    [
                        "■ Convergence final",
                        "",
                        "- Note: Status: Done: Checked:",
                        "",
                        "- Result: pass",
                        "",
                        "Remaining: none",
                    ]
                )
            ),
            "final section headers must be exact lines",
        )
        assert_lint_error(
            lambda: lint_visible(
                "\n".join(
                    [
                        "■ Convergence final",
                        "",
                        "Status:",
                        "- Result: pass",
                        "",
                        "Done:",
                        "",
                        "Checked:",
                        "- checked",
                        "",
                        "Remaining: none",
                    ]
                )
            ),
            "empty Done section should fail",
        )
        assert_lint_error(
            lambda: lint_visible(
                "\n".join(
                    [
                        "■ Convergence final",
                        "",
                        "Status:",
                        "- Result: pass",
                        "",
                        "Done:",
                        "- done",
                        "",
                        "Checked:",
                        "",
                        "Remaining: none",
                    ]
                )
            ),
            "empty Checked section should fail",
        )
        assert_lint_error(
            lambda: lint_visible(
                "\n".join(
                    [
                        "■ Convergence final",
                        "",
                        "Status:",
                        "- Result: pass",
                        "",
                        "Done:",
                        "- done",
                        "",
                        "Checked:",
                        "- checked",
                        "",
                        "Remaining:",
                        "- Blocking remaining:",
                        "  - none",
                        "unresolved blocker",
                        "- Accepted risks:",
                        "  - none",
                        "- Implementation backlog:",
                        "  - none",
                        "- Deferred scope:",
                        "  - none",
                    ]
                )
            ),
            "unbucketed residual text should fail",
        )

        unknown_verdict = dict(workflow)
        unknown_verdict["final_status"] = {
            "result": "mostly_ok",
            "done": ["done"],
            "checked": ["checked"],
            "residuals": {},
        }
        assert_lint_error(lambda: format_final(unknown_verdict), "unknown verdict should fail")
        complete_round_value = dict(workflow)
        complete_round_value["final_status"] = {
            "result": "complete_pass",
            "done": ["done"],
            "checked": ["checked"],
            "residuals": {},
        }
        assert_lint_error(lambda: format_final(complete_round_value), "complete_* should not be accepted as a final verdict")
        assert_lint_error(
            lambda: lint_visible(
                "\n".join(
                    [
                        "■ Convergence final",
                        "",
                        "Status:",
                        "- Result: mostly_ok",
                        "",
                        "Done:",
                        "- done",
                        "",
                        "Checked:",
                        "- checked",
                        "",
                        "Remaining: none",
                    ]
                )
            ),
            "lint_visible should reject unknown final verdicts",
        )

        assert_lint_error(lambda: lint_visible("| a | b |\n| - | - |"), "markdown table should fail")
        assert_lint_error(lambda: lint_visible("■ Convergence final\nTraceback (most recent call last):"), "raw traceback should fail")
        assert_lint_error(lambda: lint_visible("■ Convergence final\nSTDERR: bad"), "stderr colon raw output should fail")
        assert_lint_error(lambda: lint_visible("■ Convergence final\n- stderr: bad"), "bulleted stderr raw output should fail")
        assert_lint_error(lambda: lint_visible("■ Convergence final\n- stdout=bad"), "bulleted stdout raw output should fail")
        lint_visible(
            "\n".join(
                [
                    "■ Round 2 summary",
                    "",
                    "Status:",
                    "- Verification result: continuing",
                    "- Original target: pass",
                    "- Patch regression: none",
                    "",
                    "Found:",
                    "- none",
                    "",
                    "Accepted:",
                    "- none",
                    "",
                    "Rejected / Deferred:",
                    "- none",
                    "",
                    "Checked:",
                    "- Checked stderr: no errors",
                    "",
                    "Next:",
                    "- none",
                ]
            )
        )
        assert_lint_error(lambda: lint_visible("■ Convergence final\n```python\nprint('raw')\n```"), "generic fenced raw output should fail")

    print(json.dumps({"ok": True, "checked": "message formatter"}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
