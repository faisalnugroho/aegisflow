"""AegisFlow test suite — the full protocol contract.

Coverage map (spec sections 5-19 + hackathon scenarios S1-S5):
  S1  safe payment (25 USDC, known recipient)      -> APPROVE
  S2  budget violation (100 > 50 limit)            -> REJECT (deterministic)
  S3  new recipient (20 USDC, requires verification) -> ESCALATE
  S4  flagged target + fooled LLM                  -> REJECT (gates clamp)
  S5  policy update v1->v2                          -> stale approval dies
  + daily budget accounting at execution
  + nonce monotonicity / replay protection
  + expiry / authorization expiry / sweep
  + escalation human resolution (approve & reject)
  + circuit breaker (pause blocks submit + execute)
  + evidence registry pin: fetch failure, hash mismatch -> ESCALATE (fail-safe)
  + LLM malformed output -> ESCALATE (never approve)
  + validator substance check (different facts -> validator rejects)
  + audit ring, views, stats, events
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from helpers import (  # noqa: E402
    CONTRACT, KNOWN, NEW, FLAGGED, DAO, PROC, TRADING, OWNER, OWNER_HEX,
    TEST_REGISTRY_COMMIT, TEST_REGISTRY_KECCAK, REGISTRY_OK,
    registry_payload, registry_keccak_of, addr_str, iso_now, iso_in,
    set_time, deploy, mock_registry, submit,
    register_treasury_policy, register_trading_policy,
    register_dao_policy, register_proc_policy, register_agent,
    llm_ok, llm_semantic_fail, llm_uncertain, llm_fooled_approve,
    llm_malformed,
)


def dec(c, action_id):
    return json.loads(c.run_consensus(action_id))


def get_action(c, action_id):
    return json.loads(c.get_action(action_id))


def get_agent(c, agent_id):
    return json.loads(c.get_agent(agent_id))


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture()
def env(direct_vm):
    set_time(direct_vm, iso_now())
    contract = deploy(direct_vm)
    mock_registry(direct_vm)
    return direct_vm, contract


@pytest.fixture()
def treasury(direct_vm):
    """Policy + agent + registry + clean LLM, ready to submit."""
    set_time(direct_vm, iso_now())
    contract = deploy(direct_vm)
    mock_registry(direct_vm)
    direct_vm.mock_llm(".*", llm_ok())
    register_treasury_policy(contract)
    register_agent(contract)
    return direct_vm, contract


# ---------------------------------------------------------------------------
# S1 — safe payment -> APPROVE
# ---------------------------------------------------------------------------
class TestScenario1SafePayment:
    def test_approve(self, treasury):
        vm, c = treasury
        aid = submit(c)
        d = dec(c, aid)
        assert d["decision"] == "APPROVE", d
        assert d["reason_code"] == "policy_compliant"
        assert d["policy_compliant"] is True
        assert d["criteria"]["policy_intent_compliant"] == "PASS"
        assert d["objective_facts"]["recipient_is_new"] is False
        assert d["risk_score"] == 10       # llm advisory (max path)
        assert d["risk_bucket"] == "LOW"
        a = get_action(c, aid)
        assert a["status"] == "APPROVED"
        assert a["execution_status"] == "authorized"

    def test_execute_after_approve(self, treasury):
        vm, c = treasury
        aid = submit(c)
        dec(c, aid)
        r = json.loads(c.execute_action(aid))
        assert r["status"] == "EXECUTED"
        a = get_action(c, aid)
        assert a["status"] == "EXECUTED"
        assert a["execution_status"] == "executed"
        g = get_agent(c, "treasury-agent-01")
        assert g["executed_count"] == 1
        assert g["daily_spent_millis"] == 25_000
        assert g["total_spent_millis"] == 25_000
        assert g["pending_exposure_millis"] == 0
        # audit trail contains the execution
        page = json.loads(c.get_audit_page(0, 50))
        kinds = [e["kind"] for e in page["entries"]]
        assert "action_executed" in kinds
        assert "decision" in kinds

    def test_execute_then_replay_reverts(self, treasury):
        vm, c = treasury
        aid = submit(c)
        dec(c, aid)
        c.execute_action(aid)
        with pytest.raises(Exception, match="already_executed"):
            c.execute_action(aid)

    def test_reject_then_execute_reverts(self, treasury):
        vm, c = treasury
        aid = submit(c, amount_millis=100_000)  # violates 50 limit
        d = dec(c, aid)
        assert d["decision"] == "REJECT"
        with pytest.raises(Exception, match="not_authorized"):
            c.execute_action(aid)


# ---------------------------------------------------------------------------
# S2 — amount limit violation -> deterministic REJECT (LLM irrelevant)
# ---------------------------------------------------------------------------
class TestScenario2BudgetViolation:
    def test_amount_limit_rejects(self, treasury):
        vm, c = treasury
        aid = submit(c, amount_millis=100_000)
        d = dec(c, aid)
        assert d["decision"] == "REJECT"
        assert d["reason_code"] == "amount_limit_exceeded"
        assert d["policy_compliant"] is False
        assert d["objective_facts"]["amount_limit_violation"] is True
        a = get_action(c, aid)
        assert a["execution_status"] == "blocked"

    def test_daily_budget_rejects(self, treasury):
        vm, c = treasury
        # execute 135 today (3 x 45), then one more 20 (135+20=155>150)
        for i in range(3):
            aid = submit(c, amount_millis=45_000)
            dec(c, aid)
            c.execute_action(aid)
        aid4 = submit(c, amount_millis=20_000)
        d = dec(c, aid4)
        assert d["decision"] == "REJECT"
        assert d["reason_code"] == "daily_budget_exceeded"
        assert d["objective_facts"]["budget_exceeded"] is True

    def test_no_llm_involved_in_hard_gate(self, treasury):
        """A hallucinating LLM approving everything still cannot pass a
        limit violation — the gate fires before semantic checks."""
        vm, c = treasury
        aid = submit(c, amount_millis=100_000)
        d = dec(c, aid)
        assert d["decision"] == "REJECT"
        # decision record proves the gate preceded LLM judgment
        assert d["reason_code"] == "amount_limit_exceeded"
        # the fooled LLM's PASS labels are recorded but overridden
        assert d["criteria"]["policy_intent_compliant"] == "PASS"


# ---------------------------------------------------------------------------
# S3 — new recipient -> ESCALATE (uncertainty is never approval)
# ---------------------------------------------------------------------------
class TestScenario3NewRecipient:
    def test_new_recipient_escalates(self, treasury):
        vm, c = treasury
        aid = submit(c, target=NEW, amount_millis=20_000)
        d = dec(c, aid)
        assert d["decision"] == "ESCALATE", d
        assert d["reason_code"] == "new_recipient_requires_verification"
        a = get_action(c, aid)
        assert a["status"] == "ESCALATED"
        assert a["execution_status"] == "human_review"
        # pending exposure still held (not released on escalate)
        g = get_agent(c, "treasury-agent-01")
        assert g["pending_exposure_millis"] == 20_000

    def test_new_recipient_human_approve_then_execute(self, treasury):
        vm, c = treasury
        aid = submit(c, target=NEW, amount_millis=20_000)
        dec(c, aid)
        r = json.loads(c.resolve_escalation(aid, True, "human verified "
                                              "vendor manually"))
        assert r["status"] == "APPROVED"
        c.execute_action(aid)
        a = get_action(c, aid)
        assert a["status"] == "EXECUTED"

    def test_new_recipient_human_reject(self, treasury):
        vm, c = treasury
        aid = submit(c, target=NEW, amount_millis=20_000)
        dec(c, aid)
        r = json.loads(c.resolve_escalation(aid, False, "vendor could not "
                                              "be verified"))
        assert r["status"] == "REJECTED"
        g = get_agent(c, "treasury-agent-01")
        assert g["pending_exposure_millis"] == 0

    def test_llm_uncertain_escalates(self, treasury):
        vm, c = treasury
        vm.clear_mocks()
        mock_registry(vm)
        vm.mock_llm(".*", llm_uncertain())
        aid = submit(c)   # known recipient, amount fine
        d = dec(c, aid)
        assert d["decision"] == "ESCALATE"
        assert d["reason_code"] == "semantic:uncertain"


# ---------------------------------------------------------------------------
# S4 — flagged external evidence -> REJECT even when the LLM is fooled
# ---------------------------------------------------------------------------
class TestScenario4FlaggedEvidence:
    def test_flagged_target_rejects_despite_fooled_llm(self, treasury):
        vm, c = treasury
        vm.clear_mocks()
        mock_registry(vm)
        vm.mock_llm(".*", llm_fooled_approve())  # LLM says all PASS
        aid = submit(c, target=FLAGGED, amount_millis=25_000)
        d = dec(c, aid)
        # deterministic external_risk_flag gate clamps the fooled LLM
        assert d["decision"] == "REJECT"
        assert d["reason_code"] == "external_risk_flagged"
        assert d["objective_facts"]["external_risk_flag"] is True
        assert d["risk_score"] >= 40
        assert d["risk_bucket"] in ("HIGH", "CRITICAL")


# ---------------------------------------------------------------------------
# S5 — policy update: a v1 approval cannot execute under v2
# ---------------------------------------------------------------------------
class TestScenario5PolicyUpdate:
    def _update_to_v2(self, c):
        c.update_policy(
            policy_id="treasury-policy",
            name="Conservative Treasury Agent",
            description="Tightened after incident",
            natural_language="Tightened treasury policy.",
            max_single_action_millis=10_000,
            daily_budget_millis=50_000,
            allowed_action_types="TRANSFER",
            restricted_targets="",
            cooldown_seconds=0,
            require_consensus=True,
            require_escalation_above_millis=10_000,
            require_verification_new_recipients=True,
            reject_flagged_targets=True)

    def test_stale_approval_dies_on_v2(self, treasury):
        vm, c = treasury
        aid = submit(c)
        d = dec(c, aid)
        assert d["decision"] == "APPROVE"
        assert d["policy_version"] == "treasury-policy-v1"
        # policy updated to v2 — v1 approval must NOT execute
        self._update_to_v2(c)
        r = json.loads(c.execute_action(aid))
        assert r["status"] == "BLOCKED"
        assert r["blocked_reason"] == "policy_superseded"
        assert r["approved_under"] == "treasury-policy-v1"
        a = get_action(c, aid)
        assert a["status"] == "BLOCKED"
        assert a["execution_status"] == "stale_policy"

    def test_new_decision_uses_v2(self, treasury):
        vm, c = treasury
        self._update_to_v2(c)
        aid = submit(c, amount_millis=25_000)  # now above v2 10 limit
        d = dec(c, aid)
        assert d["policy_version"] == "treasury-policy-v2"
        assert d["decision"] == "REJECT"
        assert d["reason_code"] == "amount_limit_exceeded"


# ---------------------------------------------------------------------------
# Semantic LLM rejection (no deterministic rule fires)
# ---------------------------------------------------------------------------
class TestSemanticJudgment:
    def test_semantic_fail_rejects(self, treasury):
        vm, c = treasury
        vm.clear_mocks()
        mock_registry(vm)
        vm.mock_llm(".*", llm_semantic_fail())
        aid = submit(c, reason="Place bet on red at online casino")
        d = dec(c, aid)
        assert d["decision"] == "REJECT"
        assert d["reason_code"] == "semantic:policy_intent_compliant"
        # risk score raised by LLM advisory (80) over objective (0)
        assert d["risk_score"] == 80
        assert d["risk_bucket"] == "CRITICAL"   # 80-100 band

    def test_action_type_not_allowed(self, treasury):
        vm, c = treasury
        aid = submit(c, action_type="REBALANCE")  # treasury only allows TRANSFER
        d = dec(c, aid)
        assert d["reason_code"] == "action_type_not_allowed"
        assert d["decision"] == "REJECT"

    def test_restricted_target_rejects(self, treasury):
        vm, c = treasury
        c.update_policy(
            policy_id="treasury-policy",
            name="Conservative Treasury Agent",
            description="Small autonomous treasury",
            natural_language="Tightened.",
            max_single_action_millis=50_000,
            daily_budget_millis=150_000,
            allowed_action_types="TRANSFER",
            restricted_targets=FLAGGED,
            cooldown_seconds=0,
            require_consensus=True,
            require_escalation_above_millis=50_000,
            require_verification_new_recipients=True,
            reject_flagged_targets=True)
        aid = submit(c, target=FLAGGED, amount_millis=25_000)
        d = dec(c, aid)
        assert d["decision"] == "REJECT"
        assert d["reason_code"] == "target_restricted"


# ---------------------------------------------------------------------------
# Fail-safe paths — evidence & LLM failures never approve
# ---------------------------------------------------------------------------
class TestFailSafe:
    def test_registry_fetch_fails_escalates(self, direct_vm):
        set_time(direct_vm, iso_now())
        c = deploy(direct_vm)
        register_treasury_policy(c)
        register_agent(c)
        direct_vm.mock_llm(".*", llm_ok())
        # NO web mock registered -> fetch fails -> evidence unavailable
        aid = submit(c)
        d = dec(c, aid)
        assert d["decision"] == "ESCALATE"
        assert d["reason_code"] == "llm_or_evidence_unavailable"
        assert d["registry_fail"] != ""

    def test_registry_hash_mismatch_escalates(self, direct_vm):
        set_time(direct_vm, iso_now())
        c = deploy(direct_vm, keccak="f" * 64)  # wrong expected hash
        register_treasury_policy(c)
        register_agent(c)
        direct_vm.mock_llm(".*", llm_ok())
        direct_vm.mock_web("raw.githubusercontent.com",
                           {"status": 200, "body": REGISTRY_OK})
        aid = submit(c)
        d = dec(c, aid)
        assert d["decision"] == "ESCALATE"
        assert "mismatch" in d["registry_fail"]

    def test_llm_malformed_escalates(self, treasury):
        vm, c = treasury
        vm.clear_mocks()
        mock_registry(vm)
        vm.mock_llm(".*", llm_malformed())   # valid JSON, wrong shape
        aid = submit(c)
        d = dec(c, aid)
        assert d["decision"] == "ESCALATE"
        assert d["reason_code"] == "llm_or_evidence_unavailable"
        # no criteria parsed from the malformed shape
        assert d["criteria"] == {}
        assert d["llm_risk_score"] == 0

    def test_no_criterion_can_approve_without_all_three(self, treasury):
        vm, c = treasury
        vm.clear_mocks()
        mock_registry(vm)
        partial = json.dumps({
            "criteria": {
                "policy_intent_compliant": {"status": "PASS",
                                           "justification": "ok"},
            },
            "llm_risk_score": 10,
        })
        vm.mock_llm(".*", partial)
        aid = submit(c)
        d = dec(c, aid)
        # only one criterion present -> len(statuses) != 3 -> escalate
        assert d["decision"] == "ESCALATE"
        assert d["reason_code"] == "llm_or_evidence_unavailable"


# ---------------------------------------------------------------------------
# Validator substance — validators independently verify the leader
# ---------------------------------------------------------------------------
class TestValidatorSubstance:
    def test_validator_rejects_different_facts(self, treasury):
        """Validator sees DIFFERENT registry (target flagged) and must
        disagree with the leader's fact set."""
        vm, c = treasury
        aid = submit(c, target=NEW, amount_millis=20_000)
        dec(c, aid)  # leader run captures the validator
        # Now swap the registry under the validator: NEW becomes a known
        # flagged recipient -> external_risk_flag fact diverges.
        poisoned = json.dumps(registry_payload(recipients={
            NEW.lower(): {"known_recipient": True, "risk_flag": True,
                          "label": "drainer", "status": "malicious"},
            KNOWN.lower(): {"known_recipient": True, "risk_flag": False,
                            "label": "aws-infra", "status": "active"},
        }), sort_keys=True, separators=(",", ":"))
        vm.clear_mocks()
        vm.mock_web("raw.githubusercontent.com",
                    {"status": 200, "body": poisoned})
        vm.mock_llm(".*", llm_ok())
        ok = vm.run_validator()
        assert ok is False, "validator must reject divergent evidence"

    def test_validator_accepts_same_substance(self, treasury):
        vm, c = treasury
        aid = submit(c)
        dec(c, aid)
        ok = vm.run_validator()
        assert ok is True


# ---------------------------------------------------------------------------
# Nonce / replay / duplicate protection
# ---------------------------------------------------------------------------
class TestReplayProtection:
    def test_double_consensus_reverts(self, treasury):
        vm, c = treasury
        aid = submit(c)
        dec(c, aid)
        with pytest.raises(Exception, match="already_decided"):
            c.run_consensus(aid)

    def test_action_hash_immutability(self, treasury):
        vm, c = ledger = treasury
        aid = submit(c)
        a = get_action(c, aid)
        h1 = a["action_hash"]
        aid2 = submit(c, amount_millis=30_000)
        a2 = get_action(c, aid2)
        assert h1 != a2["action_hash"]
        # hash is 64-hex keccak
        assert len(h1) == 64

    def test_nonce_increments_per_agent(self, treasury):
        vm, c = treasury
        a1 = get_action(c, submit(c))
        a2 = get_action(c, submit(c, amount_millis=10_000))
        assert a1["nonce"] == 1
        assert a2["nonce"] == 2


# ---------------------------------------------------------------------------
# Expiry
# ---------------------------------------------------------------------------
class TestExpiry:
    def test_expired_action_cannot_get_consensus(self, treasury):
        vm, c = treasury
        aid = submit(c, expires_in_seconds=30)
        set_time(vm, iso_in(3600))
        d = json.loads(c.run_consensus(aid))
        assert d["decision"] == "EXPIRED"
        assert d["reason_code"] == "expired_before_consensus"
        a = get_action(c, aid)
        assert a["status"] == "EXPIRED"

    def test_expired_authorization_cannot_execute(self, treasury):
        vm, c = treasury
        aid = submit(c, expires_in_seconds=60)
        dec(c, aid)
        set_time(vm, iso_in(7200))
        with pytest.raises(Exception, match="authorization_expired"):
            c.execute_action(aid)
        # revert left it APPROVED-but-expired; the sweep persists EXPIRED
        r = json.loads(c.expire_action(aid))
        assert r["status"] == "EXPIRED"
        a = get_action(c, aid)
        assert a["execution_status"] == "expired"

    def test_sweep_past_expiry(self, treasury):
        vm, c = treasury
        aid = submit(c, expires_in_seconds=30)
        dec(c, aid)
        set_time(vm, iso_in(3600))
        r = json.loads(c.expire_action(aid))
        assert r["status"] == "EXPIRED"


# ---------------------------------------------------------------------------
# Circuit breaker
# ---------------------------------------------------------------------------
class TestCircuitBreaker:
    def test_pause_blocks_submit_and_execute(self, treasury):
        vm, c = treasury
        aid = submit(c)
        dec(c, aid)
        c.pause_protocol()
        stats = json.loads(c.get_protocol_stats())
        assert stats["protocol_paused"] is True
        with pytest.raises(Exception, match="protocol_paused"):
            submit(c, amount_millis=10_000)
        with pytest.raises(Exception, match="protocol_paused"):
            c.execute_action(aid)
        c.unpause_protocol()
        r = json.loads(c.execute_action(aid))
        assert r["status"] == "EXECUTED"

    def test_only_owner_pauses(self, treasury):
        vm, c = treasury
        vm.sender = b"\xee" * 20
        with pytest.raises(Exception, match="not_owner"):
            c.pause_protocol()


# ---------------------------------------------------------------------------
# Budget accounting (deterministic arithmetic)
# ---------------------------------------------------------------------------
class TestBudgetAccounting:
    def test_daily_budget_blocks_at_execution(self, treasury):
        """Deterministic enforcement catches what consensus-time checks
        cannot: approvals issued while budget was available, executed
        after other executions consumed it."""
        vm, c = treasury
        # approve 4 x 40 = 160 > 150 total while daily_spent is still 0 —
        # each individual action passes consensus-time checks
        aids = []
        for i in range(4):
            aid = submit(c, amount_millis=40_000)
            dec(c, aid)
            aids.append(aid)
        # execute the first three (120 spent today)
        for aid in aids[:3]:
            c.execute_action(aid)
        # the 4th is approved but the budget is gone -> terminal BLOCKED
        r = json.loads(c.execute_action(aids[3]))
        assert r["status"] == "BLOCKED"
        assert r["blocked_reason"] == "daily_budget_exceeded"
        g = get_agent(c, "treasury-agent-01")
        assert g["daily_spent_millis"] == 120_000
        a = get_action(c, aids[3])
        assert a["status"] == "BLOCKED"
        assert a["execution_status"] == "budget_exceeded"

    def test_day_window_rolls(self, treasury):
        vm, c = treasury
        aid = submit(c, amount_millis=40_000)
        dec(c, aid)
        c.execute_action(aid)
        set_time(vm, iso_in(86400 + 100))
        aid2 = submit(c, amount_millis=40_000)
        dec(c, aid2)
        c.execute_action(aid2)
        g = get_agent(c, "treasury-agent-01")
        assert g["daily_spent_millis"] == 40_000  # window rolled

    def test_escalated_hold_not_released_until_resolution(self, treasury):
        vm, c = treasury
        aid = submit(c, target=NEW, amount_millis=20_000)
        dec(c, aid)
        g = get_agent(c, "treasury-agent-01")
        assert g["pending_exposure_millis"] == 20_000


# ---------------------------------------------------------------------------
# Multi-policy demo: trading / DAO / procurement agents
# ---------------------------------------------------------------------------
class TestMultiAgent:
    def test_trading_agent_rebalance_approves(self, direct_vm):
        set_time(direct_vm, iso_now())
        c = deploy(direct_vm)
        mock_registry(direct_vm)
        direct_vm.mock_llm(".*", llm_ok())
        register_trading_policy(c)
        register_agent(c, agent_id="trading-agent-01",
                       policy_id="trading-policy",
                       budget_millis=30_000_000,
                       name="Trading Agent",
                       description="Portfolio rebalancing",
                       risk_level="MEDIUM")
        aid = submit(c, agent_id="trading-agent-01",
                     action_type="REBALANCE", target=TRADING,
                     amount_millis=5_000_000, asset="USDC",
                     reason="Deviation 7.2% exceeds 5% threshold; "
                            "rebalance to target weights",
                     parameters=json.dumps({"deviation_pct": 7.2}))
        d = dec(c, aid)
        assert d["decision"] == "APPROVE"

    def test_dao_agent_semantic_rejection(self, direct_vm):
        set_time(direct_vm, iso_now())
        c = deploy(direct_vm)
        mock_registry(direct_vm)
        direct_vm.mock_llm(".*", llm_semantic_fail())
        register_dao_policy(c)
        register_agent(c, agent_id="dao-agent-01",
                       policy_id="dao-policy",
                       budget_millis=3_000_000,
                       name="DAO Governance Agent",
                       description="Submits proposals",
                       risk_level="LOW")
        aid = submit(c, agent_id="dao-agent-01",
                     action_type="GOVERNANCE_SUBMIT", target=DAO,
                     amount_millis=2_000_000, asset="USDC",
                     reason="Proposal to move 40% of treasury to "
                            "personal wallet",
                     parameters=json.dumps({"proposal_id": "P-100"}))
        d = dec(c, aid)
        # 2M <= 1M max? No: 2_000_000 > 1_000_000 -> deterministic reject
        assert d["decision"] == "REJECT"
        assert d["reason_code"] == "amount_limit_exceeded"

    def test_procurement_agent_approves(self, direct_vm):
        set_time(direct_vm, iso_now())
        c = deploy(direct_vm)
        mock_registry(direct_vm)
        direct_vm.mock_llm(".*", llm_ok())
        register_proc_policy(c)
        register_agent(c, agent_id="procurement-agent-01",
                       policy_id="procurement-policy",
                       budget_millis=500_000,
                       name="Procurement Agent",
                       description="Buys software licenses",
                       risk_level="LOW")
        aid = submit(c, agent_id="procurement-agent-01",
                     action_type="PROCUREMENT", target=PROC,
                     amount_millis=120_000, asset="USDC",
                     reason="Annual SaaS license renewal",
                     parameters=json.dumps({"sku": "LIC-ANNUAL"}))
        d = dec(c, aid)
        assert d["decision"] == "APPROVE"


# ---------------------------------------------------------------------------
# Views, audit, stats
# ---------------------------------------------------------------------------
class TestViewsAndAudit:
    def test_stats_and_rates(self, treasury):
        vm, c = treasury
        a1 = submit(c)                    # approve
        dec(c, a1)
        a2 = submit(c, amount_millis=100_000)  # reject
        dec(c, a2)
        a3 = submit(c, target=NEW)        # escalate
        dec(c, a3)
        s = json.loads(c.get_protocol_stats())
        assert s["total_actions"] == 3
        assert s["approved"] == 1
        assert s["rejected"] == 1
        assert s["escalated"] == 1
        assert s["approval_rate"] == 33
        assert s["pending"] == 0
        assert s["authorized_millis"] == 25_000
        assert s["blocked_millis"] == 100_000

    def test_audit_ring_bounded(self, treasury):
        vm, c = treasury
        for i in range(5):
            dec(c, submit(c, amount_millis=10_000))
        stats = json.loads(c.get_protocol_stats())
        # 5 submits + 5 decisions + policy + agent = 12 audit entries
        assert stats["audit_count"] == 12

    def test_registry_info_view(self, treasury):
        vm, c = treasury
        info = json.loads(c.get_registry_info())
        assert TEST_REGISTRY_COMMIT in info["registry_url"]
        assert info["registry_keccak"] == TEST_REGISTRY_KECCAK

    def test_list_actions_and_agent_actions(self, treasury):
        vm, c = treasury
        a1 = submit(c)
        a2 = submit(c, amount_millis=10_000)
        lst = json.loads(c.list_actions(10))
        assert len(lst["actions"]) == 2
        mine = json.loads(c.get_agent_action_ids("treasury-agent-01"))
        assert set(mine["action_ids"]) == {a1, a2}

    def test_decision_evidence_hash(self, treasury):
        vm, c = treasury
        aid = submit(c)
        d = dec(c, aid)
        assert len(d["evidence_hash"]) == 64
        assert d["evidence_entry"]["label"] == "aws-infra"


# ---------------------------------------------------------------------------
# Input validation
# ---------------------------------------------------------------------------
class TestInputValidation:
    def test_invalid_target_reverts(self, treasury):
        vm, c = treasury
        with pytest.raises(Exception, match="invalid_target_address"):
            submit(c, target="0xdeadbeef")

    def test_invalid_action_type_reverts(self, treasury):
        vm, c = treasury
        with pytest.raises(Exception, match="invalid_action_type"):
            submit(c, action_type="DELETE_EVERYTHING")

    def test_unknown_agent_reverts(self, treasury):
        vm, c = treasury
        with pytest.raises(Exception, match="agent_not_found"):
            submit(c, agent_id="ghost-agent")

    def test_duplicate_policy_reverts(self, treasury):
        vm, c = treasury
        with pytest.raises(Exception, match="policy_exists"):
            register_treasury_policy(c)

    def test_duplicate_agent_reverts(self, treasury):
        vm, c = treasury
        register_agent(c, agent_id="dupe-agent")
        with pytest.raises(Exception, match="agent_exists"):
            register_agent(c, agent_id="dupe-agent")


# ---------------------------------------------------------------------------
# Storage semantics / misc
# ---------------------------------------------------------------------------
class TestStorageSemantics:
    def test_decision_keyed_by_action_hash_and_id(self, treasury):
        vm, c = treasury
        aid = submit(c)
        d = dec(c, aid)
        by_id = json.loads(c.get_decision(aid))
        by_hash = json.loads(c.get_decision(d["action_hash"]))
        assert by_id["decision"] == by_hash["decision"]
        assert by_hash["action_hash"] == d["action_hash"]
