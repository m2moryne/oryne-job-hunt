"""One-time setup on Stellar testnet. Safe to run again: it reuses what .env already has.

  1. CareerKit's Stellar address, the one agents pay: funded by friendbot, with a
     trustline to Oryne's test USDC so it can receive it.
  2. The demo wallet: an owner account (as Freighter would be) signs in to Oryne,
     deploys its agent-wallet contract and fills it with free test USDC.

Writes .env. Then: python -m uvicorn app.main:app --port 8800
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import httpx
from stellar_sdk import Asset, Keypair, Network, Server, TransactionBuilder

ROOT = Path(__file__).resolve().parent
ENV = ROOT / ".env"
sys.path.insert(0, str(ROOT))

from app.config import load_dotenv  # noqa: E402

load_dotenv(ENV)

from app.owner import OryneOwner  # noqa: E402

API = os.environ.get("ORYNE_API_URL", "https://testnet-apiv1.oryne.org").rstrip("/")
HORIZON = "https://horizon-testnet.stellar.org"


def friendbot(address: str) -> None:
    httpx.get("https://friendbot.stellar.org/", params={"addr": address}, timeout=60)


def trust(keypair: Keypair, asset: Asset) -> None:
    horizon = Server(HORIZON)
    account = horizon.load_account(keypair.public_key)
    balances = horizon.accounts().account_id(keypair.public_key).call()["balances"]
    if any(b.get("asset_code") == asset.code and b.get("asset_issuer") == asset.issuer for b in balances):
        return
    tx = (
        TransactionBuilder(account, Network.TESTNET_NETWORK_PASSPHRASE, base_fee=100)
        .append_change_trust_op(asset)
        .set_timeout(60)
        .build()
    )
    tx.sign(keypair)
    horizon.submit_transaction(tx)


def write_env(values: dict[str, str]) -> None:
    lines = ENV.read_text(encoding="utf-8").splitlines() if ENV.exists() else []
    seen = set()
    for i, line in enumerate(lines):
        key = line.split("=", 1)[0].strip()
        if key in values:
            lines[i] = f"{key}={values[key]}"
            seen.add(key)
    lines += [f"{key}={value}" for key, value in values.items() if key not in seen]
    ENV.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    info = httpx.get(f"{API}/v1/info", timeout=30).json()
    if info.get("network") != "stellar-testnet" or not info.get("tokenAsset"):
        sys.exit("This setup is for Stellar testnet only.")
    usdc = Asset(info["tokenAsset"]["code"], info["tokenAsset"]["issuer"])

    # 1. CareerKit's address.
    merchant_secret = os.environ.get("CAREERKIT_SECRET")
    merchant = Keypair.from_secret(merchant_secret) if merchant_secret else Keypair.random()
    friendbot(merchant.public_key)
    trust(merchant, usdc)
    print(f"✓ CareerKit is paid at {merchant.public_key} (trustline to test {usdc.code})")

    # 2. The demo wallet.
    owner_secret = os.environ.get("DEMO_OWNER_SECRET")
    owner_key = Keypair.from_secret(owner_secret) if owner_secret else Keypair.random()
    friendbot(owner_key.public_key)
    wallet = os.environ.get("DEMO_WALLET")
    owner = OryneOwner(API, owner_key.secret, wallet or "")
    if not wallet:
        created = owner._call("/v1/wallets", method="POST", body={"name": "Job agent demo wallet"})
        wallet = created["id"]
        owner.wallet = wallet
        print(f"✓ Deployed the demo wallet {wallet} (tx {created['hash'][:12]}…)")
    else:
        print(f"✓ Demo wallet {wallet} already exists")
    owner.top_up("500")
    print(f"✓ Demo wallet holds {owner.wallet_status().get('balance')} test USDC")

    write_env(
        {
            "ORYNE_API_URL": API,
            "CAREERKIT_ADDRESS": merchant.public_key,
            "CAREERKIT_SECRET": merchant.secret,
            "DEMO_OWNER_SECRET": owner_key.secret,
            "DEMO_WALLET": wallet,
        }
    )
    print(f"✓ Wrote {ENV}")


if __name__ == "__main__":
    main()
