#!/usr/bin/env python3
"""Smoke checks for C6 local install wiring."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from smoke_helpers import ROOT, assert_true, run_bin


def old_iso(hours: int = 3) -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=hours)).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def run_json(cmd: list[str], *, cwd: Path = ROOT, env: dict[str, str] | None = None) -> dict[str, object]:
    proc = subprocess.run(cmd, cwd=str(cwd), env=env, text=True, capture_output=True, check=False)
    if proc.returncode != 0:
        raise AssertionError(f"command failed: {' '.join(cmd)}\nstdout={proc.stdout}\nstderr={proc.stderr}")
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise AssertionError(f"command returned non-JSON output: {' '.join(cmd)}\nstdout={proc.stdout}") from exc


def assert_no_python_cache(root: Path) -> None:
    caches = [path for path in root.rglob("*") if path.name == "__pycache__" or path.suffix == ".pyc"]
    assert_true(not caches, f"installed runtime should not include Python cache artifacts: {caches[:3]}")


def assert_runner_error_marker(packet: dict[str, object]) -> None:
    assert_true(packet["ok"] is False and packet["wake_reason"] == "runner_error", "runner error should be deterministic JSON")
    runner = packet.get("runner", {})
    assert_true(isinstance(runner, dict) and runner.get("local_only") is True, "runner error should keep local-only marker")
    assert_true(runner.get("external_action_performed") is False, "runner error should report no external action")


def test_dev_bin_uses_runtime_contract() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        state_root = Path(tmp) / "state"
        result = run_bin("validate", "--sample-docs", state_root=state_root)
        assert_true(result["ok"] is True, "development bin should validate sample docs")


def test_package_file_contract() -> None:
    proc = subprocess.run(
        ["npm", "pack", "--dry-run", "--json"],
        cwd=str(ROOT),
        text=True,
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        raise AssertionError(f"npm pack dry-run failed\nstdout={proc.stdout}\nstderr={proc.stderr}")
    try:
        package_info = json.loads(proc.stdout)[0]
    except (IndexError, KeyError, json.JSONDecodeError) as exc:
        raise AssertionError(f"npm pack dry-run returned unexpected JSON\nstdout={proc.stdout}") from exc
    paths = {entry["path"] for entry in package_info["files"]}
    required = {
        "bin/converge",
        "converge/cli.py",
        "converge/recovery.py",
        "converge/schemas/workflow.schema.json",
        "converge/templates/worklog.md",
        "scripts/converge_watchdog_runner.py",
        "scripts/install-local.sh",
        "scripts/deploy-local.sh",
        "prompts/converge-watchdog.md",
        "openclaw.plugin.json",
        "tests/smoke/converge_install_wiring_smoke.py",
    }
    missing = sorted(required - paths)
    assert_true(not missing, f"npm package should include C6 runtime files, missing={missing}")
    assert_true(
        not any("__pycache__" in path or path.endswith(".pyc") for path in paths),
        "npm package should not include Python cache artifacts",
    )


def test_local_install_wires_cli_and_runner() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        install_root = root / "install" / "converge"
        bin_dir = root / "bin"
        plugin_dir = root / "plugin" / "openclaw-converge"
        workspace = root / "workspace"
        state_root = workspace / "state" / "converge"
        env = os.environ.copy()
        env.update(
            {
                "OPENCLAW_CONVERGE_INSTALL_ROOT": str(install_root),
                "OPENCLAW_CONVERGE_BIN_DIR": str(bin_dir),
                "OPENCLAW_CONVERGE_PLUGIN_DIR": str(plugin_dir),
                "OPENCLAW_WORKSPACE": str(workspace),
            }
        )
        unsafe_env = env.copy()
        unsafe_env["OPENCLAW_CONVERGE_INSTALL_ROOT"] = str(root / "unsafe-root")
        unsafe_install = subprocess.run(
            [str(ROOT / "scripts" / "install-local.sh")],
            cwd=str(ROOT),
            env=unsafe_env,
            text=True,
            capture_output=True,
            check=False,
        )
        assert_true(unsafe_install.returncode == 2, "install-local should reject unmanaged cleanup roots")
        unsafe_plugin_env = env.copy()
        unsafe_plugin_env["OPENCLAW_CONVERGE_PLUGIN_DIR"] = str(root / "plugin")
        unsafe_plugin_install = subprocess.run(
            [str(ROOT / "scripts" / "install-local.sh")],
            cwd=str(ROOT),
            env=unsafe_plugin_env,
            text=True,
            capture_output=True,
            check=False,
        )
        assert_true(unsafe_plugin_install.returncode == 2, "install-local should reject unmanaged plugin roots")
        source_cache = ROOT / "converge" / "modes" / "__pycache__" / "install_smoke.pyc"
        source_cache.parent.mkdir(parents=True, exist_ok=True)
        source_cache.write_bytes(b"cache")
        try:
            install = subprocess.run(
                [str(ROOT / "scripts" / "install-local.sh")],
                cwd=str(ROOT),
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )
            if install.returncode != 0:
                raise AssertionError(f"install-local failed on first attempt\nstdout={install.stdout}\nstderr={install.stderr}")

            installed_install = subprocess.run(
                [str(install_root / "scripts" / "install-local.sh")],
                cwd=str(install_root),
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )
            assert_true(installed_install.returncode == 2, "installed install-local should refuse to self-clobber")

            stale_pkg = install_root / "converge" / "stale_pkg" / "old.py"
            stale_script = install_root / "scripts" / "old_runner.py"
            stale_prompt = install_root / "prompts" / "old.md"
            stale_pkg.parent.mkdir(parents=True, exist_ok=True)
            stale_pkg.write_text("STALE = True\n", encoding="utf-8")
            stale_script.write_text("# stale\n", encoding="utf-8")
            stale_prompt.write_text("stale\n", encoding="utf-8")

            reinstall = subprocess.run(
                [str(ROOT / "scripts" / "install-local.sh")],
                cwd=str(ROOT),
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )
            if reinstall.returncode != 0:
                raise AssertionError(f"install-local failed on second attempt\nstdout={reinstall.stdout}\nstderr={reinstall.stderr}")
            assert_true(not stale_pkg.exists(), "reinstall should remove stale package files")
            assert_true(not stale_script.exists(), "reinstall should remove stale script files")
            assert_true(not stale_prompt.exists(), "reinstall should remove stale prompt files")
            assert_no_python_cache(install_root)
        finally:
            source_cache.unlink(missing_ok=True)

        deploy = subprocess.run(
            [str(ROOT / "scripts" / "deploy-local.sh")],
            cwd=str(ROOT),
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )
        if deploy.returncode != 0:
            raise AssertionError(f"deploy-local failed\nstdout={deploy.stdout}\nstderr={deploy.stderr}")
        converge_bin = bin_dir / "converge"
        runner = install_root / "scripts" / "converge_watchdog_runner.py"
        assert_true(converge_bin.exists(), "installed converge executable should exist")
        assert_true(os.access(converge_bin, os.X_OK), "installed converge executable should be executable")
        assert_true(runner.exists(), "installed watchdog runner should exist")
        assert_true((plugin_dir / "openclaw.plugin.json").exists(), "plugin manifest should be installed")
        plugin_manifest = json.loads((plugin_dir / "openclaw.plugin.json").read_text(encoding="utf-8"))
        assert_true((plugin_dir / plugin_manifest["main"]).exists(), "installed plugin manifest main should exist")
        assert_true((plugin_dir / "README.md").exists(), "installed plugin source should include README")
        assert_true((plugin_dir / "tests" / "smoke" / "converge_install_wiring_smoke.py").exists(), "installed plugin source should include smokes")
        assert_no_python_cache(plugin_dir)
        validate = run_json([str(converge_bin), "validate", "--sample-docs"], env=env)
        assert_true(validate["ok"] is True, "installed CLI should validate sample docs")
        route_parity = run_json(
            [
                str(converge_bin),
                "--state-root",
                str(state_root),
                "route-parity-check",
                "--owner-session-key",
                "session:installed-phase6",
                "--visible-delivery",
                '{"channel":"telegram","target":"demo"}',
            ],
            env=env,
        )
        assert_true(route_parity["ok"] is True, "installed CLI should run Phase 6 route parity dry-run gate")
        assert_true(
            route_parity["production_route_parity_proven"] is False,
            "installed Phase 6 route parity dry-run must not prove production parity",
        )
        dry_run_evidence = root / "phase6-dry-run-evidence.json"
        dry_run_evidence.write_text(
            json.dumps(
                {
                    "evidence_source": "command-dry-run",
                    "proof_level": "route_dry_run_gate",
                    "gateway_restart_performed": False,
                    "route_change_performed": False,
                    "deploy_or_install_performed": False,
                    "external_action_performed": False,
                    "cleanup_or_legacy_removal_performed": False,
                },
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        dry_run_verify = subprocess.run(
            [
                str(converge_bin),
                "--state-root",
                str(state_root),
                "route-parity-verify",
                "--evidence-file",
                str(dry_run_evidence),
            ],
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )
        assert_true(dry_run_verify.returncode == 1, "installed CLI should reject CLI-only Phase 6 evidence")
        assert_true(
            "CLI-only or command-adapter" in json.loads(dry_run_verify.stdout)["error"],
            "installed Phase 6 route parity verify should report CLI-only evidence rejection",
        )
        workflow = run_json(
            [
                str(converge_bin),
                "--state-root",
                str(state_root),
                "goal",
                "--text",
                "install wiring smoke",
            ],
            env=env,
        )
        assert_true(workflow["ok"] is True, "installed CLI should create/finalize a goal workflow")
        scan = run_json([str(converge_bin), "--state-root", str(state_root), "scan", "--json"], env=env)
        assert_true(scan["ok"] is True, "installed CLI scan should run")

        recovery_workflow = "installed-stale-conv"
        run_json(
            [
                str(converge_bin),
                "--state-root",
                str(state_root),
                "start",
                "--kind",
                "conv",
                "--text",
                "installed recovery smoke",
                "--workflow-id",
                recovery_workflow,
            ],
            env=env,
        )
        workflow_path = state_root / "workflows" / recovery_workflow / "workflow.json"
        workflow = json.loads(workflow_path.read_text(encoding="utf-8"))
        workflow["status"] = "running"
        workflow["last_activity_at"] = old_iso()
        workflow["stale_after_seconds"] = 1
        workflow_path.write_text(json.dumps(workflow, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        recovered = run_json(
            [str(converge_bin), "--state-root", str(state_root), "recover", "--workflow-id", recovery_workflow, "--holder", "smoke"],
            env=env,
        )
        assert_true(recovered.get("recovered") is True, "installed CLI should preserve recovery contract")

        fake_dir = root / "fake-bin"
        fake_dir.mkdir()
        fake_converge = fake_dir / "converge"
        fake_converge.write_text("#!/usr/bin/env bash\necho '{\"ok\": false, \"fake\": true}'\n", encoding="utf-8")
        fake_converge.chmod(0o755)
        runner_env = env.copy()
        runner_env.pop("OPENCLAW_CONVERGE_BIN", None)
        runner_env["PATH"] = f"{fake_dir}{os.pathsep}{runner_env.get('PATH', '')}"
        packet = run_json([str(runner), "--state-root", str(state_root), "--json"], env=runner_env)
        assert_true(packet["ok"] is True, "installed watchdog runner should emit a valid packet")
        assert_true(packet.get("runner", {}).get("local_only") is True, "runner should declare local-only policy")
        assert_true(
            packet.get("runner", {}).get("converge_bin") == str((install_root / "bin" / "converge").resolve()),
            "runner should prefer its package-local installed CLI over PATH",
        )
        missing_runner = subprocess.run(
            [str(runner), "--converge-bin", str(root / "missing-converge"), "--json"],
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )
        assert_true(missing_runner.returncode == 1, "runner should fail closed for missing converge bin")
        assert_runner_error_marker(json.loads(missing_runner.stdout))
        bad_exit = root / "bad-exit-converge"
        bad_exit.write_text("#!/usr/bin/env bash\necho 'boom' >&2\nexit 2\n", encoding="utf-8")
        bad_exit.chmod(0o755)
        bad_exit_runner = subprocess.run(
            [str(runner), "--converge-bin", str(bad_exit), "--json"],
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )
        assert_true(bad_exit_runner.returncode == 1, "runner should fail closed for nonzero converge")
        assert_runner_error_marker(json.loads(bad_exit_runner.stdout))
        bad_json = root / "bad-json-converge"
        bad_json.write_text("#!/usr/bin/env bash\necho 'not-json'\n", encoding="utf-8")
        bad_json.chmod(0o755)
        bad_json_runner = subprocess.run(
            [str(runner), "--converge-bin", str(bad_json), "--json"],
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )
        assert_true(bad_json_runner.returncode == 1, "runner should fail closed for invalid JSON")
        assert_runner_error_marker(json.loads(bad_json_runner.stdout))
        bad_shape = root / "bad-shape-converge"
        bad_shape.write_text("#!/usr/bin/env bash\necho '[]'\n", encoding="utf-8")
        bad_shape.chmod(0o755)
        bad_shape_runner = subprocess.run(
            [str(runner), "--converge-bin", str(bad_shape), "--json"],
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )
        assert_true(bad_shape_runner.returncode == 1, "runner should fail closed for non-object JSON")
        assert_runner_error_marker(json.loads(bad_shape_runner.stdout))


def main() -> None:
    test_dev_bin_uses_runtime_contract()
    test_package_file_contract()
    test_local_install_wires_cli_and_runner()
    print(json.dumps({"ok": True, "checked": ["dev-bin", "package-files", "local-install", "watchdog-runner"]}, indent=2))


if __name__ == "__main__":
    main()
