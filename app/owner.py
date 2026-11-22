"""The owner's side of the demo wallet, done from the server.

On oryne.org an owner signs with a passkey or Freighter. For the public demo the
owner is a Stellar account whose key this server holds (as Freighter would), so
a visitor can watch the whole flow without signing up: connect an agent with a
mandate, approve a payment the mandate refused, disconnect the agent.

Every one of those is a transaction the owner signs; the wallet contract on
Stellar enforces the mandate, not this server.
"""

from __future__ import annotations

import base64
import hashlib
import threading
from typing import Any

import httpx
from stellar_sdk import Keypair, TransactionEnvelope


class OryneOwner:
    def __init__(self, api_url: str, secret: str, wallet: str) -> None:
        self.api_url = api_url.rstrip("/")
        self.keypair = Keypair.from_secret(secret)
        self.wallet = wallet
        self._token: str | None = None
        self._lock = threading.Lock()
        # Owner transactions share one account sequence: build and submit them one at a time.
        self._ops = threading.Lock()
        self._http = httpx.Client(timeout=90)

    # ------------------------------------------------------------ plumbing

    def _call(self, path: str, *, method: str = "GET", body: Any = None, auth: bool = True, retry: bool = True) -> Any:
        headers = {"authorization": f"Bearer {self.token()}"} if auth else {}
        response = self._http.request(method, f"{self.api_url}{path}", json=body, headers=headers)
        if response.status_code == 401 and auth and retry:
            self._token = None
            return self._call(path, method=method, body=body, auth=auth, retry=False)
        data = response.json() if response.content else {}
        if response.status_code >= 400:
            raise RuntimeError(f"{path}: {data.get('error') or response.status_code}")
        return data

    def token(self) -> str:
        with self._lock:
            if self._token is None:
                self._token = self._sign_in()
            return self._token

    def _sign_in(self) -> str:
        """Sign in as Freighter does: sign the API's challenge as a SEP-53 message."""
        address = self.keypair.public_key
        challenge = self._call("/v1/auth/challenge", method="POST", body={"address": address}, auth=False)
        digest = hashlib.sha256(b"Stellar Signed Message:\n" + challenge["message"].encode()).digest()
        signature = base64.b64encode(self.keypair.sign(digest)).decode()
        verified = self._call(
            "/v1/auth/verify", method="POST", body={"nonce": challenge["nonce"], "signature": signature}, auth=False
        )
        return verified["token"]

    def _intent(self, path: str, *, method: str = "POST", body: Any = None) -> dict:
        """Asks the API for an intent, signs it and submits it, holding the owner's lock throughout."""
        with self._ops:
            intent = self._call(path, method=method, body=body)
            return self._sign_and_submit(intent)

    def _sign_and_submit(self, intent: dict) -> dict:
        """The API builds the transaction (an intent); the owner signs it and the API submits it."""
        if intent.get("mode") != "envelope":
            raise RuntimeError(f"Expected an envelope intent, got {intent.get('mode')}")
        envelope = TransactionEnvelope.from_xdr(intent["xdr"], intent["networkPassphrase"])
        envelope.sign(self.keypair)
        return self._call(f"/v1/intents/{intent['id']}/submit", method="POST", body={"signedXdr": envelope.to_xdr()})

    # ------------------------------------------------------------ what the demo needs

    def wallet_status(self) -> dict:
        return self._call(f"/v1/wallets/{self.wallet}")

    def top_up(self, amount: str = "100") -> None:
        """Testnet only: free test USDC from the Oryne faucet."""
        self._call("/v1/faucet", method="POST", body={"to": self.wallet, "amount": amount}, auth=False)

    def connect_agent(
        self,
        public_key: str,
        *,
        name: str,
        per_tx: str,
        budget: str,
        payees: list[str],
        expires_at: int,
    ) -> str:
        """add_agent(key, policy) on the wallet contract. Returns the transaction hash."""
        return self._intent(
            f"/v1/wallets/{self.wallet}/agents",
            body={
                "publicKey": public_key,
                "name": name,
                "kind": "Job hunting",
                "perTx": per_tx,
                "budget": budget,
                "windowDays": 1,
                "payees": payees,
                "expiresAt": expires_at,
            },
        )["hash"]

    def disconnect_agent(self, public_key: str) -> None:
        self._intent(f"/v1/wallets/{self.wallet}/agents/{public_key}", method="DELETE")

    def approve(self, approval_id: str) -> str:
        """approve_once on the wallet contract: lets this one payment through."""
        return self._intent(f"/v1/approvals/{approval_id}/approve")["hash"]

    def decline(self, approval_id: str) -> None:
        self._call(f"/v1/approvals/{approval_id}/decline", method="POST")
