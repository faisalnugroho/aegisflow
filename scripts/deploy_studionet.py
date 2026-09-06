#!/usr/bin/env python3
"""AegisFlow — deploy to Studionet + bootstrap + live consensus smoke.

Smoke plan (hackathon scenarios S1-S5, each with full consensus):
  S1 safe payment:      treasury TRANSFER 25 USDC to known infra
                        provider -> APPROVE, then EXECUTE
  S2 budget violation:  TRANSFER 100 USDC (limit 50) -> REJECT
  S3 new recipient:     TRANSFER 20 USDC to unverified -> ESCALATE,
                        then human APPROVE -> EXECUTE
  S4 flagged evidence:  TRANSFER 25 USDC to phishing-drainer -> REJECT
                        (deterministic external_risk_flag gate)
  S5 policy update:     policy v1->v2; the S5 v1 approval must BLOCK at
                        execution (policy_superseded)
Determinism: S1-class action repeated 3x (fresh IDs) must APPROVE all.

Registry pin: commit + keccak256 of data/evidence_registry.json —
verified live by the leader AND every validator on every consensus run.

Output: docs/deployment_log.json (address, tx hashes, verdicts).
Explorer: https://explorer-studio.genlayer.com/address/<addr>
"""
import json
import sys
import time
from pathlib import Path

from genlayer_py import create_client, create_account
from genlayer_py.chains import studionet
from genlayer_py.types import TransactionStatus

CODE = Path("contracts/AegisFlow.py").read_text()

PIN_COMMIT = "b5cfe180635cd334ae94a430bacbbce7c5ae257f"
PIN_KECCAK = ("bb5955cbd855d8faf3aa239eef11ea857c8b08ddcab97fdcc"
              "bbcd74a33691804")

KNOWN = "0x1010101010101010101010101010101010101010"
NEW = "0x2020202020202020202020202020202020202020"
FLAGGED = "0x3030303030303030303030303030303030303030"

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

log = {"network": "studionet", "registry_pin": {
    "commit": PIN_COMMIT, "keccak256": PIN_KECCAK},
    "transactions": [], "bootstrap": {}, "scenarios": {}}

KEYFILE = Path("scripts/smoke_deployer.json")


def load_account():
    if KEYFILE.exists():
        data = json.loads(KEYFILE.read_text())
        return create_account(account_private_key=data["private_key"])
    from eth_account import Account
    acct = Account.create()
    KEYFILE.write_text(json.dumps(
        {"address": acct.address, "private_key": acct.key.hex()},
        indent=2))
    KEYFILE.chmod(0o600)
    return create_account(account_private_key=acct.key.hex())


def wait_final(client, tx_hash, label, allow_error=False):
    receipt = None
    for _ in range(6):
        try:
            receipt = client.wait_for_transaction_receipt(
                transaction_hash=tx_hash,
                status=TransactionStatus.FINALIZED,
                retries=100, interval=3000)
            break
        except Exception as e:
            print(f"  [{label}] rpc hiccup: {str(e)[:80]}", flush=True)
            time.sleep(15)
    else:
        raise RuntimeError(f"{label} rpc failed")
    if isinstance(receipt, dict):
        leader = (receipt.get("consensus_data") or {}).get(
            "leader_receipt", [{}])
        lead = leader[0] if leader else {}
        exec_result = lead.get("execution_result")
        if exec_result is None:
            exec_result = receipt.get("tx_execution_result_name")
        stderr = str((lead.get("genvm_result") or {}).get("stderr") or "")
        ok = exec_result in (None, "SUCCESS", "FINISHED_WITH_RETURN")
        rec = {"tx_hash": tx_hash, "execution_result":
               exec_result or "SUCCESS"}
        if allow_error:
            rec["stderr_tail"] = stderr[-600:]
            return rec
        if not ok:
            print("EXECUTION FAILED:", label)
            print("stderr tail:", stderr[-1500:])
            raise RuntimeError(label + " execution failed")
        return rec
    return {"tx_hash": tx_hash, "execution_result": "SUCCESS"}


def write(client, addr, fn, args, label, allow_error=False):
    t0 = time.time()
    tx_hash = client.write_contract(address=addr, function_name=fn,
                                    args=args, account=client.local_account)
    rec = wait_final(client, tx_hash, label, allow_error=allow_error)
    rec["label"] = label
    rec["seconds"] = round(time.time() - t0, 1)
    log["transactions"].append(rec)
    print(f"  {label}: {rec['execution_result']} in {rec['seconds']}s",
          flush=True)
    return rec


def read_json(client, addr, fn, args=[]):
    raw = client.read_contract(address=addr, function_name=fn, args=args)
    return json.loads(raw) if isinstance(raw, str) else raw


def main():
    account = load_account()
    client = create_client(chain=studionet, account=account)
    client.fund_account(account.address, 10 * 10**18)
    print(f"deployer: {account.address}", flush=True)

    print("deploying AegisFlow (full consensus)...", flush=True)
    t0 = time.time()
    tx_hash = client.deploy_contract(
        code=CODE, account=client.local_account,
        args=[PIN_COMMIT, PIN_KECCAK], leader_only=False)
    receipt = client.wait_for_transaction_receipt(
        transaction_hash=tx_hash, status=TransactionStatus.FINALIZED,
        retries=100, interval=3000)
    addr = (receipt.get("data", {}).get("contract_address")
            if isinstance(receipt, dict) else None) \
        or (receipt.get("to_address") if isinstance(receipt, dict) else None)
    if not addr:
        print(json.dumps(receipt, default=str)[:2000])
        raise RuntimeError("no contract address in receipt")
    log["contract_address"] = addr
    log["deploy_tx"] = tx_hash
    log["deploy_seconds"] = round(time.time() - t0, 1)
    print(f"deployed at {addr} ({log['deploy_seconds']}s)", flush=True)

    # ---- bootstrap: policy + agent --------------------------------------
    r = write(client, addr, "register_policy", [
        "treasury-policy", "Conservative Treasury Agent",
        "Small autonomous treasury", TREASURY_NL,
        50_000, 150_000, "TRANSFER", "", 0, True, 50_000, True, True],
        "register_policy_v1")
    r = write(client, addr, "register_agent", [
        "treasury-agent-01", "Treasury Agent",
        "Pays infrastructure providers within policy limits",
        "treasury-policy", "LOW", 150_000], "register_agent")
    pol = read_json(client, addr, "get_policy", ["treasury-policy"])
    log["bootstrap"]["policy_version"] = pol.get("version_label")
    log["bootstrap"]["policy_hash"] = pol.get("policy_hash")
    print(f"bootstrap: {pol.get('version_label')}", flush=True)

    def submit_and_decide(label, target, amount, extra_note=""):
        aid = read_json(client, addr, "get_protocol_stats"
                        )  # noop read to pace RPC
        r = write(client, addr, "submit_action", [
            "treasury-agent-01", "TRANSFER", target, amount, "USDC",
            json.dumps({"note": extra_note}),
            "Pay infrastructure provider", "", 3600],
            f"submit:{label}")
        # find the newest action id
        stats = read_json(client, addr, "get_protocol_stats")
        aid = f"ACT-{stats['total_actions']:06d}"
        r = write(client, addr, "run_consensus", [aid],
                  f"consensus:{label}")
        dec = read_json(client, addr, "get_decision", [aid])
        log["scenarios"][label] = {
            "action_id": aid, "decision": dec.get("decision"),
            "reason_code": dec.get("reason_code"),
            "risk_score": dec.get("risk_score"),
            "evidence_hash": dec.get("evidence_hash"),
            "tx_hash": r["tx_hash"],
        }
        print(f"  => {label}: {dec.get('decision')} "
              f"({dec.get('reason_code')}) risk={dec.get('risk_score')}",
              flush=True)
        return aid, dec

    # ---- S1: safe payment -> APPROVE -> EXECUTE (3x determinism) --------
    for i in range(3):
        aid, dec = submit_and_decide(f"s1_safe_payment_run{i+1}", KNOWN,
                                     25_000)
        assert dec["decision"] == "APPROVE", dec
        if i == 0:
            write(client, addr, "execute_action", [aid], "s1_execute")
            a = read_json(client, addr, "get_action", [aid])
            assert a["status"] == "EXECUTED", a
            log["scenarios"]["s1_execute"] = {"action_id": aid,
                                              "status": "EXECUTED"}

    # ---- S2: 100 USDC > 50 limit -> deterministic REJECT ----------------
    aid, dec = submit_and_decide("s2_budget_violation", KNOWN, 100_000)
    assert dec["decision"] == "REJECT", dec
    assert dec["reason_code"] == "amount_limit_exceeded", dec

    # ---- S3: new recipient -> ESCALATE -> human approve -> EXECUTE ------
    aid, dec = submit_and_decide("s3_new_recipient", NEW, 20_000)
    assert dec["decision"] == "ESCALATE", dec
    assert dec["reason_code"] == "new_recipient_requires_verification", dec
    write(client, addr, "resolve_escalation",
          [aid, True, "vendor verified manually via their public "
           "status page and domain records"], "s3_human_approve")
    write(client, addr, "execute_action", [aid], "s3_execute")
    a = read_json(client, addr, "get_action", [aid])
    assert a["status"] == "EXECUTED", a
    log["scenarios"]["s3_human_resolution"] = {"action_id": aid,
                                               "status": "EXECUTED"}

    # ---- S4: flagged target -> REJECT (deterministic evidence gate) -----
    aid, dec = submit_and_decide("s4_flagged_evidence", FLAGGED, 25_000)
    assert dec["decision"] == "REJECT", dec
    assert dec["reason_code"] == "external_risk_flagged", dec

    # ---- S5: policy v1->v2; v1 approval BLOCKS at execution ------------
    aid, dec = submit_and_decide("s5_v1_approval", KNOWN, 25_000)
    assert dec["decision"] == "APPROVE", dec
    write(client, addr, "update_policy", [
        "treasury-policy", "Conservative Treasury Agent",
        "Tightened after incident review",
        "Tightened treasury policy: infrastructure payments only, max 10 USDC per transaction.",
        10_000, 50_000, "TRANSFER", "", 0, True, 10_000, True, True],
        "update_policy_v2")
    r = write(client, addr, "execute_action", [aid], "s5_execute_under_v2")
    a = read_json(client, addr, "get_action", [aid])
    log["scenarios"]["s5_policy_update"] = {
        "action_id": aid, "final_status": a["status"],
        "execution_status": a["execution_status"]}
    assert a["status"] == "BLOCKED" \
        and a["execution_status"] == "stale_policy", a
    print(f"  => s5: v1 approval correctly BLOCKED under v2", flush=True)

    # ---- restore demo-friendly limits as v3 (so live dApp scenarios
    # behave as designed: S1 25 USDC approves again) ----------------------
    write(client, addr, "update_policy", [
        "treasury-policy", "Conservative Treasury Agent",
        "Demo policy restored (v3) — original limits",
        TREASURY_NL,
        50_000, 150_000, "TRANSFER", "", 0, True, 50_000, True, True],
        "update_policy_v3_restore")

    # ---- final state snapshot -------------------------------------------
    stats = read_json(client, addr, "get_protocol_stats")
    log["final_stats"] = stats
    print(json.dumps(stats, indent=2), flush=True)

    Path("docs").mkdir(exist_ok=True)
    Path("docs/deployment_log.json").write_text(json.dumps(log, indent=2))
    print("\nSMOKE COMPLETE — all scenarios passed. Log: "
          "docs/deployment_log.json", flush=True)
    print(f"Explorer: https://explorer-studio.genlayer.com/address/{addr}")


if __name__ == "__main__":
    main()
