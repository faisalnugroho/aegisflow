#!/usr/bin/env python3
"""AegisFlow — deploy to Studio Next (studio-dev, chain 61997) + smoke.

Remediation deployment for the GenLayer Portal "Action Needed" request:
the previous production deployment lived on Studionet (chain 61999). This
script deploys the SAME byte-identical contract (contracts/AegisFlow.py,
unmodified since commit b5cfe18) to the Studio development preview
("Studio Next") at https://studio-dev.genlayer.com/api (chain 61997),
using a FRESH deployer key so the 61997 address is unambiguous, and
re-runs the full S1-S5 consensus-to-execution smoke there.

SDK: genlayer-py 0.19.0rc2 (the v0.6/Studio-v0.123-RC compatible release
line) with its official `studio_devnet` chain. Studio-dev charges fees
(fee policy enabled): every tx estimates a FeesDistribution from the live
policy and passes {distribution, feeValue} explicitly.

Registry pin: same commit + keccak256 of data/evidence_registry.json as
the original deployment (re-verified live before this deploy; the
contract fetches and keccak-verifies these exact bytes on every
consensus run).

Output: docs/deployment_log_studio_next_61997.json
Explorer: https://explorer-studio-dev.genlayer.com/address/<addr>
"""
import base64
import hashlib
import json
import subprocess
import time
from pathlib import Path

from genlayer_py import create_client, create_account
from genlayer_py.chains import studio_devnet

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

LOG_PATH = Path("docs/deployment_log_studio_next_61997.json")
KEYFILE = Path("scripts/studionext_deployer.json")

log = {"network": "studio-next (studio-dev.genlayer.com)", "chain_id": 61997,
       "sdk": "genlayer-py 0.19.0rc2 (v0.6 RC release family)",
       "registry_pin": {"commit": PIN_COMMIT, "keccak256": PIN_KECCAK},
       "transactions": [], "bootstrap": {}, "scenarios": {}}


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


def fund(client, address, amount):
    try:
        client.fund_account(address, amount)
        return "fund_account"
    except Exception as e:
        print(f"  fund_account fallback ({str(e)[:80]})", flush=True)
        client.provider.make_request("sim_fundAccount", [address, amount])
        return "sim_fundAccount"


def fee_preset(client):
    """Live-price fee estimate: 200/400 leader/validator time units,
    default execution budget, 1 rotation, 0 appeal rounds, no child
    messages (this contract emits events only, never child txs)."""
    return client.estimate_transaction_fees({
        "leaderTimeunitsAllocation": 200,
        "validatorTimeunitsAllocation": 400,
        "totalMessageFees": 0,
        "rotations": [1],
        "appealRounds": 0,
    })


def exec_of(receipt):
    if not isinstance(receipt, dict):
        return None, None, ""
    leader = (receipt.get("consensus_data") or {}).get(
        "leader_receipt", [{}])
    lead = leader[0] if leader else {}
    exec_result = lead.get("execution_result")
    if exec_result is None:
        exec_result = receipt.get("tx_execution_result_name")
    stderr = str((lead.get("genvm_result") or {}).get("stderr") or "")
    return exec_result, receipt.get("result_name"), stderr


def wait_final(client, tx_hash, label, allow_error=False):
    receipt = None
    for _ in range(6):
        try:
            receipt = client.wait_for_transaction_receipt(
                transaction_hash=tx_hash, wait_until="finalized",
                interval=5000, retries=100)
            break
        except Exception as e:
            print(f"  [{label}] rpc hiccup: {str(e)[:80]}", flush=True)
            time.sleep(20)
    else:
        raise RuntimeError(f"{label} rpc failed")
    exec_result, vote, stderr = exec_of(receipt)
    ok = exec_result in (None, "SUCCESS", "FINISHED_WITH_RETURN")
    rec = {"tx_hash": tx_hash, "execution_result": exec_result or "SUCCESS",
           "consensus_vote": vote}
    if allow_error:
        rec["stderr_tail"] = stderr[-600:]
        return rec
    if not ok:
        print("EXECUTION FAILED:", label)
        print("stderr tail:", stderr[-1500:])
        raise RuntimeError(label + " execution failed")
    return rec


def write(client, addr, fn, args, label, allow_error=False):
    t0 = time.time()
    tx_hash = client.write_contract(
        address=addr, function_name=fn, args=args,
        account=client.local_account, fees=fee_preset(client))
    rec = wait_final(client, tx_hash, label, allow_error=allow_error)
    rec["label"] = label
    rec["seconds"] = round(time.time() - t0, 1)
    log["transactions"].append(rec)
    print(f"  {label}: {rec['execution_result']} vote={rec['consensus_vote']}"
          f" in {rec['seconds']}s", flush=True)
    return rec


def read_json(client, addr, fn, args=[]):
    raw = client.read_contract(address=addr, function_name=fn, args=args,
                               account=client.local_account)
    return json.loads(raw) if isinstance(raw, str) else raw


def save_log():
    LOG_PATH.write_text(json.dumps(log, indent=2))


def byte_identity(client, deploy_tx_hash):
    """Prove deployed code == repo contract: the type-1 deploy tx's
    data.contract_code is base64 of the entire deployed Python source."""
    raw = client.provider.make_request("eth_getTransactionByHash",
                                       [deploy_tx_hash])
    result = raw.get("result") or raw
    data = result.get("data") or {}
    code_b64 = (data.get("contract_code") if isinstance(data, dict) else None) \
        or result.get("contract_code") or ""
    deployed_src = base64.b64decode(code_b64)
    deployed_sha = hashlib.sha256(deployed_src).hexdigest()
    repo_sha = hashlib.sha256(CODE.encode("utf-8")).hexdigest()
    log["byte_identity"] = {
        "deployed_sha256": deployed_sha,
        "repo_sha256": repo_sha,
        "match": deployed_sha == repo_sha,
        "method": "base64-decode data.contract_code of deploy tx",
    }
    print(f"  byte-identity: deployed {deployed_sha[:16]}… vs repo "
          f"{repo_sha[:16]}… match={deployed_sha == repo_sha}", flush=True)
    if deployed_sha != repo_sha:
        raise RuntimeError("deployed code does not match repository")


def main():
    account = load_account()
    client = create_client(chain=studio_devnet, account=account)
    chain_id_hex = client.provider.make_request("eth_chainId", [])
    cid = int(str(chain_id_hex.get("result", "0x0")), 16)
    log["rpc_chain_id_observed"] = cid
    log["deployer"] = account.address
    log["head_commit"] = subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True,
        text=True).stdout.strip()
    assert cid == 61997, f"chain id {cid} != 61997"
    print(f"deployer: {account.address} on chain {cid}", flush=True)

    fund(client, account.address, 10 * 10**18)
    time.sleep(3)

    est = fee_preset(client)
    log["fee_preset_example"] = {
        "distribution": est["distribution"], "feeValue": str(est["feeValue"])}
    save_log()

    print("deploying AegisFlow to Studio Next 61997 (full consensus)...",
          flush=True)
    t0 = time.time()
    tx_hash = client.deploy_contract(
        code=CODE, account=client.local_account,
        args=[PIN_COMMIT, PIN_KECCAK], leader_only=False,
        fees=fee_preset(client))
    receipt = client.wait_for_transaction_receipt(
        transaction_hash=tx_hash, wait_until="finalized",
        interval=5000, retries=100)
    addr = (receipt.get("data", {}).get("contract_address")
            if isinstance(receipt, dict) else None) \
        or (receipt.get("to_address") if isinstance(receipt, dict) else None)
    if not addr:
        print(json.dumps(receipt, default=str)[:2500])
        raise RuntimeError("no contract address in receipt")
    log["contract_address"] = addr
    log["deploy_tx"] = tx_hash
    log["deploy_seconds"] = round(time.time() - t0, 1)
    log["deployed_at_utc"] = time.strftime(
        "%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    save_log()
    print(f"deployed at {addr} ({log['deploy_seconds']}s)", flush=True)

    byte_identity(client, tx_hash)
    save_log()

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
    save_log()
    print(f"bootstrap: {pol.get('version_label')}", flush=True)

    def submit_and_decide(label, target, amount, extra_note=""):
        r = write(client, addr, "submit_action", [
            "treasury-agent-01", "TRANSFER", target, amount, "USDC",
            json.dumps({"note": extra_note}),
            "Pay infrastructure provider", "", 3600],
            f"submit:{label}")
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
        save_log()
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
            save_log()

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
    save_log()

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
    save_log()
    assert a["status"] == "BLOCKED" \
        and a["execution_status"] == "stale_policy", a
    print("  => s5: v1 approval correctly BLOCKED under v2", flush=True)

    # ---- restore demo-friendly limits as v3 ------------------------------
    write(client, addr, "update_policy", [
        "treasury-policy", "Conservative Treasury Agent",
        "Demo policy restored (v3) — original limits",
        TREASURY_NL,
        50_000, 150_000, "TRANSFER", "", 0, True, 50_000, True, True],
        "update_policy_v3_restore")

    # ---- final state snapshot -------------------------------------------
    stats = read_json(client, addr, "get_protocol_stats")
    log["final_stats"] = stats
    reg = read_json(client, addr, "get_registry_info")
    log["registry_info_onchain"] = reg
    save_log()
    print(json.dumps(stats, indent=2), flush=True)

    print("\nSMOKE COMPLETE — all scenarios passed on chain 61997. Log: "
          f"{LOG_PATH}", flush=True)
    print(f"Explorer: https://explorer-studio-dev.genlayer.com/address/{addr}")


if __name__ == "__main__":
    main()
