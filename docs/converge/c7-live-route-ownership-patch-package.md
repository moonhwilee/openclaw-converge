# C7 Live Route Ownership Patch Package

Date: 2026-05-27

Status: applied to workspace trigger policy; installed-copy synchronization and
post-change smoke evidence are required before calling the replacement complete.

This package defines the exact non-Gateway route ownership patch needed to move
the managed `/goal`, `/verify`, and `/conv` user-facing commands to the Converge
canonical backend. The workspace trigger-policy patch has been applied after the
owner approval text was received. No Gateway route table, cleanup/removal paths,
legacy deletion/movement/archive, external action, push, PR, or release are
changed by this document.

## Final Approval Required

Do not apply any patch below until the owner sends this exact approval text:

```text
I explicitly approve the operational live route replacement for exact commands /goal, /verify, and /conv to the Converge canonical backend. I do not approve /converge promotion, cleanup/removal execution, legacy deletion/movement/archive, deploy/apply/install, external action, push/PR/release, or Gateway restart unless separately stated with preflight evidence.
```

If the approval text differs, stop before applying changes.

## Exact Scope

In scope:

- `/goal`: exact case-sensitive command only.
- `/verify`: exact case-sensitive command only.
- `/conv`: exact case-sensitive command only.

Out of scope:

- `/converge` promotion or removal.
- `/plan`, `/cgoal`, `/cverify`, `/cconv`, or any other slash command.
- Gateway restart/reload unless separately approved after fresh preflight.
- cleanup/removal, legacy deletion, movement, archive, disable, deploy, install,
  external action, push, PR, or release.

## Current Installed Inventory

The final gate found no installed Gateway/plugin slash route for `/goal`,
`/verify`, `/conv`, or `/converge`. Current ownership is text-level workspace
orchestration:

| Command | Live file | Current owner | Current handler |
| --- | --- | --- | --- |
| `/goal` | `/Users/moon/.openclaw/workspace/AGENTS.md` | workspace contract | `workspace-contract.exact-goal-trigger` -> `/Users/moon/.openclaw/workspace/scripts/goalflow_start_goal.py` |
| `/verify` | `/Users/moon/.openclaw/workspace/skills/verification-convergence/SKILL.md` | verification-convergence skill | `verification-convergence.verify` |
| `/conv` | `/Users/moon/.openclaw/workspace/skills/verification-convergence/SKILL.md` | verification-convergence skill | `verification-convergence.conv` |
| `/converge` | `/Users/moon/.openclaw/workspace/skills/verification-convergence/SKILL.md` | legacy alias | `verification-convergence.converge_alias` |

Target ownership:

| Command | Target Converge mode | Target handler |
| --- | --- | --- |
| `/goal` | `goal` | `converge --state-root <state-root> goal --text <request> --owner-session-key <session> --visible-delivery <json>` |
| `/verify` | `verify` | `converge --state-root <state-root> verify --text <target> --owner-session-key <session> --visible-delivery <json>` |
| `/conv` | `conv` | `converge --state-root <state-root> conv --text <target> --owner-session-key <session> --visible-delivery <json>` |

## Applied Patch Summary And Rollback Hunks

The approved execution made the smallest live ownership edit in the workspace
trigger documents. The diff below is retained as an audit and rollback package.

### `/Users/moon/.openclaw/workspace/AGENTS.md`

Replace the current Exact `/goal` Trigger section:

```diff
 ## Exact /goal Trigger
 
-Treat only case-sensitive `^/goal(?:\s+|$)` as GoalFlow work. Do draft-only intake first:
-```bash
-/Users/moon/.openclaw/workspace/scripts/goalflow_start_goal.py --text '<raw message>'
-```
-
-Ask unresolved interview questions, present a final GoalSpec-style draft, and wait for explicit confirmation before creating GoalFlow records. Details: `docs/context/goalflow.md`.
+Treat only case-sensitive `^/goal(?:\s+|$)` as Converge-managed goal work.
+Route the command through the Converge canonical backend with owner session,
+visible delivery, and state root preserved:
+```bash
+converge --state-root '<state-root>' goal --text '<request without /goal>' --owner-session-key '<session>' --visible-delivery '<json>'
+```
+
+Preserve the same goal-safety semantics: draft/intake first, ask unresolved
+interview questions, present a final GoalSpec-style draft, and wait for explicit
+confirmation before executing the goal. Legacy GoalFlow records remain retained
+in place for historical state only; do not delete, move, archive, disable, or
+uninstall GoalFlow in this route replacement. Details: `docs/context/goalflow.md`.
```

Rollback hunk: restore the removed GoalFlow-owned section exactly as shown in
the minus side of this diff.

### `/Users/moon/.openclaw/workspace/skills/verification-convergence/SKILL.md`

Replace the current front-matter description and command ownership language:

```diff
 ---
 name: verification-convergence
-description: "High-intensity `/verify` and `/conv` protocol for audit, repair, or improvement through specialist subagent review, selective fixes, and evidence-based convergence across code, plans, docs, strategy, business, trading, and operations."
+description: "Legacy verification/convergence protocol reference. Exact `/verify` and `/conv` are Converge-managed commands; `/converge` remains a legacy alias boundary and is not promoted."
 user-invocable: false
 ---
 
 # Verification Convergence
 
-Use this skill when the user asks for `/verify ...`, `/conv ...`, or the legacy alias `/converge ...`.
+Exact `/verify ...` and `/conv ...` are owned by the Converge canonical backend.
+Use this skill only as retained legacy/reference material, for non-managed
+manual reviews that do not start with exact `/verify` or `/conv`, or when
+handling the legacy `/converge ...` alias boundary without promoting it.
 
-Primary user-facing commands:
+Retained command boundary:
 
-- `/verify <target>`: evidence-first verification. Defaults to `audit` mode.
-- `/conv <target>`: active convergence. Defaults to `repair` mode.
-- `/converge <target>`: legacy alias for `/conv`; accept it, but do not present it as the primary UX.
+- `/verify <target>`: Converge-owned; route to `converge verify`.
+- `/conv <target>`: Converge-owned; route to `converge conv`.
+- `/converge <target>`: legacy alias boundary only; do not promote it as primary UX.
```

Replace the current principle that treats all three commands as skill triggers:

```diff
-- Treat `/verify`, `/conv`, or the legacy `/converge` alias as an explicit request to use specialist subagents for this protocol; outside those triggers, do not apply this protocol automatically.
+- Treat exact `/verify` and `/conv` as Converge-managed commands, not direct
+  skill triggers. Treat `/converge` only as a retained legacy alias boundary
+  unless a separate owner-approved cleanup/removal operation changes it.
```

Rollback hunk: restore the removed verification-convergence trigger language
exactly as shown in the minus sides of these diffs.

## Execution Sequence

1. Confirm the exact final approval text.
2. Confirm the Converge repo and workspace trigger files are clean enough for a
   narrow change.
3. Record a rollback log path:
   `/Users/moon/.openclaw/state/converge/route-replacement/rollback-{approval_ref}-{yyyyMMddTHHmmssZ}.jsonl`.
4. Re-run pre-change smoke:
   - `npm run smoke:command-adapter`
   - `npm run smoke`
   - `python3 -m converge.cli --state-root /tmp/openclaw-converge-route-replacement command-dry-run --raw-message '/goal smoke' --owner-session-key session:telegram-direct --visible-delivery '{"channel":"telegram","target":"343580315"}'`
   - same command-dry-run pattern for `/verify smoke` and `/conv smoke`
5. Apply only the approved `/goal`, `/verify`, and `/conv` ownership hunks.
6. If the implementation requires Gateway restart/reload, stop, run
   `/Users/moon/.openclaw/workspace/scripts/gateway_restart_preflight.py`, show
   the result, and ask for separate restart/reload approval.
7. Start a fresh session or otherwise force trigger-context reload before
   post-change smoke.
8. Run post-change smoke and record delivery proof before calling the operation
   complete.

## Rollback Requirements

Rollback is explicit only. It is not an automatic fallback and must not silently
switch traffic back to legacy handlers.

Required rollback record fields:

- `approval_ref`
- `activated_at`
- `expires_at`, max 24h after activation
- `rollback_log_path`
- `legacy_route_scope`
- `excluded_route_scope`
- `activation_entries`
- `deactivation_entries`
- `post_rollback_smoke`

Legacy route scope:

- `/goal`: workspace AGENTS exact trigger plus GoalFlow draft intake.
- `/verify`: verification-convergence audit trigger.
- `/conv`: verification-convergence repair/improve trigger.

Excluded route scope:

- `/converge` alias.
- all unlisted slash commands.
- cleanup/removal and legacy state deletion/movement/archive.

## Retention Decision

Retain everything in place during live route replacement:

- GoalFlow state: retain for historical records.
- Work Ledger state: retain for outer recovery and non-Converge coordination.
- verification-convergence skill/artifacts: retain as legacy/reference material.
- chat-derived records: retain as context only, not authoritative proof.
- `/converge` alias history: retain; no promotion or removal in this operation.

Removal, archival, or disabling is a later cleanup/removal operation after stable
post-change smoke and a separate owner approval.

## Post-Change Smoke Checklist

Completion is blocked until all pass:

- Fresh-session `/goal` creates a Converge goal workflow and does not invoke
  `goalflow_start_goal.py` as the live owner.
- Fresh-session `/verify` creates a Converge verify workflow and does not invoke
  verification-convergence as the live owner.
- Fresh-session `/conv` creates a Converge conv workflow and does not invoke
  verification-convergence as the live owner.
- `/converge` is not promoted.
- Owner session key, visible delivery, state root, report proof, and
  complete-reported evidence propagate through Converge.
- No duplicate visible report is emitted by legacy and Converge handlers for the
  same user command.
- Rollback log exists and remains available until `expires_at`.

## Abort Conditions

Abort immediately if any condition is true:

- final approval text is missing or not exact
- route scope includes anything beyond `/goal`, `/verify`, and `/conv`
- `/converge` is promoted or silently changed
- a real Gateway/plugin route appears and is not inventoried with exact
  path/key/handler ID first
- rollback expiry or log path is missing
- automatic fallback is requested
- retention changes to delete/move/archive/disable
- pre-change smoke fails
- Gateway restart/reload is required but separate restart/reload approval is not
  obtained after passing preflight
- post-change smoke fails
- cleanup/removal, deploy/apply/install, external action, push, PR, or release is
  requested in the same operation

## Current State

The owner approval text was received and the workspace trigger ownership patch
was applied for exact `/goal`, `/verify`, and `/conv`, with `/converge` still
excluded.

No-Go for completion until the installed Converge CLI and installed
`verification-convergence` skill copy are synchronized or explicitly proven not
to affect runtime, and post-change smoke proves Converge-only handling without a
duplicate legacy report.
