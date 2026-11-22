"""Runs: one job hunt each, in its own thread, with a live stream of what the agent does.

Two kinds of wallet:
  demo  this server connects a fresh agent to the demo wallet for the visitor, with
        the limits they chose, and disconnects it afterwards. The visitor approves
        on this page; the server signs approve_once as the demo wallet's owner.
  own   the visitor pastes the secret of an agent they connected at oryne.org (Test
        mode). It's used for this run only and never stored. They approve in their
        Oryne dashboard, signing with their own passkey or Freighter.
"""

from __future__ import annotations

import logging
import secrets
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any

from oryne import OryneAgent
from stellar_sdk import Keypair

from .agent import HuntRequest, JobHunt
from .config import settings
from .owner import OryneOwner

log = logging.getLogger("runs")

RUN_TTL = 3600  # forget a run (and the CV in it) an hour after it starts


class RunError(Exception):
    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


@dataclass
class Run:
    id: str
    mode: str
    created: float = field(default_factory=time.time)
    events: list[dict] = field(default_factory=list)
    done: bool = False
    pending_approval: dict | None = None
    cancelled: threading.Event = field(default_factory=threading.Event)
    changed: threading.Condition = field(default_factory=threading.Condition)

    def emit(self, type: str, **data: Any) -> None:
        with self.changed:
            self.events.append({"i": len(self.events), "type": type, "at": time.time(), **data})
            self.changed.notify_all()

    def wait(self, after: int, timeout: float = 15) -> list[dict]:
        with self.changed:
            if len(self.events) <= after and not self.done:
                self.changed.wait(timeout)
            return self.events[after:]


def _amount(value: str, name: str, low: str, high: str) -> str:
    try:
        number = Decimal(str(value))
    except InvalidOperation as error:
        raise RunError(f"{name} must be a number") from error
    if not Decimal(low) <= number <= Decimal(high):
        raise RunError(f"{name} must be between {low} and {high} USDC")
    return str(number.normalize())


class Runs:
    def __init__(self) -> None:
        self.runs: dict[str, Run] = {}
        self.recent: dict[str, deque] = defaultdict(deque)
        self.lock = threading.Lock()
        self.owner = (
            OryneOwner(settings.oryne_api, settings.demo_owner_secret, settings.demo_wallet) if settings.demo_ready else None
        )

    def get(self, run_id: str) -> Run:
        run = self.runs.get(run_id)
        if not run:
            raise RunError("No such run (runs are forgotten after an hour)", 404)
        return run

    def _admit(self, ip: str) -> None:
        now = time.time()
        with self.lock:
            for run_id, run in list(self.runs.items()):
                if now - run.created > RUN_TTL:
                    run.cancelled.set()
                    del self.runs[run_id]
            active = sum(1 for run in self.runs.values() if not run.done)
            if active >= settings.max_concurrent_runs:
                raise RunError("The demo is busy right now. Try again in a minute.", 429)
            stamps = self.recent[ip]
            while stamps and now - stamps[0] > 3600:
                stamps.popleft()
            if len(stamps) >= settings.runs_per_ip_per_hour:
                raise RunError("That's the most runs per hour from one address. Try again later.", 429)
            stamps.append(now)

    def start(self, form: dict, ip: str) -> Run:
        cv = (form.get("cv") or "").strip()
        role = (form.get("role") or "").strip()
        if len(cv) < 80:
            raise RunError("Paste your CV (at least a few lines), or use the sample one.")
        if not 2 <= len(role) <= 100:
            raise RunError("Say what job you're looking for.")
        services = [s for s in form.get("services") or [] if isinstance(s, str)]
        if "search" not in services:
            raise RunError("The agent needs Job search to find anything.")
        request = HuntRequest(
            cv=cv[:15000],
            role=role,
            location=(form.get("location") or "").strip()[:60],
            boards=[b for b in form.get("boards") or [] if isinstance(b, str)] or ["remotive", "jobicy", "remoteok", "arbeitnow"],
            services=services,
            max_letters=max(0, min(int(form.get("maxLetters") or 3), 5)),
        )
        mode = form.get("mode", "demo")
        if mode == "own":
            secret = (form.get("agentSecret") or "").strip()
            try:
                Keypair.from_secret(secret)
            except Exception as error:  # noqa: BLE001
                raise RunError("That isn't an agent secret: it starts with S and is 56 characters.") from error
            self._admit(ip)
            run = Run(id=secrets.token_urlsafe(9), mode="own")
            self.runs[run.id] = run
            threading.Thread(target=self._run_own, args=(run, request, secret), daemon=True).start()
            return run
        if not self.owner:
            raise RunError("The demo wallet isn't set up on this server. Use your own agent instead.", 503)
        per_tx = _amount(form.get("perTx", "1"), "Per-payment limit", "0.05", "10")
        budget = _amount(form.get("budget", "3"), "Budget", "0.05", "20")
        if Decimal(per_tx) > Decimal(budget):
            raise RunError("The per-payment limit can't be more than the budget.")
        self._admit(ip)
        run = Run(id=secrets.token_urlsafe(9), mode="demo")
        self.runs[run.id] = run
        threading.Thread(target=self._run_demo, args=(run, request, per_tx, budget), daemon=True).start()
        return run

    # ------------------------------------------------------------ the two modes

    def _hunt(self, run: Run, request: HuntRequest, secret: str) -> None:
        def on_approval(payload: dict) -> None:
            run.pending_approval = payload

        hunt = JobHunt(request, secret, run.emit, on_approval=on_approval, cancelled=run.cancelled)
        try:
            hunt.run()
        finally:
            run.pending_approval = None

    def _run_demo(self, run: Run, request: HuntRequest, per_tx: str, budget: str) -> None:
        assert self.owner
        agent_key = Keypair.random()
        connected = False
        try:
            status = self.owner.wallet_status()
            if Decimal(status.get("balance") or "0") < 50:
                self.owner.top_up("500")
            run.emit(
                "setup",
                text=f"Connecting an agent to the demo wallet: {per_tx} USDC per payment, {budget} USDC budget, "
                "may pay CareerKit only, expires in 3 hours. The wallet owner signs add_agent on Stellar.",
            )
            tx = self.owner.connect_agent(
                agent_key.public_key,
                name=f"Job agent {run.id[:6]}",
                per_tx=per_tx,
                budget=budget,
                payees=[settings.careerkit_address],
                expires_at=int(time.time()) + 3 * 3600,
            )
            connected = True
            run.emit(
                "wallet",
                mode="demo",
                wallet=settings.demo_wallet,
                agent=agent_key.public_key,
                perTx=per_tx,
                budget=budget,
                tx=tx,
                explorer=f"https://stellar.expert/explorer/testnet/tx/{tx}",
            )
            self._hunt(run, request, agent_key.secret)
        except Exception as error:  # noqa: BLE001
            log.exception("demo run failed")
            run.emit("error", text=str(error) or type(error).__name__)
        finally:
            run.done = True
            run.emit("closed")
            if connected:
                try:
                    self.owner.disconnect_agent(agent_key.public_key)
                except Exception:  # noqa: BLE001 - it expires on its own in 3 hours anyway
                    log.warning("couldn't disconnect agent %s", agent_key.public_key)

    def _run_own(self, run: Run, request: HuntRequest, secret: str) -> None:
        try:
            with OryneAgent(secret, api_url=settings.oryne_api) as probe:
                me = probe.me()
            if me.get("network") != "stellar-testnet":
                raise RunError("This demo only runs Test mode agents (Stellar testnet).")
            agent = me["agent"]
            if agent.get("payees") and settings.careerkit_address not in agent["payees"]:
                run.emit("note", text="Your agent's mandate doesn't list CareerKit as a payee, so the wallet will refuse every payment.")
            run.emit(
                "wallet",
                mode="own",
                wallet=agent["wallet"],
                agent=agent["publicKey"],
                perTx=agent["perTx"],
                budget=agent["budget"],
                remaining=agent.get("remaining"),
                balance=me.get("walletBalance"),
            )
            self._hunt(run, request, secret)
        except Exception as error:  # noqa: BLE001
            log.exception("own run failed")
            text = str(error) or type(error).__name__
            if "401" in text or "nauthor" in text:
                text = "Oryne didn't recognise that agent secret. Check it's a Test mode agent."
            run.emit("error", text=text)
        finally:
            run.done = True
            run.emit("closed")

    # ------------------------------------------------------------ approvals on this page (demo only)

    def decide(self, run_id: str, approval_id: str, approve: bool) -> dict:
        run = self.get(run_id)
        if run.mode != "demo" or not self.owner:
            raise RunError("Approve it in your Oryne dashboard: it's your wallet.", 400)
        if not run.pending_approval or run.pending_approval["id"] != approval_id:
            raise RunError("That approval isn't waiting any more.", 409)
        run.emit("approving" if approve else "declining", id=approval_id)
        if approve:
            tx = self.owner.approve(approval_id)
            run.emit("approved_onchain", id=approval_id, tx=tx, explorer=f"https://stellar.expert/explorer/testnet/tx/{tx}")
            return {"ok": True, "tx": tx}
        self.owner.decline(approval_id)
        return {"ok": True}

    def cancel(self, run_id: str) -> None:
        self.get(run_id).cancelled.set()
