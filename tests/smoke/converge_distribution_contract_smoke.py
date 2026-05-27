#!/usr/bin/env python3
"""Smoke coverage for Converge distribution/install routing contract."""

from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path

try:
    from smoke_helpers import ROOT, VISIBLE_DELIVERY, assert_true
except ModuleNotFoundError:
    from tests.smoke.smoke_helpers import ROOT, VISIBLE_DELIVERY, assert_true


MANAGED_COMMANDS = {
    "/goal": "goal",
    "/verify": "verify",
    "/conv": "conv",
}
RETIRED_PRODUCT_COMMANDS = ["/cgoal", "/cverify", "/cconv", "/cplan"]


def run_bin(state_root: Path, raw_message: str) -> dict:
    result = subprocess.run(
        [
            str(ROOT / "bin" / "converge"),
            "--state-root",
            str(state_root),
            "command-dry-run",
            "--raw-message",
            raw_message,
            "--owner-session-key",
            "session:distribution-smoke",
            "--visible-delivery",
            VISIBLE_DELIVERY,
            "--json",
        ],
        cwd=str(ROOT),
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise AssertionError(
            f"bin/converge failed for {raw_message!r}\nstdout={result.stdout}\nstderr={result.stderr}"
        )
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise AssertionError(f"bin/converge returned non-JSON for {raw_message!r}: {result.stdout}") from exc


def assert_bin_routes_managed_commands() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        state_root = Path(tmp)
        for command, expected_mode in MANAGED_COMMANDS.items():
            packet = run_bin(state_root, f"{command} distribution route smoke")
            assert_true(packet["ok"] is True, f"{command} dry-run should succeed")
            assert_true(packet["route"]["current_command"] == command, f"{command} should stay exact command")
            assert_true(
                packet["route"]["converge_mode"] == expected_mode,
                f"{command} should route to Converge {expected_mode}",
            )
            assert_true(packet["route"]["state_root"] == str(state_root), f"{command} should preserve state root")
            assert_true(packet["route"]["owner_session_key"] == "session:distribution-smoke", f"{command} should preserve owner session")
            assert_true(packet["route"]["visible_delivery"] == json.loads(VISIBLE_DELIVERY), f"{command} should preserve visible delivery")
            assert_true(packet["workflow_created"] is False, f"{command} dry-run should not create workflow state")
            assert_true(packet["live_route_changed"] is False, f"{command} dry-run should not change live routes")
            assert_true(packet["external_action_performed"] is False, f"{command} dry-run should not perform external action")
            assert_true(
                "verification-convergence artifacts"
                in packet["route_retirement_plan"]["logging_proof"]["legacy_sources_not_authoritative_for_converge_work"],
                "retired verification-convergence artifacts should remain non-authoritative",
            )


def assert_distribution_files_hide_retired_skill() -> None:
    package = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))
    files = package.get("files", [])
    assert_true(isinstance(files, list), "package files should be a list")
    assert_true(not any("verification-convergence" in item for item in files), "package must not ship the retired verification-convergence skill")
    assert_true(not any(item.startswith("skills") for item in files), "package should not ship skill directories")

    manifest = json.loads((ROOT / "openclaw.plugin.json").read_text(encoding="utf-8"))
    assert_true("verification-convergence" not in json.dumps(manifest), "plugin manifest must not expose retired skill routing")

    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    for command in MANAGED_COMMANDS:
        assert_true(command in readme, f"README should document {command} route contract")
    assert_true("manual shell checks" in readme, "README should forbid manual fallback before Converge routing")
    assert_true("routing failure" in readme, "README should require routing failure when CLI is unavailable")
    assert_true("verification-convergence" in readme and "retired" in readme, "README should mark verification-convergence retired")
    for command in RETIRED_PRODUCT_COMMANDS:
        assert_true(command in readme, f"README should explicitly forbid retired {command} product command")


def main() -> None:
    assert_bin_routes_managed_commands()
    assert_distribution_files_hide_retired_skill()
    print(json.dumps({"ok": True, "checked": ["bin exact command routing", "retired skill hidden from package", "README fallback guard"]}))


if __name__ == "__main__":
    main()
