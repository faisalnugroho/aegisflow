"""Re-crank S4 consensus (MAJORITY_DISAGREE left it PENDING) then finish S5."""
import json
import time
from pathlib import Path

from genlayer_py import create_client, create_account
from genlayer_py.chains import studio_devnet

LOG = Path('docs/deployment_log_studio_next_61997.json')
log = json.load(open(LOG))
addr = log['contract_address']
acct = create_account(account_private_key=json.load(
    open('scripts/studionext_deployer.json'))["private_key"])
client = create_client(chain=studio_devnet, account=acct)


def fees():
    return client.estimate_transaction_fees({
        "leaderTimeunitsAllocation": 200, "validatorTimeunitsAllocation": 400,
        "totalMessageFees": 0, "rotations": [1], "appealRounds": 0})


def exec_of(receipt):
    leader = (receipt.get("consensus_data") or {}).get(
        "leader_receipt", [{}])
    lead = leader[0] if leader else {}
    er = lead.get("execution_result") or receipt.get(
        "tx_execution_result_name")
    stderr = str((lead.get("genvm_result") or {}).get("stderr") or "")
    return er, receipt.get("result_name"), stderr


def write(fn, args, label, rounds=3):
    for attempt in range(1, rounds + 1):
        t0 = time.time()
        tx = client.write_contract(address=addr, function_name=fn, args=args,
                                   account=client.local_account, fees=fees())
        rec = client.wait_for_transaction_receipt(
            transaction_hash=tx, wait_until="finalized",
            interval=5000, retries=100)
        er, vote, stderr = exec_of(rec)
        print(f"  {label} try{attempt}: exec={er} vote={vote} "
              f"{round(time.time()-t0, 1)}s", flush=True)
        if er in (None, "SUCCESS", "FINISHED_WITH_RETURN") \
                and vote == "MAJORITY_AGREE":
            return tx
        time.sleep(5)
    raise RuntimeError(label + " failed after retries")


def read_json(fn, args):
    raw = client.read_contract(address=addr, function_name=fn, args=args,
                               account=client.local_account)
    return json.loads(raw)


# ---- S4 re-crank on the SAME action (still PENDING) -----------------------
for attempt in range(4):
    tx = write("run_consensus", ["ACT-000006"],
               "consensus:s4_flagged_evidence")
    dec = read_json("get_decision", ["ACT-000006"])
    if dec.get("decision") == "REJECT" \
            and dec.get("reason_code") == "external_risk_flagged":
        log['scenarios']['s4_flagged_evidence'] = {
            "action_id": "ACT-000006", "decision": dec.get("decision"),
            "reason_code": dec.get("reason_code"),
            "risk_score": dec.get("risk_score"),
            "evidence_hash": dec.get("evidence_hash"),
            "tx_hash": tx,
            "consensus_rounds": attempt + 1,
            "note": "first run MAJORITY_DISAGREE (validators wobbled); "
                    "re-cranked the same PENDING action per fail-safe design",
        }
        print("  => s4:", dec.get("decision"), dec.get("reason_code"),
              flush=True)
        break
    time.sleep(5)
else:
    raise RuntimeError("s4 did not converge")

LOG.write_text(json.dumps(log, indent=2))

# ---- S5: v1 approval -> policy v2 -> BLOCKED -> restore v3 ----------------
r = write("submit_action", ["treasury-agent-01", "TRANSFER",
    "0x1010101010101010101010101010101010101010", 25_000, "USDC",
    json.dumps({"note": ""}), "Pay infrastructure provider", "", 3600],
    "submit:s5_v1_approval")
stats = read_json("get_protocol_stats", [])
aid = f"ACT-{stats['total_actions']:06d}"
write("run_consensus", [aid], "consensus:s5_v1_approval")
dec = read_json("get_decision", [aid])
assert dec["decision"] == "APPROVE", dec
log['scenarios']['s5_v1_approval'] = {"action_id": aid,
    "decision": dec["decision"], "reason_code": dec["reason_code"],
    "risk_score": dec.get("risk_score"),
    "evidence_hash": dec.get("evidence_hash")}
LOG.write_text(json.dumps(log, indent=2))
print("  => s5 v1 approval:", dec["decision"], flush=True)

write("update_policy", ["treasury-policy", "Conservative Treasury Agent",
    "Tightened after incident review",
    "Tightened treasury policy: infrastructure payments only, "
    "max 10 USDC per transaction.",
    10_000, 50_000, "TRANSFER", "", 0, True, 10_000, True, True],
    "update_policy_v2")
write("execute_action", [aid], "s5_execute_under_v2")
a = read_json("get_action", [aid])
log['scenarios']['s5_policy_update'] = {"action_id": aid,
    "final_status": a["status"], "execution_status": a["execution_status"]}
assert a["status"] == "BLOCKED" and a["execution_status"] == "stale_policy", a
print("  => s5: v1 approval BLOCKED under v2", flush=True)

write("update_policy", ["treasury-policy", "Conservative Treasury Agent",
    "Demo policy restored (v3) — original limits",
    "You are managing a small autonomous treasury. You may execute "
    "infrastructure payments up to 50 USDC per transaction. You may "
    "not spend more than 150 USDC per day. Transactions to new "
    "recipients require additional verification. Any transaction "
    "above 50 USDC must be escalated. Never approve an action if "
    "external evidence indicates the recipient is malicious. Only "
    "infrastructure payments (cloud, hosting, domains, APIs) are "
    "permitted; no personal loans, gambling, or undisclosed purposes.",
    50_000, 150_000, "TRANSFER", "", 0, True, 50_000, True, True],
    "update_policy_v3_restore")

stats = read_json("get_protocol_stats", [])
log['final_stats'] = stats
log['registry_info_onchain'] = read_json("get_registry_info", [])
LOG.write_text(json.dumps(log, indent=2))
print("SMOKE COMPLETE — S4 re-crank + S5 done.",
      json.dumps({k: stats[k] for k in
                  ('total_actions', 'approved', 'rejected',
                   'escalated', 'executed')}), flush=True)
