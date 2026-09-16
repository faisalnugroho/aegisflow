# AegisFlow — Submission Draft (Agent Tank Hackathon · Autonomous Protocols)

## Category
Builder / Projects — Intelligent Contracts on GenLayer.

## Title
AegisFlow — Autonomous Agents. Consensus-Governed Actions.

## One-liner
An execution control protocol where autonomous AI agents propose actions and
GenLayer consensus — not a frontend, a backend, or any single AI — decides
whether they comply with human-written policy before deterministic enforcement
allows execution.

## Full description
AI agents are starting to act directly on blockchains: treasury agents paying
invoices, trading agents rebalancing, DAO agents submitting proposals.
Deterministic smart contracts can enforce arithmetic limits, but they cannot
read a natural-language policy, weigh pinned external evidence, or judge
whether an agent's stated intent makes sense. A single centralized AI
gatekeeper is worse — whoever controls the model controls every agent.

AegisFlow is the missing layer between autonomous agents and high-impact
actions:

AGENT → INTENT → DETERMINISTIC POLICY FACTS → GENLAYER CONSENSUS →
DECISION (APPROVE / REJECT / ESCALATE) → DETERMINISTIC ENFORCEMENT →
EXECUTION / BLOCK / HUMAN REVIEW → AUDIT TRAIL.

Key architectural principle — "LLM labels, CONTRACT derives":
1. The contract computes nine objective risk facts deterministically
   (amount limit, daily budget, recipient novelty, cooldown, action type,
   restricted target, external risk flag, exposure, evidence availability).
   Arithmetic never goes to the LLM.
2. GenLayer leader + validators each independently fetch a commit-pinned,
   keccak256-verified evidence registry, recompute the same facts, and run
   the same arbitration prompt covering the three criteria no Solidity
   contract can judge: policy intent compliance, evidence safety, action
   proportionality. Validators compare only the substance — the nine fact
   booleans, the evidence hash, and the three criterion labels. Free text
   and the advisory risk score are deliberately not compared.
3. The contract derives the verdict from consensus-checked inputs: hard
   deterministic violations and criterion FAILs → REJECT; new recipients,
   UNCERTAIN labels, unavailable evidence/LLM, or above-threshold amounts →
   ESCALATE (uncertainty is never approval); otherwise → APPROVE. A fooled
   or hallucinating model can never approve a limit violation or a flagged
   target.
4. Execution is a separate, permissionless deterministic step that
   re-verifies everything: action hash, policy version (a v1 approval dies
   when the policy becomes v2 — proven live), nonce, budget, expiry,
   protocol pause, and single-execution. Consensus alone can never move
   value.

The dApp (GitHub Pages) is a full protocol console: dashboard, agent and
policy management (Human Policy vs Machine Constraints editor), action
queue with five one-click live scenarios, consensus visualization, audit
explorer, and an owner circuit breaker.

## Links
- Repository: https://github.com/faisalnugroho/aegisflow
- Live dApp: https://faisalnugroho.github.io/aegisflow/
- Contract (Studio Next, chain 61997):
  https://explorer-studio-dev.genlayer.com/address/0xadF028749733F1D5FA73Ffc0532a48e6Ee5A6B82
- Deployment evidence (Studio Next 61997): docs/deployment_log_studio_next_61997.json
- Historical baseline (Studionet 61999, Sep 2026): contract
  0xFAB23E7B871868Caf1D39454652FD20d66ebAe1E — preserved, intentionally retained,
  see docs/deployment_log.json

## Evidence — every claim is a clickable tx on the explorer

Deploy (full consensus): 0x660f18b4e501901c19df2f7fdd451e0bce7d08c94b6959d8c6ac6d5c8401cbf5

| Scenario | Action | Result | Consensus tx |
|---|---|---|---|
| S1 safe payment (25 USDC, known provider) | ACT-000001 | APPROVE risk 6, EXECUTED | 0x0204d5a35b5180567e3d309c1aafebf98e6afe41fb049b107c8eea81ea2b7465 |
| S1 determinism run 2 | ACT-000002 | APPROVE risk 5 | 0x7576a5c995834ac8ae91af04da7695573860aa798bfa1278e8fc2771397a77e1 |
| S1 determinism run 3 | ACT-000003 | APPROVE risk 8 | 0x24582d38decf54ced878dc4e3d28535746e571a5b8289e8bcb6f0ae908737b30 |
| S2 over limit (100 vs 50 USDC) | ACT-000004 | REJECT amount_limit_exceeded risk 75 | 0x518140cd1876519f45ff171173173601ba2130a6f8e6c0d24cc864875dc9c157 |
| S3 new recipient (20 USDC, unverified) | ACT-000005 | ESCALATE → human approve → EXECUTED | 0xfca319d71b278accd9190362da1f8dd39929eccccd6816bf5b74c6b663791c6f |
| S4 flagged target (phishing drainer) | ACT-000006 | REJECT external_risk_flagged risk 98 | 0xcf547b9c16c99c0d0f131b57c6067eead1002e8266182c9926e8d3ffe474cce0 |
| S5 approval under v1 | ACT-000007 | APPROVE risk 5 | 0xf42d32c8edd45dfe102ff11247bfc315beb136cf7131528c7b46fc5431c92da4 |
| S5 policy v1→v2, execute v1 approval | ACT-000007 | BLOCKED stale_policy | 0xfa05f51095097a7f63858baf7efdd31627ad4defdafe6f7448d766144cb8ba76 |
| S3 human resolution | ACT-000005 | APPROVED via resolve_escalation | 0x90df789f0f138994bcecce9142f84567f0b08eed1e2252f5190640ba9a79a59f |
| S3 execution after human approval | ACT-000005 | EXECUTED | 0x82bd13b622363c220553454cb5b45ee5e76c283eddbbc812e628827343cd76aa |
| dApp E2E (from GitHub Pages, burner wallet) | ACT-000009 | APPROVE → EXECUTED | 0xde612e0ccd2ee200e7c38c8d73c5a5b354e5abf2f4cb493b8f3789d27966d992 |

Determinism note: three consecutive S1-class consensus runs produced the
same APPROVE/policy_compliant verdict with advisory risk scores 6/5/8 —
the LLM's free-text reasoning and advisory score vary by design; the
compared substance (facts, labels, evidence hash) is identical, which is
exactly the equivalence principle working.

## Testing
- 48/48 direct-mode tests green (gltest 0.29.2, deterministic mocks):
  S1–S5, fail-safe paths (registry fetch failure, keccak mismatch, LLM
  malformed, missing criteria), validator-substance check (divergent
  evidence → validator rejects), budget accounting, replay protection,
  expiry, circuit breaker, audit ring, input validation.
- genvm-lint: validate ok (21 methods; W004 bare-AssertionError warnings
  only — the same non-blocking class shipped with portal-accepted
  TrustReconciler and SecondHandCarInspectionEscrow).

## Honest scope notes (what this is NOT)
- The demo tracks value in integer milli-USDC accounting units enforced by
  the protocol (authorization, budget rails, single-execution); it does not
  move a real token on execution — wiring `execute_action` to an actual
  token transfer is a mechanical integration, deliberately out of scope to
  keep the hackathon demo safe.
- The evidence registry is a curated static snapshot pinned per deployment
  (commit + keccak256), not a live feed; addresses absent from it are
  treated as NEW/unknown — never as safe.
- The per-validator grid in the consensus visualization is labeled
  DEMO MODE where the tx receipt does not expose per-validator votes; the
  decisions, facts, criteria labels, and evidence hashes displayed are
  always real on-chain state written by real GenLayer consensus.
- `resolve_escalation` / `pause_protocol` are owner-gated; the deployer is
  the owner in this deployment.

## Local reproduction
```bash
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python genlayer-test==0.29.2 pytest eth_utils
.venv/bin/python -m pytest tests/ -v          # 48/48

uv pip install --python .venv/bin/python genlayer-py
.venv/bin/python scripts/deploy_studio_next.py # deploy + live smoke S1-S5 (Studio Next 61997,
                                               # genlayer-py 0.19.0rc2 + genlayer-test 0.30.0rc2)
```


## Studio Next deployment (chain 61997) — September 2026

Per the Steward request, the identical AegisFlow contract was deployed to the
GenLayer Studio development preview ("Studio Next", studio-dev.genlayer.com,
chain ID 61997) using the v0.6 release-candidate tooling family
(genlayer-py 0.19.0rc2, genlayer-test 0.30.0rc2, genlayer-js 2.0.0-rc.1).
The contract source (contracts/AegisFlow.py) is byte-identical to the deployed
code (sha256 e7e550d84701a170…, proven from the deploy tx's on-chain
contract_code against the repository file). The stable Studionet (61999)
deployment is retained unchanged as a historical baseline.

### Studio Next evidence — every claim is a clickable tx on
https://explorer-studio-dev.genlayer.com

Deploy (full consensus): 0x317fdb3a596f9f93b95f56f9ffc8f8428c013ebd24dabc15e73904e6dcb4b456
Contract: 0xadF028749733F1D5FA73Ffc0532a48e6Ee5A6B82

| Scenario | Action | Result | Consensus tx |
|---|---|---|---|
| S1 safe payment (25 USDC, known provider) | ACT-000001 | APPROVE risk 8, EXECUTED | 0xeef623eec0254f8bf2dea0ff795f75f6f5f4d34ef273f273001fec8152e524f1 |
| S1 determinism run 2 | ACT-000002 | APPROVE risk 8 | (see deployment log) |
| S1 determinism run 3 | ACT-000003 | APPROVE risk 6 | (see deployment log) |
| S2 over limit (100 vs 50 USDC) | ACT-000004 | REJECT amount_limit_exceeded risk 78 | (see deployment log) |
| S3 new recipient (20 USDC, unverified) | ACT-000005 | ESCALATE → human approve → EXECUTED | (see deployment log) |
| S4 flagged target (phishing drainer) | ACT-000006 | REJECT external_risk_flagged risk 97 | (see deployment log; first run MAJORITY_DISAGREE, re-cranked on the same PENDING action per the fail-safe design) |
| S5 approval under v1 → policy v2 | ACT-000007 | BLOCKED stale_policy at execution | (see deployment log) |
| dApp E2E (browser burner wallet) | ACT-000009 | APPROVE risk 15 → EXECUTED | 0xd99839cb6e44378923073b9d16f53ebbd8ae342f0210ab9acca5708b93701448 |

Full tx hashes for every step: docs/deployment_log_studio_next_61997.json.

### Demo video

A short public demo video (no login required) showing the live dApp on
Studio Next 61997 and the full consensus → execution flow is embedded in the
repository README: docs/demo_video.md (public URL inside).
