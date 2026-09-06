# AegisFlow

**Autonomous Agents. Consensus-Governed Actions.**

An execution control protocol for autonomous agents, powered by GenLayer consensus.
Built for the GenLayer Agent Tank Hackathon — **Autonomous Protocols** track.

AegisFlow is the missing layer between autonomous AI agents and high-impact
on-chain actions:

```
AGENT → INTENT → POLICY FACTS → GENLAYER CONSENSUS → DECISION
      → DETERMINISTIC ENFORCEMENT → EXECUTION / BLOCK / ESCALATE → AUDIT TRAIL
```

Every agent action intent must pass decentralized AI validation against a
human-written natural-language policy and pinned external evidence before it can
be authorized. The consensus verdict is one of `APPROVE` / `REJECT` / `ESCALATE`
— and only `APPROVE`, re-verified by deterministic contract logic, can ever
authorize execution.

---

## Why GenLayer is essential here

The question this protocol answers:

> *"What decision must not depend on my frontend, my backend, or a single
> centralized AI?"*

**Whether an autonomous agent's action complies with its human-defined policy
under current external context.**

- A normal smart contract can enforce `amount <= 50` — it **cannot** judge
  whether *"paying 25 USDC because the agent 'feels like it'"* violates a
  policy that permits only infrastructure payments.
- A single centralized AI gatekeeper means whoever controls the model controls
  every agent's actions.
- AegisFlow puts that judgment **inside an Intelligent Contract**: the GenLayer
  leader proposes, every validator independently re-fetches the pinned evidence
  registry, recomputes the same deterministic facts, re-runs the arbitration
  prompt, and compares only the substance (facts, criterion labels, evidence
  hash). The final verdict is **derived by contract code** from
  consensus-checked inputs.

## Architecture — "LLM labels, CONTRACT derives"

Defense in depth, following the pattern proven by portal-accepted GenLayer
contracts:

1. **Deterministic facts first.** The contract computes nine objective risk
   components as pure functions of on-chain state + the fetched evidence:
   `amount_limit_violation`, `budget_exceeded`, `recipient_is_new`,
   `cooldown_active`, `action_type_allowed`, `target_restricted`,
   `external_risk_flag`, `exposure_high`, `evidence_available`.
   Arithmetic never goes to the LLM.
2. **LLM consensus judges the semantics.** Three criteria that no Solidity
   contract can evaluate: `policy_intent_compliant` (does the stated reason
   satisfy the natural-language policy?), `evidence_safe`, and
   `action_proportionate`. The LLM returns only PASS/FAIL/UNCERTAIN labels +
   short justifications and an advisory risk score.
3. **The contract derives the verdict.** Any hard deterministic violation →
   REJECT. Any criterion FAIL → REJECT. New recipient / UNCERTAIN / evidence or
   LLM unavailable / above escalation threshold → ESCALATE. Otherwise → APPROVE.
   **A fooled or hallucinating LLM can never approve a limit violation or a
   flagged target** — the deterministic gates clamp it. The LLM's advisory risk
   score can only *raise* the final risk score, never lower it.
4. **Fail-safe everywhere.** Registry fetch failure, keccak mismatch, malformed
   LLM JSON, missing criteria — every failure mode maps to a well-formed
   decision that never approves (ESCALATE with a machine-readable reason, or
   REJECT when a hard fact already fired).
5. **Deterministic enforcement + replay protection.** `execute_action`
   re-verifies everything: action hash, policy version (a v1 approval **dies**
   when the policy is updated to v2), nonce, budget, expiry, protocol pause
   state, single-execution. Consensus alone can never move value.

## The five live demo scenarios

All run through **real GenLayer consensus** on Studionet:

| # | Scenario | Input | Expected |
|---|----------|-------|----------|
| S1 | Safe payment | 25 USDC to known infra provider | APPROVE → EXECUTE |
| S2 | Budget violation | 100 USDC vs 50 USDC single limit | REJECT (deterministic) |
| S3 | New recipient | 20 USDC to unverified vendor | ESCALATE → human resolution |
| S4 | Flagged evidence | target flagged as phishing drainer | REJECT (evidence gate) |
| S5 | Policy update | v1 approval vs policy v2 | BLOCKED at enforcement |

Plus: emergency circuit breaker (owner pause), daily budget accounting with
day-window rollover, escalation queue with human approve/reject, audit ring
(bounded storage), policy versioning with content hashes, and nonce/replay
protection.

## External evidence (honest sourcing)

A single pinned JSON registry (`data/evidence_registry.json`) served from
`raw.githubusercontent.com` at an **immutable commit URL** set at deployment.
The leader and every validator fetch the identical bytes and verify the
**keccak256 content hash on-chain** against the deployment pin on every run.
Only stable normalized facts are extracted (`known_recipient`, `risk_flag`,
`label`, `status`) — no timestamps, no raw HTML, no volatile fields. The
`evidence_hash` stored with each decision is the keccak256 of the exact
normalized entry used, so the UI shows precisely what consensus saw.

## Repository layout

```
contracts/AegisFlow.py        # the Intelligent Contract (the protocol)
tests/                        # 48 direct-mode tests (deterministic core,
                              #   S1-S5, fail-safes, validator substance)
scripts/deploy_studionet.py   # deploy + bootstrap + live smoke (S1-S5)
data/evidence_registry.json   # pinned external evidence registry
frontend/                     # the dApp (GitHub Pages, GenLayer SDK bundle)
docs/                         # architecture, deployment log
.github/workflows/            # Pages deploy
```

## Quick start

```bash
# tests (gltest direct mode — milliseconds per test)
uv venv --python 3.12 .venv && uv pip install --python .venv/bin/python \
  genlayer-test==0.29.2 pytest eth_utils
.venv/bin/python -m pytest tests/ -v

# deploy + live smoke (Studionet; ~20 min for ~15 consensus txs)
uv pip install --python .venv/bin/python genlayer-py
.venv/bin/python scripts/deploy_studionet.py
```

The dApp frontend reads its contract address from Settings (persisted in
localStorage). Deployed instance: see `docs/deployment_log.json`.

## Frontend

Premium dark dashboard (electric cyan / violet on deep navy, glass panels,
animated status indicators) with hash-routed pages: landing, dashboard (live
protocol stats + activity feed), agents (+detail), actions (+detail with
consensus visualization and the decision record), policies (+visual editor
showing Human Policy vs Machine Constraints), consensus activity, audit
explorer (filterable), and settings (contract connection, circuit breaker,
registry pin).

Live tx data is labeled `LIVE CONSENSUS`; the per-validator grid is
labeled `DEMO MODE` where the receipt does not expose per-validator votes —
consensus decisions, facts, and evidence hashes shown are always real
on-chain state.

## Known limitations

- The evidence registry is a curated static snapshot (pinned per deployment),
  not a live feed of every possible target — addresses absent from the
  registry are treated as NEW/unknown (never as safe).
- Value in the demo is denominated in milli-USDC accounting units tracked by
  the contract; the demo does not move real tokens on execution (the
  authorization/budget bookkeeping and any integrations to actual token
  transfers are where the protocol enforces; this keeps the hackathon demo
  safe while demonstrating the full decision → enforcement lifecycle).
- `resolve_escalation` / `pause_protocol` are owner-gated; in the demo the
  contract deployer is the owner.
- Studionet consensus takes ~40-120 s per write; the UI communicates this
  with progress steps and never fakes a decision.
