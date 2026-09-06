# { "Depends": "py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6" }
from genlayer import *
import json

"""
AEGISFLOW — Autonomous Agents. Consensus-Governed Actions.
GenLayer Intelligent Contract (Agent Tank Hackathon — Autonomous Protocols).

PROBLEM
  Autonomous AI agents are starting to act directly on blockchains:
  treasury agents that pay invoices, trading agents that rebalance,
  DAO agents that submit proposals. Deterministic smart contracts can
  enforce arithmetic limits, but they CANNOT:
    - interpret a natural-language policy ("never approve actions that
      look like personal gambling, even if the amount is small"),
    - weigh external evidence about a recipient,
    - judge whether a stated reason matches the policy's intent,
    - decide that uncertainty should block execution rather than allow it.
  A single centralized AI gatekeeper is worse: whoever controls the
  gatekeeper controls the agent's every action.

AEGISFLOW is the missing EXECUTION CONTROL LAYER between autonomous
agents and high-impact actions:

  AGENT -> INTENT -> DETERMINISTIC POLICY FACTS -> GENLAYER CONSENSUS
        -> DECISION (APPROVE / REJECT / ESCALATE) -> DETERMINISTIC
        ENFORCEMENT -> EXECUTION / BLOCK / HUMAN REVIEW -> AUDIT TRAIL

ARCHITECTURE (defense in depth — "LLM labels, CONTRACT derives"):
  1. DETERMINISTIC FACTS FIRST. The contract computes seven objective
     risk components as pure functions of on-chain state plus a
     pinned external evidence registry:
       amount_limit_violation, budget_exceeded, recipient_is_new,
       cooldown_active, action_type_allowed, target_restricted,
       external_risk_flag
     Arithmetic NEVER goes to the LLM.
  2. LLM CONSENSUS JUDGES THE SEMANTICS. Leader and every validator
     independently fetch the SAME pinned registry (commit-pinned URL +
     on-chain keccak256 content verification), recompute the same
     facts, and each run the arbitration prompt. The LLM returns ONLY
     per-criterion labels (PASS / FAIL / UNCERTAIN) with short cited
     justifications for the three criteria deterministic code cannot
     judge:
       policy_intent_compliant  — does the reason/purpose satisfy the
                                  natural-language policy?
       evidence_safe            — does the external evidence support
                                  that the target is safe?
       action_proportionate    — is the action proportionate to the
                                  stated reason and agent role?
  3. THE CONTRACT DERIVES THE VERDICT (the LLM never picks it):
       - any hard deterministic violation        -> REJECT
       - any criterion FAIL                      -> REJECT
       - new recipient needing verification,
         or any UNCERTAIN criterion,
         or evidence unavailable,
         or amount above escalation threshold    -> ESCALATE
       - otherwise                                -> APPROVE
     A fooled or hallucinating model can therefore NEVER approve a
     limit violation or a flagged target: the deterministic gates
     clamp it. Conversely the LLM can REJECT or ESCALATE something
     the arithmetic alone would allow — the semantic judgment that no
     Solidity contract could make. That is why GenLayer consensus is
     load-bearing here, not decorative.
  4. FAIL-SAFE EVERYWHERE. Registry fetch failure, hash mismatch,
     malformed payload, LLM exception, malformed LLM JSON, missing
     criterion — every failure mode maps to a well-formed decision
     that NEVER approves (ESCALATE with a machine-readable reason, or
     REJECT when a hard fact already fired). No path can skip it.
  5. DETERMINISTIC ENFORCEMENT + REPLAY PROTECTION. Execution is a
     separate permissionless step that re-verifies EVERYTHING: action
     hash, policy version (an approval dies when its policy is
     superseded — the v1-approval cannot execute under v2), agent
     nonce monotonicity, daily budget, expiry, protocol pause state,
     and single-execution. Consensus can never move value by itself.

EXTERNAL EVIDENCE (honest sourcing, keyless and re-fetchable):
  A single pinned JSON registry in this repository
  (data/evidence_registry.json) served from raw.githubusercontent.com
  at an IMMUTABLE commit URL set at deployment. Leader and every
  validator fetch the identical bytes and independently verify the
  keccak256 against the deployment pin; the registry contains only
  stable normalized facts (known_recipient, risk_flag, label, status)
  — no timestamps, no raw HTML, no volatile fields. The evidence_hash
  stored with every decision is the keccak256 of the canonical
  normalized entry actually used, so the UI can show exactly what
  consensus saw without storing bulky evidence on-chain.

DECISION RECORD (per spec): decision, risk_score (0-100, derived as
  max(objective score from the facts, LLM advisory score) — never the
  raw LLM number), policy_compliant, reason_code, evidence_hash,
  policy_version, timestamp, action_hash.
"""

# ---------------------------------------------------------------------------
# Tunable protocol constants
# ---------------------------------------------------------------------------
MAX_AUDIT_ENTRIES = 240          # audit ring size (keep storage bounded)
AUDIT_START = 0                  # first audit sequence number
DEFAULT_EXPIRY_SECONDS = 3600    # fallback when caller passes 0
MIN_EXPIRY_SECONDS = 30
DAY_SECONDS = 86400
LLM_JUSTIFICATION_MAX = 200
RISK_LOW = 29
RISK_MEDIUM = 59
RISK_HIGH = 79

_ACTION_TYPES = ("TRANSFER", "REBALANCE", "GOVERNANCE_SUBMIT",
                 "PROCUREMENT", "DATA_PUBLISH")


# ---------------------------------------------------------------------------
# Pure helpers (identical for leader, validators, and tests)
# ---------------------------------------------------------------------------
def _parse_iso_epoch(iso: str) -> int:
    """Parse node-assigned 'YYYY-MM-DDTHH:MM:SS(.ffffff)?(Z|+00:00)?' to
    epoch seconds with pure integer math (Howard Hinnant
    _days_from_civil). No datetime module, no floats."""
    try:
        date_part = iso.split("T")[0]
        y = int(date_part[0:4])
        m = int(date_part[5:7])
        d = int(date_part[8:10])
        time_part = iso.split("T")[1]
        hh = int(time_part[0:2])
        mm = int(time_part[3:5])
        ss = int(time_part[6:8])
    except Exception:
        return 0
    yy = y
    if m <= 2:
        yy -= 1
    era = int(yy / 400) if yy >= 0 else -int((-yy + 399) / 400)
    yoe = yy - era * 400
    mp = (m + 9) % 12
    doy = int((153 * mp + 2) / 5) + d - 1
    doe = yoe * 365 + int(yoe / 4) - int(yoe / 100) + doy
    days = era * 146097 + doe - 719468
    return days * 86400 + hh * 3600 + mm * 60 + ss


def _keccak256_hex_of_string(text: str) -> str:
    """keccak256 of a str's UTF-8 bytes as lowercase hex — deterministic
    pure-stdlib path (genlayer.py.keccak), identical in the leader and
    every validator. Used for the pinned registry content verification
    and for all canonical hashes (action / policy / evidence)."""
    from genlayer.py.keccak import Keccak256
    return Keccak256(text.encode("utf-8")).hexdigest()


def _canonical_json(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"))


def _is_hex_address(raw) -> bool:
    if not isinstance(raw, str):
        return False
    if len(raw) != 42 or not raw.startswith("0x"):
        return False
    for ch in raw[2:]:
        if ch not in "0123456789abcdefABCDEF":
            return False
    return True


def _valid_action_type(t: str) -> bool:
    for allowed in _ACTION_TYPES:
        if t == allowed:
            return True
    return False


def _risk_bucket(score: int) -> str:
    if score <= RISK_LOW:
        return "LOW"
    if score <= RISK_MEDIUM:
        return "MEDIUM"
    if score <= RISK_HIGH:
        return "HIGH"
    return "CRITICAL"


def _objective_risk_score(facts: dict) -> int:
    """Deterministic risk score derived ONLY from the objective facts —
    the LLM's advisory score can raise it, never lower it."""
    score = 0
    if facts.get("amount_limit_violation"):
        score += 35
    if facts.get("budget_exceeded"):
        score += 30
    if facts.get("recipient_is_new"):
        score += 20
    if facts.get("cooldown_active"):
        score += 10
    if not facts.get("action_type_allowed", True):
        score += 25
    if facts.get("target_restricted"):
        score += 40
    if facts.get("external_risk_flag"):
        score += 40
    if facts.get("exposure_high"):
        score += 15
    if score > 100:
        score = 100
    return score


# ---------------------------------------------------------------------------
# Contract
# ---------------------------------------------------------------------------
class ActionSubmittedEvent(gl.Event):
    def __init__(self, action_id: str, /, **blob): ...


class DecisionEvent(gl.Event):
    def __init__(self, action_id: str, /, **blob): ...


class ActionExecutedEvent(gl.Event):
    def __init__(self, action_id: str, /, **blob): ...


class ProtocolPausedEvent(gl.Event):
    def __init__(self, /, **blob): ...


class AegisFlow(gl.Contract):
    """Consensus-governed execution control protocol for autonomous agents."""

    # -- storage (uniform TreeMap[str, str] — gltest-compatible) ---------
    owner: Address
    protocol_paused: bool
    registry_url: str
    registry_keccak: str
    registry_ref: str
    agents: TreeMap[str, str]        # agent_id -> JSON profile + counters
    policies: TreeMap[str, str]     # policy_id -> JSON current version
    actions: TreeMap[str, str]       # action_id -> JSON intent + status
    decisions: TreeMap[str, str]     # action_id / action_hash -> JSON
    audit_records: TreeMap[str, str] # zero-padded seq -> JSON entry
    agent_id_list: str              # comma-separated discovery list
    policy_id_list: str
    next_action_seq: u256
    next_audit_seq: u256
    # aggregates for the dashboard
    stat_actions_total: u256
    stat_approved: u256
    stat_rejected: u256
    stat_escalated: u256
    stat_executed: u256
    stat_authorized_millis: u256
    stat_blocked_millis: u256

    def __init__(self, registry_commit: str, registry_keccak256: str):
        self.owner = gl.message.sender_address
        self.protocol_paused = False
        if len(registry_commit) != 40:
            raise AssertionError("bad_registry_commit")
        if len(registry_keccak256) != 64:
            raise AssertionError("bad_registry_keccak")
        self.registry_url = ("https://raw.githubusercontent.com/faisalnugroho/"
                             "aegisflow/" + registry_commit
                             + "/data/evidence_registry.json")
        self.registry_keccak = registry_keccak256
        self.registry_ref = registry_commit[:12] + ":" + registry_keccak256[:16]
        self.agents = TreeMap()
        self.policies = TreeMap()
        self.actions = TreeMap()
        self.decisions = TreeMap()
        self.audit_records = TreeMap()
        self.agent_id_list = ""
        self.policy_id_list = ""
        self.next_action_seq = u256(1)
        self.next_audit_seq = u256(AUDIT_START)
        self.stat_actions_total = u256(0)
        self.stat_approved = u256(0)
        self.stat_rejected = u256(0)
        self.stat_escalated = u256(0)
        self.stat_executed = u256(0)
        self.stat_authorized_millis = u256(0)
        self.stat_blocked_millis = u256(0)

    # ------------------------------------------------------------------
    # internal storage helpers
    # ------------------------------------------------------------------
    def _now(self) -> int:
        return _parse_iso_epoch(gl.message_raw["datetime"])

    def _load(self, store: TreeMap, key: str) -> dict:
        return json.loads(store[key])

    def _put(self, store: TreeMap, key: str, obj: dict) -> None:
        store[key] = _canonical_json(obj)

    def _audit(self, entry: dict) -> None:
        seq = int(self.next_audit_seq)
        entry["seq"] = seq
        entry["at"] = self._now()
        key = "A" + str(seq).zfill(6)
        # bounded ring: overwrite the oldest slot once full
        if seq - AUDIT_START >= MAX_AUDIT_ENTRIES:
            key = "A" + str(seq - MAX_AUDIT_ENTRIES).zfill(6)
        self._put(self.audit_records, key, entry)
        self.next_audit_seq = u256(seq + 1)

    # ------------------------------------------------------------------
    # POLICY SYSTEM — machine constraints + natural language, versioned
    # ------------------------------------------------------------------
    @gl.public.write
    def register_policy(self,
                        policy_id: str,
                        name: str,
                        description: str,
                        natural_language: str,
                        max_single_action_millis: int,
                        daily_budget_millis: int,
                        allowed_action_types: str,
                        restricted_targets: str,
                        cooldown_seconds: int,
                        require_consensus: bool,
                        require_escalation_above_millis: int,
                        require_verification_new_recipients: bool,
                        reject_flagged_targets: bool) -> str:
        if policy_id in self.policies:
            raise AssertionError("policy_exists")
        pol = {
            "policy_id": policy_id,
            "version": 1,
            "version_label": policy_id + "-v1",
            "name": name,
            "description": description,
            "natural_language": natural_language,
            "max_single_action_millis": int(max_single_action_millis),
            "daily_budget_millis": int(daily_budget_millis),
            "allowed_action_types": allowed_action_types,
            "restricted_targets": restricted_targets,
            "cooldown_seconds": int(cooldown_seconds),
            "require_consensus": bool(require_consensus),
            "require_escalation_above_millis":
                int(require_escalation_above_millis),
            "require_verification_new_recipients":
                bool(require_verification_new_recipients),
            "reject_flagged_targets": bool(reject_flagged_targets),
            "status": "ACTIVE",
            "owner": str(gl.message.sender_address),
            "created_at": self._now(),
            "updated_at": self._now(),
        }
        pol["policy_hash"] = _keccak256_hex_of_string(
            _canonical_json(self._policy_constraints(pol)))
        self._put(self.policies, policy_id, pol)
        self.policy_id_list = _append_id(self.policy_id_list, policy_id)
        self._audit({"kind": "policy_registered",
                     "policy_id": policy_id,
                     "policy_version": pol["version_label"],
                     "policy_hash": pol["policy_hash"],
                     "by": str(gl.message.sender_address)})
        return pol["version_label"]

    @gl.public.write
    def update_policy(self,
                      policy_id: str,
                      name: str,
                      description: str,
                      natural_language: str,
                      max_single_action_millis: int,
                      daily_budget_millis: int,
                      allowed_action_types: str,
                      restricted_targets: str,
                      cooldown_seconds: int,
                      require_consensus: bool,
                      require_escalation_above_millis: int,
                      require_verification_new_recipients: bool,
                      reject_flagged_targets: bool) -> str:
        if policy_id not in self.policies:
            raise AssertionError("policy_not_found")
        old = self._load(self.policies, policy_id)
        new_version = int(old["version"]) + 1
        pol = {
            "policy_id": policy_id,
            "version": new_version,
            "version_label": policy_id + "-v" + str(new_version),
            "name": name,
            "description": description,
            "natural_language": natural_language,
            "max_single_action_millis": int(max_single_action_millis),
            "daily_budget_millis": int(daily_budget_millis),
            "allowed_action_types": allowed_action_types,
            "restricted_targets": restricted_targets,
            "cooldown_seconds": int(cooldown_seconds),
            "require_consensus": bool(require_consensus),
            "require_escalation_above_millis":
                int(require_escalation_above_millis),
            "require_verification_new_recipients":
                bool(require_verification_new_recipients),
            "reject_flagged_targets": bool(reject_flagged_targets),
            "status": "ACTIVE",
            "owner": old["owner"],
            "created_at": old["created_at"],
            "updated_at": self._now(),
        }
        pol["policy_hash"] = _keccak256_hex_of_string(
            _canonical_json(self._policy_constraints(pol)))
        self._put(self.policies, policy_id, pol)
        # THE v1->v2 RULE: approvals live and die with the exact policy
        # version that produced them. Marking is implicit (execute checks
        # the live version) but we audit the supersession explicitly.
        self._audit({"kind": "policy_updated",
                     "policy_id": policy_id,
                     "policy_version": pol["version_label"],
                     "previous_version": old["version_label"],
                     "policy_hash": pol["policy_hash"],
                     "note": "outstanding approvals under "
                             + old["version_label"]
                             + " can no longer execute",
                     "by": str(gl.message.sender_address)})
        return pol["version_label"]

    def _policy_constraints(self, pol: dict) -> dict:
        """The machine-enforceable subset that defines a policy's hash."""
        return {
            "max_single_action_millis": pol["max_single_action_millis"],
            "daily_budget_millis": pol["daily_budget_millis"],
            "allowed_action_types": pol["allowed_action_types"],
            "restricted_targets": pol["restricted_targets"],
            "cooldown_seconds": pol["cooldown_seconds"],
            "require_escalation_above_millis":
                pol["require_escalation_above_millis"],
            "require_verification_new_recipients":
                pol["require_verification_new_recipients"],
            "reject_flagged_targets": pol["reject_flagged_targets"],
        }

    # ------------------------------------------------------------------
    # AGENT REGISTRY
    # ------------------------------------------------------------------
    @gl.public.write
    def register_agent(self,
                       agent_id: str,
                       name: str,
                       description: str,
                       policy_id: str,
                       risk_level: str,
                       display_budget_millis: int) -> str:
        if agent_id in self.agents:
            raise AssertionError("agent_exists")
        if policy_id not in self.policies:
            raise AssertionError("policy_not_found")
        agent = {
            "agent_id": agent_id,
            "name": name,
            "description": description,
            "policy_id": policy_id,
            "risk_level": risk_level,
            "status": "ACTIVE",
            "display_budget_millis": int(display_budget_millis),
            "next_nonce": 1,
            "action_count": 0,
            "approved_count": 0,
            "rejected_count": 0,
            "escalated_count": 0,
            "executed_count": 0,
            "daily_spent_millis": 0,
            "pending_exposure_millis": 0,
            "total_spent_millis": 0,
            "day_anchor": 0,
            "last_executed_at": 0,
            "created_at": self._now(),
        }
        self._put(self.agents, agent_id, agent)
        self.agent_id_list = _append_id(self.agent_id_list, agent_id)
        self._audit({"kind": "agent_registered",
                     "agent_id": agent_id,
                     "policy_id": policy_id,
                     "by": str(gl.message.sender_address)})
        return agent_id

    # ------------------------------------------------------------------
    # ACTION INTAKE — deterministic, no LLM
    # ------------------------------------------------------------------
    @gl.public.write
    def submit_action(self,
                      agent_id: str,
                      action_type: str,
                      target: str,
                      amount_millis: int,
                      asset: str,
                      parameters: str,
                      reason: str,
                      context_hash: str,
                      expires_in_seconds: int) -> str:
        if self.protocol_paused:
            raise AssertionError("protocol_paused")
        if agent_id not in self.agents:
            raise AssertionError("agent_not_found")
        if not _valid_action_type(action_type):
            raise AssertionError("invalid_action_type")
        if not _is_hex_address(target):
            raise AssertionError("invalid_target_address")
        if int(amount_millis) < 0:
            raise AssertionError("invalid_amount")

        agent = self._load(self.agents, agent_id)
        pol = self._load(self.policies, agent["policy_id"])
        now = self._now()

        expires_in = int(expires_in_seconds)
        if expires_in <= 0:
            expires_in = DEFAULT_EXPIRY_SECONDS
        if expires_in < MIN_EXPIRY_SECONDS:
            raise AssertionError("expiry_too_short")

        seq = int(self.next_action_seq)
        action_id = "ACT-" + str(seq).zfill(6)
        self.next_action_seq = u256(seq + 1)

        nonce = int(agent["next_nonce"])
        record = {
            "action_id": action_id,
            "agent_id": agent_id,
            "action_type": action_type,
            "target": target,
            "amount_millis": int(amount_millis),
            "asset": asset,
            "parameters": parameters,
            "reason": reason,
            "policy_id": agent["policy_id"],
            "policy_version": pol["version_label"],
            "policy_hash": pol["policy_hash"],
            "nonce": nonce,
            "context_hash": context_hash,
            "created_at": now,
            "expires_at": now + expires_in,
            "status": "PENDING",
            "execution_status": "not_authorized",
            "executed_at": 0,
            "resolution": "",
            "submitted_by": str(gl.message.sender_address),
        }
        # The action hash IS the immutable identity of the intent —
        # keccak256 over the canonical normalized form.
        record["action_hash"] = _keccak256_hex_of_string(_canonical_json({
            "agent_id": agent_id,
            "action_type": action_type,
            "target": target.lower(),
            "amount_millis": int(amount_millis),
            "asset": asset,
            "parameters": parameters,
            "reason": reason,
            "policy_id": record["policy_id"],
            "policy_version": record["policy_version"],
            "nonce": nonce,
            "created_at": now,
            "expires_at": record["expires_at"],
        }))
        if record["action_hash"] in self.decisions:
            raise AssertionError("duplicate_action_hash")

        agent["next_nonce"] = nonce + 1
        agent["action_count"] = int(agent["action_count"]) + 1
        agent["pending_exposure_millis"] = (
            int(agent["pending_exposure_millis"]) + int(amount_millis))
        self._put(self.agents, agent_id, agent)
        self._put(self.actions, action_id, record)
        self.stat_actions_total = u256(int(self.stat_actions_total) + 1)
        ActionSubmittedEvent(action_id,
                             agent=agent_id,
                             action_type=action_type,
                             amount_millis=int(amount_millis),
                             target=target,
                             action_hash=record["action_hash"]).emit()
        self._audit({"kind": "action_submitted",
                     "action_id": action_id,
                     "agent_id": agent_id,
                     "action_type": action_type,
                     "amount_millis": int(amount_millis),
                     "target": target,
                     "policy_version": record["policy_version"],
                     "action_hash": record["action_hash"],
                     "nonce": nonce})
        return action_id

    # ------------------------------------------------------------------
    # THE CONSENSUS PIPELINE — leader proposes, validators verify the
    # SUBSTANCE (facts + criteria labels + evidence hash), contract
    # derives the verdict.
    # ------------------------------------------------------------------
    @gl.public.write
    def run_consensus(self, action_id: str) -> str:
        if action_id not in self.actions:
            raise AssertionError("action_not_found")
        action = self._load(self.actions, action_id)
        if action["status"] != "PENDING":
            raise AssertionError("already_decided:status=" + action["status"])
        if self.protocol_paused:
            raise AssertionError("protocol_paused")

        agent = self._load(self.agents, action["agent_id"])
        pol = self._load(self.policies, action["policy_id"])
        now = self._now()

        # ---- deterministic pre-checks (cheap, before any LLM) ----------
        if now > int(action["expires_at"]):
            # persist the terminal EXPIRED state (the tx itself succeeds)
            action["status"] = "EXPIRED"
            action["execution_status"] = "expired"
            self._put(self.actions, action_id, action)
            self._release_exposure(agent, action)
            self._audit({"kind": "action_expired",
                         "action_id": action_id,
                         "agent_id": action["agent_id"],
                         "reason": "expired_before_consensus"})
            return _canonical_json({
                "action_id": action_id,
                "action_hash": action["action_hash"],
                "decision": "EXPIRED",
                "reason_code": "expired_before_consensus",
                "timestamp": now})

        # copy storage into memory for the nondet block
        a_snapshot = dict(action)
        p_snapshot = dict(pol)
        g_snapshot = dict(agent)
        registry_url = self.registry_url
        registry_keccak = self.registry_keccak

        def leader_fn():
            # ---------------- 1. FETCH PINNED EXTERNAL EVIDENCE ----------
            # Every validator fetches the identical commit-pinned URL and
            # verifies the keccak256 content hash. Only stable, normalized
            # fields are read out; nothing volatile is stored or compared.
            evidence = None
            registry_fail = ""
            try:
                resp = gl.nondet.web.get(registry_url)
                if resp.status != 200:
                    raise AssertionError("http_" + str(resp.status))
                if resp.body is None:
                    raise AssertionError("empty_body")
                body_text = resp.body.decode("utf-8", errors="replace")
                fetched_hash = _keccak256_hex_of_string(body_text)
                if fetched_hash != registry_keccak:
                    raise AssertionError("registry_hash_mismatch")
                d = json.loads(body_text)
                if not isinstance(d, dict):
                    raise AssertionError("malformed_payload")
                if not isinstance(d.get("recipients"), dict):
                    raise AssertionError("registry_missing_recipients")
                evidence = d
            except Exception as e:
                registry_fail = repr(e)[:80]

            # ---------------- 2. NORMALIZE THE EVIDENCE -----------------
            target_key = str(a_snapshot["target"]).lower()
            entry = None
            if evidence is not None:
                raw_entry = evidence["recipients"].get(target_key)
                if isinstance(raw_entry, dict):
                    entry = {
                        "target": target_key,
                        "known_recipient":
                            bool(raw_entry.get("known_recipient", False)),
                        "risk_flag": bool(raw_entry.get("risk_flag", False)),
                        "label": str(raw_entry.get("label", ""))[:80],
                        "status": str(raw_entry.get("status", ""))[:24],
                    }
            if entry is None:
                # Unknown to the registry = NEW recipient, no evidence.
                entry = {"target": target_key, "known_recipient": False,
                         "risk_flag": False, "label": "not_in_registry",
                         "status": "unknown"}
            evidence_hash = _keccak256_hex_of_string(
                _canonical_json(entry))

            # ---------------- 3. DETERMINISTIC FACTS --------------------
            # Pure functions of storage + fetched evidence. Identical
            # across every validator; arithmetic never goes to the LLM.
            amount = int(a_snapshot["amount_millis"])
            daily_budget = int(p_snapshot["daily_budget_millis"])
            daily_spent = int(g_snapshot["daily_spent_millis"])
            pending = int(g_snapshot["pending_exposure_millis"])
            exposure = daily_spent + pending + amount

            type_ok = False
            for t in str(p_snapshot["allowed_action_types"]).split(","):
                if t.strip() == a_snapshot["action_type"]:
                    type_ok = True
            restricted = False
            for t in str(p_snapshot["restricted_targets"]).split(","):
                t = t.strip().lower()
                if t != "" and t == target_key:
                    restricted = True

            facts = {
                "amount_limit_violation":
                    amount > int(p_snapshot["max_single_action_millis"]),
                "budget_exceeded":
                    daily_spent + amount > daily_budget,
                "recipient_is_new": not entry["known_recipient"],
                "cooldown_active":
                    (int(g_snapshot["last_executed_at"]) > 0
                     and now - int(g_snapshot["last_executed_at"])
                     < int(p_snapshot["cooldown_seconds"])),
                "action_type_allowed": type_ok,
                "target_restricted": restricted,
                "external_risk_flag": bool(entry["risk_flag"]),
                "exposure_high": exposure > daily_budget,
                "evidence_available": evidence is not None,
            }

            # ---------------- 4. LLM SEMANTIC JUDGMENT ------------------
            # The three criteria a Solidity contract cannot judge. The
            # prompt embeds the deterministic facts so validators reach
            # the same labels; the rules below make the mapping from
            # facts to labels crisp and consensus-stable.
            prompt = (
                "You are an AegisFlow consensus evaluator. An autonomous "
                "AI agent has submitted an action intent. Judge it "
                "against the agent's policy. You are one independent "
                "evaluator; other validators judge the same action "
                "independently.\n\n"
                "POLICY (natural language, human-written):\n"
                + str(p_snapshot["natural_language"]) + "\n\n"
                "POLICY (machine constraints, already enforced "
                "deterministically on-chain): max_single_action_millis="
                + str(p_snapshot["max_single_action_millis"])
                + ", daily_budget_millis="
                + str(p_snapshot["daily_budget_millis"])
                + ", allowed_action_types="
                + str(p_snapshot["allowed_action_types"])
                + ", cooldown_seconds="
                + str(p_snapshot["cooldown_seconds"])
                + ", require_verification_new_recipients="
                + str(p_snapshot["require_verification_new_recipients"])
                + ", reject_flagged_targets="
                + str(p_snapshot["reject_flagged_targets"]) + "\n\n"
                "ACTION INTENT:\n"
                "agent_id=" + str(a_snapshot["agent_id"]) + "\n"
                "action_type=" + str(a_snapshot["action_type"]) + "\n"
                "target=" + str(a_snapshot["target"]) + "\n"
                "amount_millis=" + str(a_snapshot["amount_millis"])
                + " (asset " + str(a_snapshot["asset"]) + ")\n"
                "stated_reason=" + str(a_snapshot["reason"]) + "\n"
                "parameters=" + str(a_snapshot["parameters"]) + "\n\n"
                "DETERMINISTIC FACTS (computed on-chain, identical for "
                "every validator, do NOT recompute):\n"
                "amount_limit_violation="
                + str(facts["amount_limit_violation"]) + "\n"
                "budget_exceeded=" + str(facts["budget_exceeded"]) + "\n"
                "recipient_is_new=" + str(facts["recipient_is_new"]) + "\n"
                "cooldown_active=" + str(facts["cooldown_active"]) + "\n"
                "action_type_allowed="
                + str(facts["action_type_allowed"]) + "\n"
                "target_restricted=" + str(facts["target_restricted"]) + "\n"
                "external_risk_flag="
                + str(facts["external_risk_flag"]) + "\n"
                "exposure_high=" + str(facts["exposure_high"]) + "\n"
                "evidence_available="
                + str(facts["evidence_available"]) + "\n\n"
                "EXTERNAL EVIDENCE (normalized from the pinned registry): "
                + _canonical_json(entry) + "\n\n"
                "Judge each criterion independently. Use exactly PASS, "
                "FAIL, or UNCERTAIN.\n"
                "1. policy_intent_compliant: does the action's stated "
                "purpose, reason and context comply with the "
                "natural-language policy? This is the semantic judgment "
                "no deterministic contract can make.\n"
                "2. evidence_safe: does the external evidence support "
                "that the target is safe for this action?\n"
                "3. action_proportionate: is the action proportionate to "
                "the stated reason and the agent's described role?\n\n"
                "BINDING RULES (follow them exactly):\n"
                "- If external_risk_flag=true or target_restricted=true, "
                "evidence_safe MUST be FAIL.\n"
                "- If recipient_is_new=true, evidence_safe MUST be "
                "UNCERTAIN (a new recipient requires verification).\n"
                "- If evidence_available=false, evidence_safe MUST be "
                "UNCERTAIN.\n"
                "- If amount_limit_violation=true or budget_exceeded=true "
                "or action_type_allowed=false, policy_intent_compliant "
                "MUST be FAIL.\n"
                "- If the stated reason describes a purpose the policy "
                "clearly forbids or does not permit (e.g. gambling, "
                "personal loans, undisclosed transfers), "
                "policy_intent_compliant MUST be FAIL.\n"
                "- If the stated reason plainly matches the policy's "
                "permitted purposes and all facts above are clean, "
                "policy_intent_compliant MUST be PASS.\n"
                "- Only mark a criterion PASS when the facts and reason "
                "clearly support it; when genuinely in doubt, use "
                "UNCERTAIN (uncertainty escalates, it never approves).\n\n"
                "Return ONLY a JSON object with exactly this shape:\n"
                '{"criteria": {"policy_intent_compliant": {"status": '
                '"PASS|FAIL|UNCERTAIN", "justification": "short cited '
                'reason"}, "evidence_safe": {"status": "...", '
                '"justification": "..."}, "action_proportionate": '
                '{"status": "...", "justification": "..."}}, '
                '"llm_risk_score": <integer 0-100>}\n'
                "llm_risk_score is your overall risk assessment of this "
                "action (0=extremely safe, 100=extremely dangerous)."
            )
            llm_fail = ""
            parsed = None
            try:
                raw = gl.nondet.exec_prompt(prompt, response_format="json")
                if isinstance(raw, str):
                    parsed = json.loads(raw)
                else:
                    parsed = raw
            except Exception as e:
                llm_fail = repr(e)[:80]
            if parsed is None:
                llm_fail = llm_fail or "malformed_json"

            return {
                "facts": facts,
                "entry": entry,
                "evidence_hash": evidence_hash,
                "registry_fail": registry_fail,
                "llm_fail": llm_fail,
                "parsed": parsed,
            }

        def validator_fn(leader_res) -> bool:
            if not isinstance(leader_res, gl.vm.Return):
                return False
            try:
                mine = leader_fn()
            except Exception:
                return False
            ld = leader_res.calldata
            # PARTIAL FIELD MATCHING — compare only the consensus-critical
            # substance: the deterministic facts, the three criterion
            # STATUSES (never the free-text justifications), and the
            # evidence hash. Free text and the advisory risk score vary
            # naturally between independent LLM runs and are NOT compared.
            lf = ld.get("facts", {})
            mf = mine.get("facts", {})
            for f in ("amount_limit_violation", "budget_exceeded",
                      "recipient_is_new", "cooldown_active",
                      "action_type_allowed", "target_restricted",
                      "external_risk_flag", "exposure_high",
                      "evidence_available"):
                if bool(lf.get(f)) != bool(mf.get(f)):
                    return False
            if str(ld.get("evidence_hash")) != str(mine.get("evidence_hash")):
                return False
            lp = ld.get("parsed") or {}
            mp = mine.get("parsed") or {}
            # An LLM failure on either side must agree as a flag.
            if bool(ld.get("llm_fail")) != bool(mine.get("llm_fail")):
                return False
            if bool(ld.get("registry_fail")) != bool(mine.get("registry_fail")):
                return False
            if ld.get("llm_fail") or mine.get("llm_fail"):
                return True     # both failed -> agreement; contract fails safe
            lc = lp.get("criteria") if isinstance(lp, dict) else None
            mc = mp.get("criteria") if isinstance(mp, dict) else None
            if not isinstance(lc, dict) or not isinstance(mc, dict):
                return False
            for c in ("policy_intent_compliant", "evidence_safe",
                      "action_proportionate"):
                ls = lc.get(c)
                ms = mc.get(c)
                if not isinstance(ls, dict) or not isinstance(ms, dict):
                    return False
                if str(ls.get("status")) != str(ms.get("status")):
                    return False
            return True

        result = gl.vm.run_nondet(leader_fn, validator_fn)

        # ---- DERIVE THE VERDICT (deterministic, post-consensus) --------
        facts = result["facts"]
        entry = result["entry"]
        evidence_hash = result["evidence_hash"]
        parsed = result.get("parsed")
        llm_fail = result.get("llm_fail")

        statuses = {}
        if isinstance(parsed, dict) and isinstance(parsed.get("criteria"),
                                                   dict):
            for c in ("policy_intent_compliant", "evidence_safe",
                      "action_proportionate"):
                v = parsed["criteria"].get(c)
                if isinstance(v, dict):
                    statuses[c] = str(v.get("status"))
        llm_risk = 0
        if isinstance(parsed, dict):
            try:
                llm_risk = int(parsed.get("llm_risk_score", 0))
            except Exception:
                llm_risk = 0
        if llm_risk < 0:
            llm_risk = 0
        if llm_risk > 100:
            llm_risk = 100

        # Hard deterministic gates — clamp the LLM, always.
        if facts["amount_limit_violation"]:
            decision, reason = "REJECT", "amount_limit_exceeded"
        elif facts["budget_exceeded"]:
            decision, reason = "REJECT", "daily_budget_exceeded"
        elif not facts["action_type_allowed"]:
            decision, reason = "REJECT", "action_type_not_allowed"
        elif facts["target_restricted"]:
            decision, reason = "REJECT", "target_restricted"
        elif facts["external_risk_flag"] and pol["reject_flagged_targets"]:
            decision, reason = "REJECT", "external_risk_flagged"
        # Semantic gates — the LLM's labels, derived not trusted.
        elif statuses.get("policy_intent_compliant") == "FAIL":
            decision, reason = "REJECT", "semantic:policy_intent_compliant"
        elif statuses.get("evidence_safe") == "FAIL":
            decision, reason = "REJECT", "semantic:evidence_safe"
        elif statuses.get("action_proportionate") == "FAIL":
            decision, reason = "REJECT", "semantic:action_proportionate"
        # Uncertainty is NEVER approval — escalate.
        elif result.get("registry_fail") or llm_fail \
                or len(statuses) != 3:
            decision, reason = "ESCALATE", "llm_or_evidence_unavailable"
        elif facts["recipient_is_new"] \
                and pol["require_verification_new_recipients"]:
            decision, reason = "ESCALATE", "new_recipient_requires_verification"
        elif "UNCERTAIN" in statuses.values():
            decision, reason = "ESCALATE", "semantic:uncertain"
        elif int(action["amount_millis"]) > \
                int(pol["require_escalation_above_millis"]):
            decision, reason = "ESCALATE", "above_escalation_threshold"
        else:
            decision, reason = "APPROVE", "policy_compliant"

        risk_score = _objective_risk_score(facts)
        if llm_risk > risk_score:
            risk_score = llm_risk

        policy_compliant = decision == "APPROVE"
        decision_record = {
            "action_id": action_id,
            "action_hash": action["action_hash"],
            "decision": decision,
            "risk_score": risk_score,
            "risk_bucket": _risk_bucket(risk_score),
            "policy_compliant": policy_compliant,
            "reason_code": reason,
            "evidence_hash": evidence_hash,
            "policy_version": action["policy_version"],
            "policy_hash": action["policy_hash"],
            "timestamp": now,
            "criteria": statuses,
            "objective_facts": facts,
            "evidence_entry": entry,
            "registry_fail": result.get("registry_fail", ""),
            "llm_fail": llm_fail or "",
            "llm_risk_score": llm_risk,
            "consensus": "genlayer",
        }

        self._put(self.decisions, action["action_hash"], decision_record)
        # decisions are keyed by action_hash (immutable identity); also
        # index by action_id for convenience.
        self._put(self.decisions, action_id, decision_record)

        action["decision"] = decision
        if decision == "APPROVE":
            action["status"] = "APPROVED"
            action["execution_status"] = "authorized"
        elif decision == "REJECT":
            action["status"] = "REJECTED"
            action["execution_status"] = "blocked"
        else:
            action["status"] = "ESCALATED"
            action["execution_status"] = "human_review"
        self._put(self.actions, action_id, action)

        agent["approved_count"] = int(agent["approved_count"]) + (
            1 if decision == "APPROVE" else 0)
        agent["rejected_count"] = int(agent["rejected_count"]) + (
            1 if decision == "REJECT" else 0)
        agent["escalated_count"] = int(agent["escalated_count"]) + (
            1 if decision == "ESCALATE" else 0)
        if decision == "REJECT":
            agent["pending_exposure_millis"] = (
                int(agent["pending_exposure_millis"])
                - int(action["amount_millis"]))
        self._put(self.agents, action["agent_id"], agent)

        if decision == "APPROVE":
            self.stat_approved = u256(int(self.stat_approved) + 1)
            self.stat_authorized_millis = u256(
                int(self.stat_authorized_millis)
                + int(action["amount_millis"]))
        elif decision == "REJECT":
            self.stat_rejected = u256(int(self.stat_rejected) + 1)
            self.stat_blocked_millis = u256(
                int(self.stat_blocked_millis)
                + int(action["amount_millis"]))
        else:
            self.stat_escalated = u256(int(self.stat_escalated) + 1)

        DecisionEvent(action_id,
                      decision=decision,
                      risk_score=risk_score,
                      reason_code=reason,
                      policy_version=action["policy_version"],
                      evidence_hash=evidence_hash).emit()
        self._audit({
            "kind": "decision",
            "action_id": action_id,
            "agent_id": action["agent_id"],
            "action_type": action["action_type"],
            "amount_millis": int(action["amount_millis"]),
            "policy_version": action["policy_version"],
            "decision": decision,
            "risk": risk_score,
            "reason_code": reason,
            "evidence_hash": evidence_hash,
            "action_hash": action["action_hash"],
            "execution_status": action["execution_status"],
        })
        return _canonical_json(decision_record)

    def _release_exposure(self, agent: dict, action: dict) -> None:
        pending = int(agent["pending_exposure_millis"]) \
            - int(action["amount_millis"])
        if pending < 0:
            pending = 0
        agent["pending_exposure_millis"] = pending
        self._put(self.agents, action["agent_id"], agent)

    # ------------------------------------------------------------------
    # DETERMINISTIC ENFORCEMENT — the only path that authorizes execution
    # ------------------------------------------------------------------
    @gl.public.write
    def execute_action(self, action_id: str) -> str:
        if action_id not in self.actions:
            raise AssertionError("action_not_found")
        action = self._load(self.actions, action_id)
        if action["status"] == "EXECUTED":
            raise AssertionError("already_executed")
        if action["status"] != "APPROVED":
            raise AssertionError("not_authorized:status=" + action["status"])
        if self.protocol_paused:
            raise AssertionError("protocol_paused")

        agent = self._load(self.agents, action["agent_id"])
        pol = self._load(self.policies, action["policy_id"])
        now = self._now()

        # Re-verify EVERYTHING — never trust a stale approval.
        # Guard failures that must leave ALL state untouched RAISE
        # (revert). Outcomes that are legitimate enforcement results
        # persist a terminal BLOCKED status (spec lifecycle state) and
        # return normally.
        # 1. expiry -> revert; the permissionless sweep persists EXPIRED
        if now > int(action["expires_at"]):
            raise AssertionError("authorization_expired")
        # 2. policy version — a v1 approval DIES when v2 is live.
        #    This is a legitimate terminal outcome: persist BLOCKED.
        if pol["version_label"] != action["policy_version"]:
            action["status"] = "BLOCKED"
            action["execution_status"] = "stale_policy"
            self._put(self.actions, action_id, action)
            self._release_exposure(agent, action)
            self._audit({"kind": "execution_blocked",
                         "action_id": action_id,
                         "agent_id": action["agent_id"],
                         "reason": "policy_superseded:approved_under:"
                                   + action["policy_version"]
                                   + ":live:" + pol["version_label"],
                         "decision": "APPROVE",
                         "action_hash": action["action_hash"]})
            return _canonical_json({"action_id": action_id,
                                    "status": "BLOCKED",
                                    "blocked_reason": "policy_superseded",
                                    "approved_under": action["policy_version"],
                                    "live_policy": pol["version_label"]})
        # 3. deterministic budget (node-clock day window) — terminal BLOCK
        self._roll_day_window(agent, now)
        if int(agent["daily_spent_millis"]) + int(action["amount_millis"]) \
                > int(pol["daily_budget_millis"]):
            action["status"] = "BLOCKED"
            action["execution_status"] = "budget_exceeded"
            self._put(self.actions, action_id, action)
            self._audit({"kind": "execution_blocked",
                         "action_id": action_id,
                         "agent_id": action["agent_id"],
                         "reason": "daily_budget_exceeded_at_execution",
                         "decision": "APPROVE",
                         "action_hash": action["action_hash"]})
            return _canonical_json({"action_id": action_id,
                                    "status": "BLOCKED",
                                    "blocked_reason":
                                        "daily_budget_exceeded"})
        # 4. cooldown (transient execution rate limit) -> revert
        if int(agent["last_executed_at"]) > 0:
            elapsed = now - int(agent["last_executed_at"])
            if elapsed < int(pol["cooldown_seconds"]):
                raise AssertionError(
                    "cooldown_active:"
                    + str(int(pol["cooldown_seconds"]) - elapsed))
        # 5. action hash integrity — recompute from the stored intent
        #    (tamper alarm -> revert, no state change)
        recomputed = _keccak256_hex_of_string(_canonical_json({
            "agent_id": action["agent_id"],
            "action_type": action["action_type"],
            "target": action["target"].lower(),
            "amount_millis": int(action["amount_millis"]),
            "asset": action["asset"],
            "parameters": action["parameters"],
            "reason": action["reason"],
            "policy_id": action["policy_id"],
            "policy_version": action["policy_version"],
            "nonce": int(action["nonce"]),
            "created_at": int(action["created_at"]),
            "expires_at": int(action["expires_at"]),
        }))
        if recomputed != action["action_hash"]:
            raise AssertionError("action_hash_mismatch")

        # ---- COMMIT — checks-effects order -----------------------------
        agent["daily_spent_millis"] = (
            int(agent["daily_spent_millis"])
            + int(action["amount_millis"]))
        agent["total_spent_millis"] = (
            int(agent["total_spent_millis"])
            + int(action["amount_millis"]))
        agent["pending_exposure_millis"] = max(
            0, int(agent["pending_exposure_millis"])
            - int(action["amount_millis"]))
        agent["executed_count"] = int(agent["executed_count"]) + 1
        agent["last_executed_at"] = now
        self._put(self.agents, action["agent_id"], agent)

        action["status"] = "EXECUTED"
        action["execution_status"] = "executed"
        action["executed_at"] = now
        self._put(self.actions, action_id, action)
        self.stat_executed = u256(int(self.stat_executed) + 1)

        ActionExecutedEvent(action_id,
                            agent=action["agent_id"],
                            amount_millis=int(action["amount_millis"]),
                            asset=action["asset"],
                            target=action["target"]).emit()
        self._audit({"kind": "action_executed",
                     "action_id": action_id,
                     "agent_id": action["agent_id"],
                     "action_type": action["action_type"],
                     "amount_millis": int(action["amount_millis"]),
                     "policy_version": action["policy_version"],
                     "decision": "APPROVE",
                     "risk": 0,
                     "action_hash": action["action_hash"],
                     "execution_status": "executed"})
        return _canonical_json({"action_id": action_id,
                                "status": "EXECUTED",
                                "executed_at": now})

    def _roll_day_window(self, agent: dict, now: int) -> None:
        if int(agent["day_anchor"]) == 0:
            agent["day_anchor"] = now
        elif now - int(agent["day_anchor"]) >= DAY_SECONDS:
            agent["day_anchor"] = now
            agent["daily_spent_millis"] = 0

    # ------------------------------------------------------------------
    # ESCALATION — human resolution of first-class ESCALATE state
    # ------------------------------------------------------------------
    @gl.public.write
    def resolve_escalation(self, action_id: str, approve: bool,
                           note: str) -> str:
        if str(gl.message.sender_address) != str(self.owner):
            raise AssertionError("not_owner")
        if action_id not in self.actions:
            raise AssertionError("action_not_found")
        action = self._load(self.actions, action_id)
        if action["status"] != "ESCALATED":
            raise AssertionError("not_escalated:status=" + action["status"])
        now = self._now()

        if approve:
            if now > int(action["expires_at"]):
                # human approved too late — persist EXPIRED (terminal)
                action["status"] = "EXPIRED"
                action["execution_status"] = "expired"
                action["resolution"] = "human:approved_but_expired"
                self._put(self.actions, action_id, action)
                self._audit({"kind": "escalation_resolved",
                             "action_id": action_id,
                             "agent_id": action["agent_id"],
                             "resolution": "approved_but_expired"})
                return _canonical_json({"action_id": action_id,
                                        "status": "EXPIRED"})
            action["status"] = "APPROVED"
            action["execution_status"] = "authorized"
            action["resolution"] = "human:approved:" + note[:160]
            # the ESCALATED decision was already counted; a human
            # approval authorizes value without double-counting decided
            self.stat_authorized_millis = u256(
                int(self.stat_authorized_millis)
                + int(action["amount_millis"]))
        else:
            action["status"] = "REJECTED"
            action["execution_status"] = "blocked"
            action["resolution"] = "human:rejected:" + note[:160]
            agent = self._load(self.agents, action["agent_id"])
            agent["rejected_count"] = int(agent["rejected_count"]) + 1
            agent["pending_exposure_millis"] = max(
                0, int(agent["pending_exposure_millis"])
                - int(action["amount_millis"]))
            self._put(self.agents, action["agent_id"], agent)
            self.stat_blocked_millis = u256(
                int(self.stat_blocked_millis)
                + int(action["amount_millis"]))
        self._put(self.actions, action_id, action)
        self._audit({"kind": "escalation_resolved",
                     "action_id": action_id,
                     "agent_id": action["agent_id"],
                     "resolution": ("human_approved" if approve
                                    else "human_rejected"),
                     "note": note[:160]})
        return _canonical_json({"action_id": action_id,
                                "status": action["status"]})

    # ------------------------------------------------------------------
    # EMERGENCY CIRCUIT BREAKER
    # ------------------------------------------------------------------
    @gl.public.write
    def pause_protocol(self) -> None:
        if str(gl.message.sender_address) != str(self.owner):
            raise AssertionError("not_owner")
        if self.protocol_paused:
            raise AssertionError("already_paused")
        self.protocol_paused = True
        self._audit({"kind": "protocol_paused",
                     "by": str(gl.message.sender_address)})
        ProtocolPausedEvent(by=str(gl.message.sender_address),
                            paused=True).emit()

    @gl.public.write
    def unpause_protocol(self) -> None:
        if str(gl.message.sender_address) != str(self.owner):
            raise AssertionError("not_owner")
        if not self.protocol_paused:
            raise AssertionError("not_paused")
        self.protocol_paused = False
        self._audit({"kind": "protocol_resumed",
                     "by": str(gl.message.sender_address)})
        ProtocolPausedEvent(by=str(gl.message.sender_address),
                            paused=False).emit()

    # ------------------------------------------------------------------
    # EXPIRY SWEEP (permissionless)
    # ------------------------------------------------------------------
    @gl.public.write
    def expire_action(self, action_id: str) -> str:
        if action_id not in self.actions:
            raise AssertionError("action_not_found")
        action = self._load(self.actions, action_id)
        now = self._now()
        if action["status"] in ("EXECUTED", "REJECTED", "EXPIRED"):
            raise AssertionError("already_final:status=" + action["status"])
        if now <= int(action["expires_at"]):
            raise AssertionError("not_yet_expired")
        agent = self._load(self.agents, action["agent_id"])
        action["status"] = "EXPIRED"
        action["execution_status"] = "expired"
        self._put(self.actions, action_id, action)
        self._release_exposure(agent, action)
        self._audit({"kind": "action_expired",
                     "action_id": action_id,
                     "agent_id": action["agent_id"],
                     "reason": "swept_past_expiry"})
        return _canonical_json({"action_id": action_id,
                                "status": "EXPIRED"})

    # ------------------------------------------------------------------
    # VIEWS
    # ------------------------------------------------------------------
    @gl.public.view
    def get_action(self, action_id: str) -> str:
        if action_id not in self.actions:
            return _canonical_json({"error": "not_found"})
        return self.actions[action_id]

    @gl.public.view
    def get_decision(self, action_id: str) -> str:
        if action_id not in self.decisions:
            return _canonical_json({"error": "not_found"})
        return self.decisions[action_id]

    @gl.public.view
    def get_policy(self, policy_id: str) -> str:
        if policy_id not in self.policies:
            return _canonical_json({"error": "not_found"})
        return self.policies[policy_id]

    @gl.public.view
    def get_agent(self, agent_id: str) -> str:
        if agent_id not in self.agents:
            return _canonical_json({"error": "not_found"})
        return self.agents[agent_id]

    @gl.public.view
    def get_registry_info(self) -> str:
        return _canonical_json({
            "registry_url": self.registry_url,
            "registry_keccak": self.registry_keccak,
            "registry_ref": self.registry_ref,
        })

    @gl.public.view
    def get_protocol_stats(self) -> str:
        total = int(self.stat_actions_total)
        approved = int(self.stat_approved)
        rejected = int(self.stat_rejected)
        escalated = int(self.stat_escalated)
        decided = approved + rejected + escalated
        return _canonical_json({
            "protocol_paused": bool(self.protocol_paused),
            "owner": str(self.owner),
            "total_actions": total,
            "approved": approved,
            "rejected": rejected,
            "escalated": escalated,
            "executed": int(self.stat_executed),
            "decided": decided,
            "pending": max(0, total - decided),
            "authorized_millis": int(self.stat_authorized_millis),
            "blocked_millis": int(self.stat_blocked_millis),
            "approval_rate": _rate(approved, decided),
            "rejection_rate": _rate(rejected, decided),
            "escalation_rate": _rate(escalated, decided),
            "next_action_seq": int(self.next_action_seq),
            "audit_count": min(MAX_AUDIT_ENTRIES,
                               int(self.next_audit_seq) - AUDIT_START),
        })

    @gl.public.view
    def get_audit_page(self, start_seq: int, count: int) -> str:
        out = []
        total = int(self.next_audit_seq)
        start = max(0, int(start_seq))
        n = int(count)
        if n > 50:
            n = 50
        if n < 0:
            n = 0
        seq = start
        while seq < total and len(out) < n:
            key = "A" + str(seq).zfill(6)
            if key in self.audit_records:
                out.append(json.loads(self.audit_records[key]))
            seq += 1
        return _canonical_json({"entries": out,
                                "next_seq": seq,
                                "total": total})

    @gl.public.view
    def get_agent_action_ids(self, agent_id: str) -> str:
        if agent_id not in self.agents:
            return _canonical_json({"error": "not_found"})
        out = []
        seq = 1
        total = int(self.next_action_seq)
        while seq < total:
            action_id = "ACT-" + str(seq).zfill(6)
            if action_id in self.actions:
                rec = json.loads(self.actions[action_id])
                if rec["agent_id"] == agent_id:
                    out.append(action_id)
            seq += 1
        return _canonical_json({"agent_id": agent_id,
                                "action_ids": out})

    @gl.public.view
    def list_agents(self) -> str:
        out = []
        if self.agent_id_list:
            for agent_id in self.agent_id_list.split(","):
                if agent_id in self.agents:
                    out.append(json.loads(self.agents[agent_id]))
        return _canonical_json({"agents": out})

    @gl.public.view
    def list_policies(self) -> str:
        out = []
        if self.policy_id_list:
            for policy_id in self.policy_id_list.split(","):
                if policy_id in self.policies:
                    out.append(json.loads(self.policies[policy_id]))
        return _canonical_json({"policies": out})

    @gl.public.view
    def list_actions(self, count: int) -> str:
        out = []
        seq = int(self.next_action_seq) - 1
        n = int(count)
        if n > 60:
            n = 60
        while seq >= 1 and len(out) < n:
            action_id = "ACT-" + str(seq).zfill(6)
            if action_id in self.actions:
                out.append(json.loads(self.actions[action_id]))
            seq -= 1
        return _canonical_json({"actions": out})


def _append_id(existing: str, new_id: str) -> str:
    """Append to a comma-separated discovery list (idempotent)."""
    if not existing:
        return new_id
    for part in existing.split(","):
        if part == new_id:
            return existing
    return existing + "," + new_id


def _rate(part: int, whole: int) -> int:
    if whole <= 0:
        return 0
    return int(100 * part / whole)
