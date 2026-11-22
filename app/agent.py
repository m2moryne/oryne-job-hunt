"""The job-hunt agent. It pays CareerKit from its Oryne wallet, inside its mandate.

You give it your CV and what you're looking for, pick which paid services it may
buy, and set its limits. It searches, scores the jobs that look promising,
writes cover letters for the best ones, and asks you before it buys anything
its mandate doesn't allow (the application pack, at 5 USDC, is over its 1 USDC
per-payment limit by default).

Three layers keep the spending in check:
  1. the model only gets tools for the services you allowed;
  2. before signing, the SDK refuses anything but those services, at their list price;
  3. the wallet contract on Stellar refuses anything outside the mandate, whatever
     the first two do. That's the one nothing can get past.
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Callable

from oryne import OryneAgent, PaymentEvent, PaymentRefusedError

from . import llm
from .careerkit import SERVICES
from .config import settings

Emit = Callable[..., None]

EXPLORER = "https://stellar.expert/explorer/testnet/tx/"


@dataclass
class HuntRequest:
    cv: str
    role: str
    location: str = ""
    boards: list[str] = field(default_factory=lambda: ["remotive", "jobicy", "remoteok", "arbeitnow"])
    services: list[str] = field(default_factory=lambda: list(SERVICES))
    max_letters: int = 3


class Cancelled(Exception):
    pass


class JobHunt:
    def __init__(
        self,
        request: HuntRequest,
        agent_secret: str,
        emit: Emit,
        *,
        on_approval: Callable[[dict], None] | None = None,
        cancelled: threading.Event | None = None,
    ) -> None:
        self.request = request
        self.emit = emit
        self.on_approval = on_approval
        self.cancelled = cancelled or threading.Event()
        self.allowed = [s for s in request.services if s in SERVICES]
        self.jobs: dict[str, dict] = {}
        self.scores: dict[str, dict] = {}
        self.letters: dict[str, str] = {}
        self.spent = Decimal("0")
        self.payments: list[dict] = []
        allowed_urls = {f"{settings.careerkit_url}{SERVICES[s]['path']}" for s in self.allowed}
        self.agent = OryneAgent(
            agent_secret,
            api_url=settings.oryne_api,
            payees=[settings.careerkit_address],
            # Last check before signing: only the services the user allowed.
            before_pay=lambda check: check.url.split("?")[0] in allowed_urls or "You didn't allow this service",
            on_payment=self._on_payment,
        )

    # ------------------------------------------------------------ payments

    def _on_payment(self, event: PaymentEvent) -> None:
        resource = event.requirements.get("description") or event.requirements.get("resource", "")
        if event.type == "paying":
            self.emit("paying", amount=event.amount, what=resource)
        elif event.type == "paid":
            tx = (event.settlement or {}).get("transaction")
            self.spent += Decimal(event.amount)
            self.payments.append({"amount": event.amount, "what": resource, "tx": tx})
            self.emit("paid", amount=event.amount, what=resource, tx=tx, explorer=f"{EXPLORER}{tx}" if tx else None, spent=str(self.spent))
        else:
            self.emit("refused", amount=event.amount, what=resource, reason=event.reason, by=event.source)

    def _check(self) -> None:
        if self.cancelled.is_set():
            raise Cancelled()

    def _buy(self, service: str, *, params: dict | None = None, body: dict | None = None, why: str = "") -> dict:
        """Pays for one CareerKit call. Over the mandate, it asks the owner and waits."""
        self._check()
        if service not in self.allowed:
            return {"error": f"You didn't allow the {SERVICES[service]['name']} service."}
        info = SERVICES[service]
        url = f"{settings.careerkit_url}{info['path']}"
        method = "POST" if body is not None else "GET"
        kwargs: dict[str, Any] = {"params": params} if params else {}
        if body is not None:
            kwargs["json"] = body
        try:
            response = self.agent.fetch(url, method, max_price=info["price"], **kwargs)
        except PaymentRefusedError as refused:
            if refused.source != "wallet":
                return {"error": f"Not paid: {refused.reason}"}
            if "Budget" in refused.reason and service != "application_pack":
                return {"error": "The wallet refused: the agent's budget is used up. Stop buying and report back."}
            return self._ask_owner(service, url, method, kwargs, refused, why)
        if response.status_code >= 400:
            return {"error": f"CareerKit answered {response.status_code}: {response.text[:200]}"}
        return response.json()

    def _ask_owner(self, service: str, url: str, method: str, kwargs: dict, refused: PaymentRefusedError, why: str) -> dict:
        info = SERVICES[service]
        approval = self.agent.request_approval(
            pay_to=refused.requirements["payTo"],
            amount=info["price"],
            description=f"{info['name']}: {why}"[:200] if why else info["name"],
            resource=refused.requirements.get("resource"),
            reason=refused.reason,
        )
        payload = {"id": approval["id"], "amount": info["price"], "what": info["name"], "why": why, "reason": refused.reason}
        self.emit("approval_needed", **payload)
        if self.on_approval:
            self.on_approval(payload)
        decided = self._wait(approval["id"])
        self.emit("approval_decided", id=approval["id"], status=decided)
        if decided != "approved":
            return {"error": f"The owner didn't approve it ({decided}). Don't ask again; finish with what you have."}
        response = self.agent.fetch(url, method, max_price=info["price"], **kwargs)
        if response.status_code >= 400:
            return {"error": f"CareerKit answered {response.status_code}"}
        return response.json()

    def _wait(self, approval_id: str, timeout: float = 600) -> str:
        waited = 0.0
        while waited < timeout:
            self._check()
            status = self.agent.approval(approval_id)["status"]
            if status != "pending":
                return status
            self.cancelled.wait(3)
            waited += 3
        return "expired"

    # ------------------------------------------------------------ what the agent can do

    def search_jobs(self, query: str, location: str = "") -> str:
        result = self._buy(
            "search", params={"q": query, "location": location or self.request.location, "boards": ",".join(self.request.boards)}
        )
        if "error" in result:
            return json.dumps(result)
        fresh = [job for job in result["jobs"] if job["id"] not in self.jobs]
        for job in result["jobs"]:
            self.jobs[job["id"]] = job
        self.emit("jobs", query=query, boards=result.get("boards"), jobs=fresh)
        return json.dumps(
            {
                "found": len(result["jobs"]),
                "jobs": [
                    {k: job[k] for k in ("id", "title", "company", "location", "salary", "posted", "source")}
                    | {"summary": job["description"][:280]}
                    for job in result["jobs"][:15]
                ],
            }
        )

    def _job_body(self, job_id: str) -> dict | None:
        job = self.jobs.get(job_id)
        if not job:
            return None
        return {
            "cv": self.request.cv,
            "job": {k: job[k] for k in ("title", "company", "description", "tags", "url")},
        }

    def score_job(self, job_id: str) -> str:
        body = self._job_body(job_id)
        if not body:
            return json.dumps({"error": f"No job {job_id}: use an id from search_jobs"})
        if job_id in self.scores:
            return json.dumps(self.scores[job_id])
        result = self._buy("match", body=body)
        if "error" in result:
            return json.dumps(result)
        result.pop("payment", None)
        self.scores[job_id] = result
        self.emit("score", job_id=job_id, **result)
        return json.dumps({k: result.get(k) for k in ("score", "verdict", "strengths", "gaps")})

    def write_cover_letter(self, job_id: str) -> str:
        body = self._job_body(job_id)
        if not body:
            return json.dumps({"error": f"No job {job_id}"})
        if len(self.letters) >= self.request.max_letters:
            return json.dumps({"error": f"The user asked for at most {self.request.max_letters} cover letters."})
        result = self._buy("cover_letter", body=body)
        if "error" in result:
            return json.dumps(result)
        self.letters[job_id] = result["letter"]
        self.emit("letter", job_id=job_id, letter=result["letter"], method=result.get("method"))
        return json.dumps({"ok": True, "words": len(result["letter"].split())})

    def buy_application_pack(self, job_id: str, why: str) -> str:
        body = self._job_body(job_id)
        if not body:
            return json.dumps({"error": f"No job {job_id}"})
        result = self._buy("application_pack", body=body, why=why)
        if "error" in result:
            return json.dumps(result)
        result.pop("payment", None)
        self.emit("pack", job_id=job_id, **result)
        return json.dumps({"ok": True, "sections": [k for k in result if k not in ("job", "company", "method")]})

    def wallet_status(self) -> str:
        me = self.agent.me(refresh=True)
        agent = me.get("agent", {})
        return json.dumps(
            {
                "per_payment_limit": agent.get("perTx"),
                "budget": agent.get("budget"),
                "budget_left": agent.get("remaining"),
                "spent_this_run": str(self.spent),
            }
        )

    # ------------------------------------------------------------ running it

    def tools(self) -> list[dict]:
        def tool(name: str, description: str, properties: dict, required: list[str]) -> dict:
            return {
                "type": "function",
                "function": {
                    "name": name,
                    "description": description,
                    "parameters": {"type": "object", "properties": properties, "required": required},
                },
            }

        job_id = {"job_id": {"type": "string", "description": "A job id from search_jobs"}}
        tools = [tool("wallet_status", "The agent's spending limits and what's left of its budget. Free.", {}, [])]
        if "search" in self.allowed:
            tools.append(
                tool(
                    "search_jobs",
                    f"Search job boards. Costs {SERVICES['search']['price']} USDC per search. Use short queries (a job title).",
                    {"query": {"type": "string"}, "location": {"type": "string", "description": "Optional"}},
                    ["query"],
                )
            )
        if "match" in self.allowed:
            tools.append(tool("score_job", f"Score the user's CV against one job. Costs {SERVICES['match']['price']} USDC.", job_id, ["job_id"]))
        if "cover_letter" in self.allowed:
            tools.append(
                tool("write_cover_letter", f"Write a cover letter for one job. Costs {SERVICES['cover_letter']['price']} USDC.", job_id, ["job_id"])
            )
        if "application_pack" in self.allowed:
            tools.append(
                tool(
                    "buy_application_pack",
                    f"Buy the full application pack for the single best match. Costs {SERVICES['application_pack']['price']} USDC, "
                    "which is likely over your per-payment limit: the owner will be asked to approve it. Buy at most one.",
                    job_id | {"why": {"type": "string", "description": "One sentence for the owner: why this job"}},
                    ["job_id", "why"],
                )
            )
        return tools

    def run(self) -> dict:
        try:
            if llm.enabled():
                summary = self._run_model()
            else:
                self.emit("note", text="No model key is set, so the agent follows a fixed plan and CareerKit uses keyword matching and templates.")
                summary = self._run_plan()
        except Cancelled:
            summary = "Stopped."
        result = {
            "summary": summary,
            "spent": str(self.spent),
            "payments": self.payments,
            "scores": self.scores,
            "letters": self.letters,
        }
        self.emit("done", **result)
        return result

    def _run_model(self) -> str:
        services = ", ".join(f"{SERVICES[s]['name']} ({SERVICES[s]['price']} USDC)" for s in self.allowed)
        messages: list[dict] = [
            {
                "role": "system",
                "content": (
                    "You are a job-hunting agent with your own wallet. You pay for each service you use, in USDC, so "
                    "spend like it's the user's money: it is. Services you may buy: " + services + ".\n"
                    "Plan: 1) search once or twice with short job-title queries; 2) pick the up to 6 most promising "
                    "jobs from the results (title, location and seniority fit the user) and score each; 3) write cover "
                    f"letters for the best matches scoring 60 or more (at most {self.request.max_letters}); 4) if the "
                    "application pack is available and the best match scores 70 or more, buy one for it, explaining why "
                    "in one sentence. If a payment is refused or declined, don't retry it. "
                    "Finish with a short message to the user: the best jobs with their scores and why, what you bought "
                    "and what it cost in total. No markdown tables."
                ),
            },
            {
                "role": "user",
                "content": f"I'm looking for: {self.request.role}"
                + (f" (location: {self.request.location})" if self.request.location else "")
                + f"\n\nMy CV:\n{self.request.cv[:8000]}",
            },
        ]
        handlers = {
            "search_jobs": lambda a: self.search_jobs(a.get("query", self.request.role), a.get("location", "")),
            "score_job": lambda a: self.score_job(a.get("job_id", "")),
            "write_cover_letter": lambda a: self.write_cover_letter(a.get("job_id", "")),
            "buy_application_pack": lambda a: self.buy_application_pack(a.get("job_id", ""), a.get("why", "")),
            "wallet_status": lambda a: self.wallet_status(),
        }
        tools = self.tools()
        for _ in range(30):
            self._check()
            message = llm.chat(messages, tools=tools)
            calls = message.get("tool_calls") or []
            messages.append({k: v for k, v in message.items() if k in ("role", "content", "tool_calls")} | {"role": "assistant"})
            if message.get("content") and calls:
                self.emit("thinking", text=message["content"])
            if not calls:
                return (message.get("content") or "").strip()
            for call in calls:
                name = call["function"]["name"]
                try:
                    args = json.loads(call["function"].get("arguments") or "{}")
                except json.JSONDecodeError:
                    args = {}
                self.emit("tool", name=name, args=args)
                handler = handlers.get(name)
                output = handler(args) if handler else json.dumps({"error": f"No tool {name}"})
                messages.append({"role": "tool", "tool_call_id": call["id"], "content": output})
        return "I stopped after 30 steps."

    def _run_plan(self) -> str:
        """Without a model: the same steps, in a fixed order."""
        if "search" not in self.allowed:
            return "Job search isn't allowed, so there's nothing to look at."
        self.emit("tool", name="search_jobs", args={"query": self.request.role})
        found = json.loads(self.search_jobs(self.request.role))
        if "error" in found:
            return found["error"]
        ids = [job["id"] for job in found["jobs"][:6]]
        if "match" in self.allowed:
            for job_id in ids:
                self.emit("tool", name="score_job", args={"job_id": job_id})
                if "error" in json.loads(self.score_job(job_id)):
                    break
        ranked = sorted(ids, key=lambda i: self.scores.get(i, {}).get("score", 0), reverse=True)
        if "cover_letter" in self.allowed:
            for job_id in ranked[: self.request.max_letters]:
                self.emit("tool", name="write_cover_letter", args={"job_id": job_id})
                if "error" in json.loads(self.write_cover_letter(job_id)):
                    break
        if "application_pack" in self.allowed and ranked:
            best = self.jobs[ranked[0]]
            self.emit("tool", name="buy_application_pack", args={"job_id": ranked[0]})
            self.buy_application_pack(ranked[0], f"Best match: {best['title']} at {best['company']}")
        lines = [f"I looked at {len(self.jobs)} jobs and spent {self.spent} USDC."]
        for job_id in ranked[:3]:
            job, score = self.jobs[job_id], self.scores.get(job_id, {})
            lines.append(f"• {job['title']} at {job['company']}: {score.get('score', '?')}/100, {score.get('verdict', '')}")
        return "\n".join(lines)
