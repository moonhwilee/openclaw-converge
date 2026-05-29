#!/usr/bin/env python3
"""Deterministic Converge watchdog runner.

The runner stays local-only: it executes the installed or development
``converge watchdog-check`` command, appends a JSONL heartbeat record, and
emits the resulting JSON packet. Waking sessions, Gateway restart, slash
routing, and external delivery remain outside the runner boundary.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


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
    parser.add_argument(
        "--log-path",
        default=os.environ.get("OPENCLAW_CONVERGE_WATCHDOG_LOG", str(Path.home() / ".openclaw" / "logs" / "converge-watchdog.jsonl")),
        help="JSONL heartbeat log path.",
    )
    parser.add_argument(
        "--runner-state-file",
        default=os.environ.get(
            "OPENCLAW_CONVERGE_WATCHDOG_STATE",
            str(Path.home() / ".openclaw" / "state" / "converge" / "watchdog-runner-state.json"),
        ),
        help="State file used for the latest heartbeat summary.",
    )
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


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def isoformat_z(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def actionable_items(packet: dict[str, object]) -> list[dict[str, object]]:
    recoveries = packet.get("recoveries")
    if isinstance(recoveries, list):
        return [item for item in recoveries if isinstance(item, dict)]
    if packet.get("needs_wake"):
        return [
            {
                "wake_reason": packet.get("wake_reason", "watchdog_needs_wake"),
                "status": packet.get("status"),
                "error": packet.get("error"),
            }
        ]
    return []


def packet_fingerprint(packet: dict[str, object]) -> str:
    items: list[dict[str, object]] = []
    for item in actionable_items(packet):
        items.append(
            {
                "workflow_id": item.get("workflow_id"),
                "status": item.get("status"),
                "wake_reason": item.get("wake_reason"),
                "owner_session_key": item.get("owner_session_key"),
                "visible_delivery": item.get("visible_delivery"),
                "source_of_truth": item.get("source_of_truth"),
                "error": item.get("error"),
            }
        )
    if not items:
        items = [{"status": packet.get("status"), "needs_wake": bool(packet.get("needs_wake"))}]
    material = json.dumps(items, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def write_runner_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=str(path.parent), delete=False) as handle:
        json.dump(state, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
        temp_name = handle.name
    Path(temp_name).replace(path)


def append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")


def annotate_heartbeat(args: argparse.Namespace, packet: dict[str, object]) -> dict[str, object]:
    now = utc_now()
    log_path = Path(args.log_path).expanduser()
    state_file = Path(args.runner_state_file).expanduser()
    fingerprint = packet_fingerprint(packet)
    actionable = bool(packet.get("needs_wake"))

    heartbeat = {
        "checked_at": isoformat_z(now),
        "needs_wake": actionable,
        "status": packet.get("status"),
        "fingerprint": fingerprint,
        "log_path": str(log_path),
        "state_file": str(state_file),
    }
    packet.setdefault("runner", {})
    if isinstance(packet["runner"], dict):
        packet["runner"].update({"heartbeat": heartbeat, **runner_metadata(args)})

    log_record = {
        "checked_at": heartbeat["checked_at"],
        "status": packet.get("status"),
        "ok": packet.get("ok"),
        "needs_wake": actionable,
        "fingerprint": fingerprint,
    }
    append_jsonl(log_path, log_record)

    next_state = {
        "last_checked_at": heartbeat["checked_at"],
        "last_fingerprint": fingerprint,
        "last_status": packet.get("status"),
        "last_needs_wake": actionable,
        "last_recovery_count": len(actionable_items(packet)),
    }
    write_runner_state(state_file, next_state)
    return packet


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
    result = annotate_heartbeat(args, run_watchdog(args))
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    if result.get("ok") is False:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
