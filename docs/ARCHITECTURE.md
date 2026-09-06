# AegisFlow — Protocol Architecture

## Overview

AegisFlow is a consensus-governed execution control protocol for autonomous
agents. The Intelligent Contract (`contracts/AegisFlow.py`) is the entire
protocol: policy registry, agent registry, action intake, consensus pipeline,
decision derivation, deterministic enforcement, escalation, circuit breaker,
and audit ring.

```
            ┌────────────────────────────────────────────────┐
            │                AEGISFLOW CONTRACT              │
            │                                                │
 AGENT ────►│ submit_action ──► canonical hash, nonce,      │
 (operator) │                   pending exposure accounting  │
            │                                                │
            │ run_consensus (the ONLY nondet block)          │
            │   leader: fetch pinned registry (keccak verify) │
            │           → normalize evidence                 │
            │           → compute 9 deterministic facts     │
            │           → LLM labels 3 semantic criteria     │
            │   validators: same pipeline independently,     │
            │           compare FACTS + LABELS + EVIDENCE HASH│
            │   contract: derives verdict from consensus      │
            │             (hard gates → semantic gates →     │
            │              uncertainty → escalate threshold)  │
            │                                                │
            │ execute_action (deterministic enforcement)      │
            │   re-verify hash, policy version, nonce,        │
            │   budget, expiry, pause, single-execution      │
            │                                                │
            │ audit ring (bounded, all events)               │
            └────────────────────────────────────────────────┘
```

## Decision derivation order (fail-safe by construction)

```
REJECT  ← amount_limit_violation          (deterministic gate)
REJECT  ← budget_exceeded                 (deterministic gate)
REJECT  ← action_type_not_allowed         (deterministic gate)
REJECT  ← target_restricted               (deterministic gate)
REJECT  ← external_risk_flag (if policy says reject flagged)
REJECT  ← semantic:policy_intent FAIL     (LLM label, consensus-checked)
REJECT  ← semantic:evidence_safe FAIL
REJECT  ← semantic:action_proportionate FAIL
ESCALATE← evidence or LLM unavailable     (fail-safe, NEVER approve)
ESCALATE← new recipient needs verification
ESCALATE← any UNCERTAIN label             (uncertainty ≠ approval)
ESCALATE← amount above escalation threshold
APPROVE ← otherwise (all facts clean, all labels PASS)
```

Risk score = `max(objective_score_from_facts, llm_advisory)` — the LLM can
raise risk, never lower it. Buckets: 0-29 LOW, 30-59 MEDIUM, 60-79 HIGH,
80-100 CRITICAL.

## Consensus: leader/validator substance verification

The leader's free-text reasoning is never compared. Validators independently:

1. Re-fetch the **pinned** registry (immutable commit URL) and verify the
   on-chain keccak256 pin against the fetched bytes.
2. Recompute the identical nine deterministic facts (pure functions of
   storage + normalized evidence).
3. Re-run the same arbitration prompt (identical inputs: policy NL text,
   machine constraints, intent, facts, evidence entry).
4. Compare ONLY: the 9 fact booleans, the evidence hash, and the three
   criterion STATUS labels. LLM failure flags must agree as flags.

Divergence in any compared field → validator rejects the leader result.

## Storage model (GenVM-safe)

Uniform `TreeMap[str, str]` with canonical JSON (`sort_keys`,
separators=(",", ":")) values — the gltest-compatible pattern. Agents,
policies, actions, decisions (double-keyed by `action_id` and `action_hash`),
and a bounded audit ring (`A000001…`, 240 slots, oldest-slot overwrite).
Numeric storage uses `u256` fields for counters; all money is tracked in
integer milli-units (1 USDC = 1000) — no floats, ever.

## Enforcement invariants

- An approval is bound to the exact policy version that produced it;
  `update_policy` supersedes outstanding authorizations (S5).
- Nonces are monotonic per agent (assigned at submit; replay is impossible by
  construction — each action_id is also unique).
- `execute_action` recomputes the canonical action hash from stored fields and
  reverts on mismatch (tamper alarm).
- The protocol owner can PAUSE the protocol: submit and execute revert while
  paused; everything remains readable/auditable.
- EXPIRED sweeps are permissionless (`expire_action`), releasing pending
  exposure.

## Events

One indexed positional field + blob kwargs (live-chain topic-limit safe):
`ActionSubmittedEvent`, `DecisionEvent`, `ActionExecutedEvent`,
`ProtocolPausedEvent`.

## Frontend

Single-page dApp (`frontend/index.html` + the GenLayer SDK IIFE bundle) on
GitHub Pages. Burner wallets + `sim_fundAccount` faucet; all writes wait for
real ACCEPTED consensus and surface the leader receipt's execution result.
Live data is tagged LIVE CONSENSUS; the illustrative validator grid is
explicitly tagged DEMO MODE when per-validator votes are not exposed by the
receipt (honesty over theater).
