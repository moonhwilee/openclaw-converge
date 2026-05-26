#!/usr/bin/env python3
"""Deterministic Converge watchdog runner.

The runner stays local-only: it executes the installed or development
``converge watchdog-check`` command and emits the resulting JSON packet. Waking
sessions, Gateway restart, slash routing, and external delivery remain outside
the C6 install-wiring boundary.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path


def default_converge_bin() -> str:
    configured = os.environ.get("OPENCLAW_CONVERGE_BIN")
    if configured:
        return configured
    local_bin = Path(__file__).resolve().parents[1] / "bin" / "converge"
    if local_bin.exists():
        return str(local_bin.resolve())
    configured_bin_dir = os.environ.get("OPENCLAW_CONVERGE_BIN_DIR")
    if configured_bin_dir:
        configured_bin = Path(configured_bin_dir).expanduser() / "converge"
        if configured_bin.exists():
            return str(configured_bin.resolve())
    home_bin = Path.home() / ".openclaw" / "bin" / "converge"
    if home_bin.exists():
        return str(home_bin.resolve())
    discovered = shutil.which("converge")
    if discovered:
        return discovered
    return str(Path.home() / ".openclaw" / "bin" / "converge")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run one local Converge watchdog check.")
    parser.add_argument("--converge-bin", default=default_converge_bin())
    parser.add_argument("--state-root")
    parser.add_argument("--include-clean", action="store_true")
    parser.add_argument("--json", action="store_true", help="Accepted for command consistency; output is always JSON.")
    return parser


def runner_metadata(args: argparse.Namespace) -> dict[str, object]:
    return {
        "local_only": True,
        "converge_bin": args.converge_bin,
        "external_action_performed": False,
    }


def runner_error_packet(args: argparse.Namespace, cmd: list[str], error: str) -> dict[str, object]:
    return {
        "ok": False,
        "status": "error",
        "needs_wake": True,
        "wake_reason": "runner_error",
        "policy": "local-only runner; inspect before any user-visible or external action",
        "command": cmd,
        "error": error,
        "runner": runner_metadata(args),
    }


def run_watchdog(args: argparse.Namespace) -> dict[str, object]:
    cmd = [args.converge_bin]
    if args.state_root:
        cmd.extend(["--state-root", args.state_root])
    cmd.extend(["watchdog-check", "--json"])
    if args.include_clean:
        cmd.append("--include-clean")
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    except OSError as exc:
        return runner_error_packet(args, cmd, str(exc))
    if proc.returncode != 0:
        return runner_error_packet(args, cmd, (proc.stderr or proc.stdout or f"converge exited {proc.returncode}").strip())
    try:
        result = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        return runner_error_packet(args, cmd, f"invalid converge watchdog-check JSON: {exc}")
    if not isinstance(result, dict):
        return runner_error_packet(args, cmd, f"invalid converge watchdog-check JSON shape: {type(result).__name__}")
    result.setdefault("runner", {})
    if isinstance(result["runner"], dict):
        result["runner"].update(
            {
                **runner_metadata(args),
            }
        )
    return result


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    result = run_watchdog(args)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    if result.get("ok") is False:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
