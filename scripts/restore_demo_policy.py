#!/usr/bin/env python3
"""One-off: restore the live demo policy to friendly limits as a new
version (v3) after the S5 smoke tightened it to v2. Keeps the demo
scenarios behaving as designed (25 USDC approves under a 50 USDC limit)."""
import json
import time
from pathlib import Path

from genlayer_py import create_client, create_account
from genlayer_py.chains import studionet
from genlayer_py.types import TransactionStatus

ADDR = "0xFAB23E7B871868Caf1D39454652FD20d66ebAe1E"
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


def wait_final(client, tx_hash, label):
    receipt = client.wait_for_transaction_receipt(
        transaction_hash=tx_hash, status=TransactionStatus.FINALIZED,
        retries=100, interval=3000)
    if isinstance(receipt, dict):
        leader = (receipt.get("consensus_data") or {}).get(
            "leader_receipt", [{}])
        lead = leader[0] if leader else {}
        if lead.get("execution_result") not in (None, "SUCCESS",
                                                "FINISHED_WITH_RETURN"):
            raise RuntimeError(label + " failed: "
                              + str(lead.get("genvm_result", {})
                                    .get("stderr", ""))[-800:])
    print(label, "OK")


def main():
    data = json.loads(Path("scripts/smoke_deployer.json").read_text())
    account = create_account(account_private_key=data["private_key"])
    client = create_client(chain=studionet, account=account)
    tx = client.write_contract(
        address=ADDR, function_name="update_policy", account=account,
        args=["treasury-policy", "Conservative Treasury Agent",
              "Demo policy restored — original limits (post-S5 demo reset)",
              TREASURY_NL,
              50_000, 150_000, "TRANSFER", "", 0, True, 50_000, True, True])
    wait_final(client, tx, "update_policy_v3_restore")
    pol = client.read_contract(address=ADDR,
                               function_name="get_policy",
                               args=["treasury-policy"])
    p = json.loads(pol) if isinstance(pol, str) else pol
    print("policy now:", p["version_label"],
          "| max_single:", p["max_single_action_millis"],
          "| daily:", p["daily_budget_millis"])


if __name__ == "__main__":
    main()
