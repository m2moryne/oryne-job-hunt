# Oryne job hunt

A working sample of [Oryne](https://oryne.org): an AI job-hunting agent with its own wallet.

You give it your CV and the job you want, choose which paid services it may buy, and set its limits. It searches four job boards, scores the best jobs against your CV and writes cover letters. It pays for each step in test USDC on Stellar, from its own Oryne wallet. When it wants something its limits don't allow (the 5 USDC application pack, against a 1 USDC per-payment limit), the wallet refuses and the agent asks you. Approve it, and the payment goes through on Stellar.

It runs on Stellar testnet, so nothing costs real money.

## How it's put together

- **This repository** is the website: plain HTML, CSS and JavaScript, with no build step. It's hosted on Vercel.
- **The agent runs on the Oryne API** ([`services/api/src/jobs`](https://github.com/m2moryne/Oryne/tree/master/services/api/src/jobs) in the Oryne repository), under `/v1/jobs`. That includes CareerKit, the paid API the agent buys from (`/v1/jobs/careerkit`). The page only shows what the agent does, streamed live.

| Page              | API                                                 |
| ----------------- | --------------------------------------------------- |
| loads the form    | `GET /v1/jobs/config`                               |
| starts a hunt     | `POST /v1/jobs/runs`                                |
| shows it live     | `GET /v1/jobs/runs/:id/events` (server-sent events) |
| Approve / Decline | `POST /v1/jobs/runs/:id/approvals/:approvalId`      |
| Stop              | `POST /v1/jobs/runs/:id/cancel`                     |

Uploaded CVs (PDF, Word or text) are read in the browser, with copies of [pdf.js](https://github.com/mozilla/pdf.js) and [mammoth](https://github.com/mwilliamson/mammoth.js) kept in `assets/vendor`. The file never leaves the page; only the text goes to the API when a hunt starts. The API keeps it in memory for an hour at most.

## Run it locally

Serve this folder with the Oryne API running on `http://localhost:8787`. On `localhost` the page calls that API instead of the hosted one.

```sh
python -m http.server 5500      # or: npx serve -l 5500
```

In the Oryne repository:

```sh
echo "CORS_ORIGINS=http://localhost:5500" >> services/api/.env
npm run dev:api
ORYNE_API_URL=http://localhost:8787 npm run jobs-setup   # once: CareerKit's address and the demo wallet
# then restart the API so it reads the JOBS_* settings
```

## Deploy

1. **API:** add `JOBS_CAREERKIT_ADDRESS`, `JOBS_OWNER_SECRET` and `JOBS_WALLET` to the testnet API's environment, plus the site's address in `CORS_ORIGINS`. To create the three values, run `ORYNE_API_URL=https://testnet-apiv1.oryne.org npm run jobs-setup -- --print`. `GET /v1/info` shows `features.jobHunt: true` once it's on.
2. **Site:** import this repository in Vercel. There's no framework and no build command; `vercel.json` sets the security headers. The API the page calls is the `oryne-api` meta tag in `index.html`. If you change it, change `connect-src` in `vercel.json` to match.

## License

MIT. Job listings come from Remotive, Jobicy, Remote OK and Arbeitnow. Each job links back to its original posting.
