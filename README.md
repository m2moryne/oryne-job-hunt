# Job Hunt Agent

**An AI agent that hunts jobs for you and pays for every step from its own wallet, inside limits it can't break.**

You give it your CV and the job you want. It searches four job boards, scores the promising
jobs against your CV, writes cover letters for the best matches, and offers a full application
pack for the top one. Each of those is a paid API call, and the agent pays for each one itself in
USDC on Stellar from an [Oryne](https://oryne.org) wallet.

You choose **which paid services it may buy** and **how much it may spend**. Those limits live in
a smart contract on Stellar. When the agent wants something over its limit (the 5 USDC
application pack, with the default 1 USDC per payment), the wallet refuses and the agent asks you.
It goes ahead only if you approve.

It runs on **Stellar testnet**: test USDC, nothing real is spent.

```
🔐 Connecting an agent to the demo wallet: 1 USDC per payment, 3 USDC budget, may pay CareerKit only
✅ Agent connected                                            add_agent tx 847a2d77…
🔧 Searching for “python developer”
✔  Paid 0.05 USDC, settled on Stellar                         tx ac006848…
📋 Found 25 jobs · remotive: 16, jobicy: 30, remoteok: 7, arbeitnow: 24
🔧 Scoring Founding Staff Software Engineer • Python/FastAPI against your CV
✔  Paid 0.02 USDC                                             tx 589b5c09…
🔧 Writing a cover letter for Founding Staff Software Engineer
✔  Paid 0.25 USDC                                             tx 1ababddd…
🔧 Buying the application pack for the best match
⛔ The wallet contract refused 5 USDC: OverPerTxLimit. Nothing moved.
🙋 Asking you to approve 5 USDC for the Application pack
👍 Owner signed approve_once                                  tx 13c9cd4f…
✔  Paid 5 USDC                                                tx 214030f2…
```

## What's in it

| Part | What it is |
|---|---|
| **CareerKit** (`app/careerkit.py`, at `/careerkit`) | A paid API for job hunting. Each route is behind an Oryne paywall (`oryne.merchant.fastapi`): unpaid requests get `402 Payment Required`, paid ones settle in about 5 seconds. Any x402 client can pay it. |
| **The agent** (`app/agent.py`) | A tool-calling agent (DeepSeek by default) that pays CareerKit with `oryne.OryneAgent`. A refused payment becomes an approval request to the owner. |
| **The try-it page** (`/`) | Paste or upload a CV, choose the services and limits, and watch each payment land on Stellar. You approve or decline on the page. |
| **Job boards** (`app/sources.py`) | Real listings from [Remotive](https://remotive.com), [Jobicy](https://jobicy.com), [Remote OK](https://remoteok.com) and [Arbeitnow](https://www.arbeitnow.com). Each is credited, and applying always goes to the original posting. |

### What the agent can pay for

| Service | Price (test USDC) | What you get |
|---|---|---|
| Job search | 0.05 per search | The boards you pick, searched at once, deduplicated and ranked |
| Fit score | 0.02 per job | A fit score out of 100, with strengths, gaps and missing keywords |
| Cover letter | 0.25 per job | A letter for that job, written only from what's in your CV |
| Application pack | 5 | A tailored CV summary and bullets, a cover letter, likely interview questions and how to answer them |

### Three layers of limits

1. **The model only gets tools for the services you allowed.**
2. **Before signing, the SDK checks each payment.** It refuses anything but those services, at their list price, paid to CareerKit's address (`before_pay`, `max_price`, `payees`).
3. **The wallet contract on Stellar refuses anything outside the mandate**, whatever the first two do. The mandate covers the per-payment limit, the budget, the payee and the expiry. Nothing can get past this layer, not even a prompt-injected model or a leaked key.

## Two ways to try it

- **Demo wallet (instant).** The server connects a fresh agent to a demo wallet with the limits
  you chose. The agent may pay CareerKit only, and it expires in 3 hours. The server disconnects
  the agent when the run ends. When the agent asks for approval, the server signs `approve_once`
  as the demo wallet's owner.
- **Your own Oryne agent.** Sign in at [oryne.org/app](https://oryne.org/app) in **Test** mode,
  add test USDC and connect an agent, then paste its secret. The page uses the secret for that
  run only and never stores it. You approve in your Oryne dashboard, with your own passkey or
  Freighter.

## Run it

Needs Python 3.10+.

```sh
python -m venv .venv
.venv/Scripts/activate            # Windows; on macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt

python setup_testnet.py           # one time: CareerKit's Stellar address and the demo wallet, written to .env
python -m uvicorn app.main:app --port 8800
```

Open http://localhost:8800.

To use a model, put `LLM_API_KEY=` (a [DeepSeek](https://platform.deepseek.com) key) in `.env`.
Any OpenAI-compatible API with tool calling works: set `LLM_BASE_URL` and `LLM_MODEL` to use
another one. Without a key the app still runs. The agent follows a fixed plan, CareerKit scores
by matching skills, and letters come from a template. The page says when that's the case.

Tests: `python -m pytest -q`.

> **Windows note:** if importing `pydantic_core` fails with *"An Application Control policy has
> blocked this file"*, install the previous release: `pip install "pydantic==2.14.3" "pydantic_core==2.50.4"`.
> Smart App Control blocks native libraries until they've been out a while.

### Host it

```sh
docker build -t oryne-job-agent .
docker run -d --env-file .env -e PUBLIC_URL=https://jobs.example.com -p 8800:8800 oryne-job-agent
```

Put it behind HTTPS and set `PUBLIC_URL` to the address people use, because agents pay CareerKit
at `PUBLIC_URL/careerkit`. Two settings limit public use:
- `MAX_CONCURRENT_RUNS`, the most runs at once
- `RUNS_PER_IP_PER_HOUR`, the most runs from one address per hour

The demo wallet tops itself up from the testnet faucet.

## Privacy

The server keeps CVs in memory for the run only, and forgets each run after an hour. With a model
key set, it sends the CV and the job text to that model to score and write. It also stores no
agent secrets.
