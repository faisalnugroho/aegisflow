"""Shared test helpers for AegisFlow direct-mode tests.

Proven patterns from TrustReconciler/SecondHandCarInspectionEscrow
(both portal-accepted): set_time + message_raw patch for clock control;
mock_web in DICT format (string bodies silently fetch empty!);
mock_llm first-match-wins (clear_mocks between verdicts); addr_str for
EIP-55 comparisons; vendor_keccak byte-identical to the SDK's
genlayer/py/keccak.py.
"""
import json
import sys
import time
from pathlib import Path

from eth_utils import to_checksum_address

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

CONTRACT = "contracts/AegisFlow.py"

# ---------------------------------------------------------------------------
# Registry pin for TESTS: fake 40-hex commit + keccak of the MOCKED
# registry payload. The pin machinery (URL construction, hash
# verification, ref recording) is exercised on every deploy, exactly as
# the production deploy pins the real commit.
# ---------------------------------------------------------------------------
import vendor_keccak  # noqa: E402  (vendored, byte-identical to SDK)


def registry_payload(recipients=None):
    if recipients is None:
        recipients = {}
    return {
        "description": "AegisFlow external evidence registry (test)",
        "recipients": recipients,
    }


def registry_keccak_of(payload_text):
    return vendor_keccak.Keccak256(
        payload_text.encode("utf-8")).hexdigest()


TEST_REGISTRY_COMMIT = "0" * 39 + "1"


def _ck(hex40):
    return to_checksum_address(hex40)


# Deterministic demo identities (shared with data/demo_identities.json)
KNOWN = _ck("0x" + "10" * 20)      # known infrastructure provider
NEW = _ck("0x" + "20" * 20)        # never-seen recipient
FLAGGED = _ck("0x" + "30" * 20)    # flagged malicious target
DAO = _ck("0x" + "40" * 20)
PROC = _ck("0x" + "50" * 20)
TRADING = _ck("0x" + "60" * 20)

REGISTRY_OK = json.dumps(registry_payload(recipients={
    KNOWN.lower(): {"known_recipient": True, "risk_flag": False,
                    "label": "aws-infra", "status": "active"},
    FLAGGED.lower(): {"known_recipient": False, "risk_flag": True,
                      "label": "phishing-drainer",
                      "status": "malicious"},
    DAO.lower(): {"known_recipient": True, "risk_flag": False,
                  "label": "dao-treasury", "status": "active"},
    PROC.lower(): {"known_recipient": True, "risk_flag": False,
                   "label": "supplier", "status": "active"},
    TRADING.lower(): {"known_recipient": True, "risk_flag": False,
                      "label": "cex-hotwallet", "status": "active"},
}), sort_keys=True, separators=(",", ":"))

# The test pin is the keccak of the payload the tests actually serve —
# the pin machinery verifies content hashes end-to-end.
TEST_REGISTRY_KECCAK = registry_keccak_of(REGISTRY_OK)

# vm.sender must be RAW BYTES (gltest direct mode)
OWNER = b"\xdd" * 20
OWNER_HEX = _ck("0x" + "dd" * 20)
AGENT_OPERATOR = b"\xee" * 20

# ---------------------------------------------------------------------------
# Natural-language policies (mirrors frontend demo text)
# ---------------------------------------------------------------------------
TREASURY_NL = (
    "You are managing a small autonomous treasury. You may execute "
    "infrastructure payments up to 50 USDC per transaction. You may "
    "not spend more than 150 USDC per day. Transactions to new "
    "recipients require additional verification. Any transaction "
    "above 50 USDC must be escalated. Never approve an action if "
    "external evidence indicates the recipient is malicious. Only "
    "infrastructure payments (cloud, hosting, domains, APIs) are "
    "permitted; no personal loans, gambling, or undisclosed purposes."
)

TRADING_NL = (
    "Allow the trading agent to rebalance the portfolio when "
    "deviation exceeds 5 percent, but prohibit exposure above 30 "
    "percent to a single asset. Rebalances must cite the deviation "
    "in their reason."
)

DAO_NL = (
    "Allow an autonomous DAO agent to submit governance proposals. "
    "Block proposals that attempt to transfer more than 10 percent "
    "of treasury funds. Proposal text must state its intent clearly."
)

PROC_NL = (
    "Procurement agent may purchase software licenses and cloud "
    "tools up to 200 USDC per transaction, max 500 USDC per day, "
    "from established vendors. New vendors require verification."
)


def addr_str(raw):
    if isinstance(raw, str):
        return raw
    if hasattr(raw, "as_bytes"):
        raw = raw.as_bytes
    return to_checksum_address(bytes(raw))


def iso_now():
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()) + ".000Z"


def iso_in(seconds):
    return time.strftime(
        "%Y-%m-%dT%H:%M:%S",
        time.gmtime(time.time() + seconds)) + ".000Z"


def set_time(vm, iso):
    vm.warp(iso)
    gl_mod = sys.modules.get("genlayer.gl")
    if gl_mod is not None:
        try:
            if getattr(gl_mod, "message_raw", None):
                gl_mod.message_raw["datetime"] = iso
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Contract bootstrap (used by nearly every test)
# ---------------------------------------------------------------------------
def deploy(vm, commit=TEST_REGISTRY_COMMIT, keccak=TEST_REGISTRY_KECCAK):
    from gltest.direct.loader import deploy_contract
    vm.sender = OWNER
    return deploy_contract(CONTRACT, vm, commit, keccak)


def mock_registry(vm, body=REGISTRY_OK, pattern="raw.githubusercontent.com"):
    vm.mock_web(pattern, {"status": 200, "body": body})


def _policy_args(policy_id, name, description, natural_language,
                 max_single, daily, action_types, restricted,
                 cooldown, escalation_above, verify_new, reject_flagged):
    return {
        "policy_id": policy_id,
        "name": name,
        "description": description,
        "natural_language": natural_language,
        "max_single_action_millis": max_single,
        "daily_budget_millis": daily,
        "allowed_action_types": action_types,
        "restricted_targets": restricted,
        "cooldown_seconds": cooldown,
        "require_consensus": True,
        "require_escalation_above_millis": escalation_above,
        "require_verification_new_recipients": verify_new,
        "reject_flagged_targets": reject_flagged,
    }


def register_treasury_policy(c, **kw):
    args = _policy_args(
        "treasury-policy", "Conservative Treasury Agent",
        "Small autonomous treasury", TREASURY_NL,
        50_000, 150_000, "TRANSFER", "", 0, 50_000, True, True)
    args.update(kw)
    return c.register_policy(**args)


def register_trading_policy(c, **kw):
    args = _policy_args(
        "trading-policy", "Constrained Trading Agent",
        "Rebalancing only", TRADING_NL,
        10_000_000, 30_000_000, "REBALANCE", "", 0, 10_000_000,
        False, True)
    args.update(kw)
    return c.register_policy(**args)


def register_dao_policy(c, **kw):
    args = _policy_args(
        "dao-policy", "DAO Governance Agent",
        "Proposals only", DAO_NL,
        1_000_000, 3_000_000, "GOVERNANCE_SUBMIT", "", 0, 1_000_000,
        False, True)
    args.update(kw)
    return c.register_policy(**args)


def register_proc_policy(c, **kw):
    args = _policy_args(
        "procurement-policy", "Procurement Agent",
        "Software purchasing", PROC_NL,
        200_000, 500_000, "PROCUREMENT", "", 0, 200_000, True, True)
    args.update(kw)
    return c.register_policy(**args)


def register_agent(c, agent_id="treasury-agent-01",
                   policy_id="treasury-policy", budget_millis=150_000,
                   **kw):
    args = {
        "agent_id": agent_id,
        "name": kw.pop("name", "Treasury Agent"),
        "description": kw.pop("description",
                              "Pays infrastructure providers"),
        "policy_id": policy_id,
        "risk_level": kw.pop("risk_level", "LOW"),
        "display_budget_millis": budget_millis,
    }
    args.update(kw)
    return c.register_agent(**args)


def submit(c, agent_id="treasury-agent-01", action_type="TRANSFER",
           target=KNOWN, amount_millis=25_000, asset="USDC",
           parameters="{}", reason="Pay infrastructure provider",
           context_hash="", expires_in_seconds=3600):
    return c.submit_action(
        agent_id=agent_id, action_type=action_type, target=target,
        amount_millis=amount_millis, asset=asset, parameters=parameters,
        reason=reason, context_hash=context_hash,
        expires_in_seconds=expires_in_seconds)


# ---------------------------------------------------------------------------
# LLM label fixtures — the model labels criteria; the CONTRACT derives
# ---------------------------------------------------------------------------
def llm_ok(risk=10):
    """LLM labels — clean PASS across all criteria."""
    return json.dumps({
        "criteria": {
            "policy_intent_compliant": {"status": "PASS",
                                        "justification": "infra payment "
                                        "matches policy"},
            "evidence_safe": {"status": "PASS",
                              "justification": "known active provider"},
            "action_proportionate": {"status": "PASS",
                                     "justification": "amount fits role"},
        },
        "llm_risk_score": risk,
    })


def llm_semantic_fail(risk=80):
    """LLM labels — semantic FAIL (reason violates policy intent)."""
    return json.dumps({
        "criteria": {
            "policy_intent_compliant": {"status": "FAIL",
                                        "justification": "gambling is "
                                        "prohibited by policy"},
            "evidence_safe": {"status": "PASS",
                              "justification": "target known"},
            "action_proportionate": {"status": "PASS",
                                     "justification": "amount small"},
        },
        "llm_risk_score": risk,
    })


def llm_uncertain(risk=40):
    """LLM labels — UNCERTAIN on evidence (uncertainty escalates)."""
    return json.dumps({
        "criteria": {
            "policy_intent_compliant": {"status": "PASS",
                                        "justification": "infra payment"},
            "evidence_safe": {"status": "UNCERTAIN",
                              "justification": "no prior interaction"},
            "action_proportionate": {"status": "PASS",
                                     "justification": "amount fits role"},
        },
        "llm_risk_score": risk,
    })


def llm_fooled_approve(risk=5):
    """LLM labels everything PASS — used with a FLAGGED target to prove
    the deterministic gates clamp a fooled/hallucinating model."""
    return json.dumps({
        "criteria": {
            "policy_intent_compliant": {"status": "PASS",
                                        "justification": "reason claims "
                                        "infra payment"},
            "evidence_safe": {"status": "PASS",
                              "justification": "assumed safe"},
            "action_proportionate": {"status": "PASS",
                                     "justification": "amount fits"},
        },
        "llm_risk_score": risk,
    })


def llm_malformed():
    return json.dumps({"not_the_expected_shape": True})
