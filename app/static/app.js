// The site: a home page, and the job hunt as a dashboard (form, then a live run over Server-Sent Events).
const $ = (id) => document.getElementById(id)
const esc = (value) =>
  String(value ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c])
const safeUrl = (url) => (/^https:\/\//.test(url || '') ? url : '#')
const short = (text, n = 8) => (text ? `${text.slice(0, n)}…` : '')
const usdc = (n) => Number(n || 0).toFixed(2)
const EXPLORER = 'https://stellar.expert/explorer/testnet'

let config = null
let mode = 'demo'
let state = null

// ---------------------------------------------------------------- setup

async function load() {
  config = await (await fetch('/api/config')).json()
  $('boards').innerHTML = config.boards
    .map((b) => `<label class="chip" title="${esc(b.about)}"><input type="checkbox" value="${esc(b.id)}" checked />${esc(b.name)}</label>`)
    .join('')
  $('services').innerHTML = config.services
    .map(
      (s) => `<label class="row-item">
        <input type="checkbox" value="${esc(s.id)}" checked ${s.id === 'search' ? 'disabled' : ''} />
        <span><span class="name">${esc(s.name)}</span><span class="about">${esc(s.about)}</span><span class="flag" data-flag="${esc(s.id)}"></span></span>
        <span class="price">${esc(s.price)}</span>
      </label>`,
    )
    .join('')
  $('priceTable').innerHTML = config.services
    .map((s) => `<div class="price-row"><div><h3>${esc(s.name)}</h3><p>${esc(s.about)}</p></div><strong>${esc(s.price)}<em>USDC</em></strong></div>`)
    .join('')
  $('payee').textContent = short(config.careerkitAddress, 10)
  $('payee').title = `${config.careerkitAddress} (click to copy)`
  $('payee').onclick = () => navigator.clipboard?.writeText(config.careerkitAddress)
  $('modelNote').textContent = config.model ? '' : 'No model key on this server, so the agent follows a fixed plan and writes from templates.'
  if (!config.demoReady) switchMode('own')
  document.querySelectorAll('#services input, #perTx, #budget, #maxLetters').forEach((el) => el.addEventListener('input', estimate))
  estimate()
}

const chosen = (selector) => [...document.querySelectorAll(`${selector} input:checked`)].map((el) => el.value)
const price = (id) => Number(config.services.find((s) => s.id === id)?.price ?? 0)

/** What the agent could spend, and which services its limits will stop. */
function estimate() {
  const services = chosen('#services')
  const perTx = Number($('perTx').value)
  const budget = Number($('budget').value)
  const letters = Math.min(Number($('maxLetters').value) || 0, 5)
  for (const flag of document.querySelectorAll('[data-flag]')) {
    flag.textContent = mode === 'demo' && price(flag.dataset.flag) > perTx ? 'Needs your approval' : ''
  }
  let most = price('search') * 2
  if (services.includes('match')) most += price('match') * 6
  if (services.includes('cover_letter')) most += price('cover_letter') * letters
  $('estimate').innerHTML =
    mode === 'demo'
      ? `Usually about <b>${most.toFixed(2)} USDC</b>. It can never spend more than <b>${budget} USDC</b>.`
      : `Usually about <b>${most.toFixed(2)} USDC</b>, inside your agent's own limits.`
}

function switchMode(next) {
  mode = next
  document.querySelectorAll('.seg').forEach((seg) => seg.classList.toggle('active', seg.dataset.mode === next))
  $('mode-demo').hidden = next !== 'demo'
  $('mode-own').hidden = next !== 'own'
  estimate()
}
document.querySelectorAll('.seg').forEach((seg) => (seg.onclick = () => switchMode(seg.dataset.mode)))

$('sample').onclick = () => {
  $('cv').value = config.sampleCv
  if (!$('role').value) $('role').value = 'Python developer'
}

$('cvFile').onchange = async (event) => {
  const file = event.target.files[0]
  if (!file) return
  const body = new FormData()
  body.append('file', file)
  const response = await fetch('/api/cv-file', { method: 'POST', body })
  const data = await response.json()
  if (!response.ok) return showError(data.detail || 'Could not read that file')
  showError('')
  $('cv').value = data.text
}

function showError(text) {
  $('formError').textContent = text
  $('formError').hidden = !text
}

$('form').onsubmit = async (event) => {
  event.preventDefault()
  showError('')
  if (state && !state.closed) return showError('A hunt is already running. Stop it first.')
  const body = {
    mode,
    cv: $('cv').value,
    role: $('role').value,
    location: $('location').value,
    boards: chosen('#boards'),
    services: ['search', ...chosen('#services').filter((s) => s !== 'search')],
    maxLetters: Number($('maxLetters').value),
    perTx: $('perTx').value,
    budget: $('budget').value,
    agentSecret: $('agentSecret').value,
  }
  $('start').disabled = true
  try {
    const response = await fetch('/api/runs', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body) })
    const data = await response.json()
    if (!response.ok) throw new Error(data.error || 'Could not start')
    $('agentSecret').value = ''
    begin(data.id, data.mode, body)
  } catch (error) {
    showError(error.message)
  } finally {
    $('start').disabled = false
  }
}

// ---------------------------------------------------------------- the dashboard frame

const sheet = $('sheet')
const VIEWS = {
  setup: ['New job hunt', 'Set it up once. The agent does the rest.'],
  overview: ['Overview', 'What the agent is doing, and what it has spent.'],
  shortlist: ['Shortlist', 'The jobs it scored, best first.'],
  documents: ['Documents', 'Cover letters and the application pack.'],
  receipt: ['Receipt', 'Every payment, each one a Stellar transaction.'],
}

function show(view) {
  document.querySelectorAll('.side-nav button').forEach((b) => b.classList.toggle('active', b.dataset.view === view))
  document.querySelectorAll('.view').forEach((v) => (v.hidden = v.dataset.view !== view))
  $('viewTitle').textContent = VIEWS[view][0]
  $('viewNote').textContent = VIEWS[view][1]
  document.querySelector('.main-body').scrollTop = 0
}
document.querySelectorAll('.side-nav button').forEach((b) => (b.onclick = () => show(b.dataset.view)))
document.querySelectorAll('[data-goto]').forEach((b) => (b.onclick = () => show(b.dataset.goto)))

function openSheet() {
  sheet.hidden = false
  sheet.classList.remove('closing-out')
  document.body.classList.add('locked')
  $('runPill').hidden = true
  if (location.hash !== '#hunt') history.replaceState(null, '', '#hunt')
}

function closeSheet() {
  sheet.classList.add('closing-out')
  setTimeout(() => {
    sheet.hidden = true
    document.body.classList.remove('locked')
    pill()
  }, 230)
  history.replaceState(null, '', location.pathname)
}

/** While a hunt runs with the dashboard closed, a pill in the corner shows it and reopens the dashboard. */
function pill() {
  const show = Boolean(state && !state.closed && (sheet.hidden || sheet.classList.contains('closing-out')))
  $('runPill').hidden = !show
  if (!show) return
  $('runPill').classList.toggle('asks', state.asking)
  $('runPillText').textContent = state.asking ? 'Your agent needs your approval' : `Job hunt running · ${usdc(state.spent)} USDC`
}

document.querySelectorAll('[data-open]').forEach((el) => el.addEventListener('click', openSheet))
document.querySelectorAll('[data-close]').forEach((el) =>
  el.addEventListener('click', (event) => {
    event.preventDefault()
    closeSheet()
  }),
)
document.addEventListener('keydown', (event) => event.key === 'Escape' && !sheet.hidden && closeSheet())
if (location.hash === '#hunt') openSheet()

const seen = new IntersectionObserver(
  (entries) => entries.forEach((entry) => entry.isIntersecting && (entry.target.classList.add('in'), seen.unobserve(entry.target))),
  { threshold: 0.12 },
)
document.querySelectorAll('.reveal').forEach((el) => seen.observe(el))

function setStatus(kind, text) {
  $('status').className = `status ${kind}`
  $('status').querySelector('span').textContent = text
}

function count(id, n) {
  $(id).hidden = !n
  $(id).textContent = n
}

// ---------------------------------------------------------------- a run

function begin(id, runMode, form) {
  state = {
    id, mode: runMode, jobs: {}, found: 0, scores: {}, letters: {}, payments: [], spent: 0,
    budget: Number(form.budget), perTx: form.perTx, asking: false, closed: false,
  }
  $('timeline').innerHTML = ''
  $('jobs').innerHTML = ''
  $('letters').innerHTML = ''
  $('receipt').innerHTML = ''
  $('pack').hidden = true
  $('approval').hidden = true
  $('summaryPanel').hidden = true
  $('mandate').innerHTML = ''
  $('mandateTx').hidden = true
  for (const empty of ['jobsEmpty', 'docsEmpty', 'receiptEmpty']) $(empty).hidden = false
  for (const n of ['navJobs', 'navDocs', 'navPays', 'navAsk']) count(n, 0)
  $('overviewEmpty').hidden = true
  $('overviewLive').hidden = false
  $('liveDot').hidden = false
  $('stop').hidden = false
  $('wcAddress').textContent = 'Connecting…'
  $('wcAddress').removeAttribute('href')
  stats()
  setStatus('running', 'Connecting')
  show('overview')

  const source = new EventSource(`/api/runs/${id}/events`)
  source.onmessage = (message) => {
    handle(JSON.parse(message.data), source)
    stats()
    pill()
  }
  source.onerror = () => state?.closed && source.close()
}

$('stop').onclick = () => state && fetch(`/api/runs/${state.id}/cancel`, { method: 'POST' })

function line(tag, html, kind = '', tx = '') {
  const li = document.createElement('li')
  li.className = kind
  li.innerHTML = `<span class="tag">${esc(tag)}</span><span>${html}</span><span class="tx-cell">${tx}</span>`
  const list = $('timeline')
  list.appendChild(li)
  list.scrollTop = list.scrollHeight
}

const txLink = (event) =>
  event.explorer ? `<a class="tx" href="${safeUrl(event.explorer)}" target="_blank" rel="noopener">${esc(short(event.tx))} ↗</a>` : ''
const jobName = (id, fallback) => esc(state.jobs[id]?.title || fallback)

const TOOL_TEXT = {
  search_jobs: (a) => ['Search', `Searching for “${esc(a.query)}”`],
  score_job: (a) => ['Score', `Scoring ${jobName(a.job_id, 'a job')}`],
  write_cover_letter: (a) => ['Write', `Cover letter for ${jobName(a.job_id, 'a job')}`],
  buy_application_pack: (a) => ['Pack', `Application pack for ${jobName(a.job_id, 'the best match')}`],
  wallet_status: () => ['Check', 'Checking its budget'],
}

function handle(event, source) {
  switch (event.type) {
    case 'setup':
      line('Wallet', 'Connecting an agent with your limits', 'quiet')
      break
    case 'wallet':
      state.budget = Number(event.budget)
      state.perTx = event.perTx
      $('wcLimit').textContent = `${event.perTx} per payment`
      $('wcAddress').textContent = `${event.mode === 'demo' ? 'Demo wallet' : 'Your wallet'} ${short(event.wallet, 6)}`
      $('wcAddress').href = `${EXPLORER}/contract/${event.wallet}`
      mandate(event)
      line('Wallet', 'Agent connected with your limits', 'paid', txLink(event))
      setStatus('running', 'Running')
      break
    case 'note':
    case 'thinking':
      line('Note', esc(event.text), 'quiet')
      break
    case 'tool': {
      const [tag, text] = (TOOL_TEXT[event.name] || (() => ['Tool', esc(event.name)]))(event.args || {})
      line(tag, text)
      break
    }
    case 'paid':
      state.spent = Number(event.spent)
      state.payments.push({ ...event, at: event.at })
      line('Paid', `${esc(event.amount)} USDC · ${esc(event.what.toLowerCase())}`, 'paid', txLink(event))
      receipt()
      break
    case 'refused':
      line(
        'Refused',
        event.by === 'wallet' ? `The wallet refused ${esc(event.amount)} USDC (${esc(event.reason)}). Nothing moved.` : `Not signed: ${esc(event.reason)}`,
        'refused',
      )
      break
    case 'approval_needed':
      state.asking = true
      line('Asks', `Your approval for ${esc(event.amount)} USDC`, 'ask')
      approval(event)
      count('navAsk', 1)
      setStatus('asks', 'Needs you')
      break
    case 'approving':
      $('approval').querySelector('.actions').innerHTML = '<span class="small muted">Signing on Stellar…</span>'
      break
    case 'approved_onchain':
      line('Approved', 'One-time approval signed', 'paid', txLink(event))
      break
    case 'approval_decided':
      state.asking = false
      $('approval').hidden = true
      count('navAsk', 0)
      setStatus('running', 'Running')
      if (event.status !== 'approved') line('Declined', 'The agent carries on without it', 'quiet')
      break
    case 'jobs':
      for (const job of event.jobs) state.jobs[job.id] = job
      state.found += event.jobs.length
      state.boards = Object.values(event.boards || {}).filter((o) => o.endsWith('jobs')).length
      line('Found', `${event.jobs.length} jobs across ${state.boards} boards`)
      break
    case 'score':
      state.scores[event.job_id] = event
      jobs()
      break
    case 'letter':
      state.letters[event.job_id] = event.letter
      letters()
      jobs()
      break
    case 'pack':
      pack(event)
      break
    case 'error':
      line('Error', esc(event.text), 'refused')
      break
    case 'done':
      if (event.summary) {
        $('summaryPanel').hidden = false
        $('summary').textContent = event.summary
      }
      break
    case 'closed':
      state.closed = true
      state.asking = false
      source.close()
      $('approval').hidden = true
      $('stop').hidden = true
      $('liveDot').hidden = true
      count('navAsk', 0)
      setStatus('done', 'Finished')
      break
  }
}

function stats() {
  const scored = Object.values(state.scores).sort((a, b) => b.score - a.score)
  $('statSpent').textContent = usdc(state.spent)
  $('statSpentNote').textContent = `of ${usdc(state.budget)} USDC budget`
  $('statJobs').textContent = state.found
  $('statJobsNote').textContent = state.found ? `${Object.keys(state.scores).length} scored` : 'Searching…'
  $('statBest').textContent = scored[0] ? scored[0].score : '–'
  $('statBestNote').textContent = scored[0] ? `${scored[0].job} · ${scored[0].company}` : 'Not scored yet'
  $('statPays').textContent = state.payments.length
  $('wcSpent').textContent = usdc(state.spent)
  const ratio = state.budget ? Math.min(1, state.spent / state.budget) : 0
  $('wcFill').style.width = `${ratio * 100}%`
  $('wcFill').classList.toggle('full', ratio >= 1)
  $('wcLeft').textContent = `${usdc(Math.max(0, state.budget - state.spent))} left`
  count('navJobs', Object.keys(state.scores).length)
  count('navDocs', Object.keys(state.letters).length + (state.hasPack ? 1 : 0))
  count('navPays', state.payments.length)
}

function mandate(event) {
  const rows = [
    ['Per payment', `${event.perTx} USDC`],
    ['Budget', `${event.budget} USDC`],
    ['May pay', event.mode === 'demo' ? 'CareerKit only' : 'As set in your mandate'],
    ['Expires', event.mode === 'demo' ? 'In 3 hours' : 'As set in your mandate'],
    ['Agent', `<a class="tx" href="${EXPLORER}/account/${esc(event.agent)}" target="_blank" rel="noopener">${esc(short(event.agent, 10))} ↗</a>`],
  ]
  $('mandate').innerHTML = rows.map(([k, v]) => `<div><dt>${k}</dt><dd>${k === 'Agent' ? v : esc(v)}</dd></div>`).join('')
  if (event.explorer) {
    $('mandateTx').href = safeUrl(event.explorer)
    $('mandateTx').hidden = false
  }
}

function approval(event) {
  const box = $('approval')
  box.hidden = false
  const actions =
    state.mode === 'demo'
      ? `<button class="button primary sm" id="yes">Approve ${esc(event.amount)} USDC</button><button class="button outline sm" id="no">Decline</button>`
      : `<a class="button primary sm" href="${safeUrl(config.dashboard)}/approvals" target="_blank" rel="noopener">Approve in Oryne ↗</a>`
  box.innerHTML = `<div>
      <p class="label">Needs your approval</p>
      <h3>${esc(event.amount)} USDC for the ${esc(event.what.toLowerCase())}</h3>
      <p class="why">${event.why ? `${esc(event.why)}. ` : ''}It's over the agent's ${esc(state.perTx)} USDC limit, so the wallet refused it.</p>
    </div>
    <div class="actions">${actions}</div>`
  if (state.mode !== 'demo') return
  const decide = async (approve) => {
    $('yes').disabled = $('no').disabled = true
    const response = await fetch(`/api/runs/${state.id}/approvals/${event.id}`, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ approve }),
    })
    if (!response.ok) line('Error', esc((await response.json()).error || 'Could not record that'), 'refused')
  }
  $('yes').onclick = () => decide(true)
  $('no').onclick = () => decide(false)
}

function jobs() {
  const ids = Object.keys(state.scores).sort((a, b) => state.scores[b].score - state.scores[a].score)
  $('jobsEmpty').hidden = ids.length > 0
  $('jobs').innerHTML = ids
    .map((id) => {
      const job = state.jobs[id] || {}
      const s = state.scores[id]
      const cls = s.score >= 70 ? 'hi' : s.score < 40 ? 'lo' : ''
      const notes =
        (s.strengths || []).slice(0, 2).map((x) => `<li>${esc(x)}</li>`).join('') +
        (s.gaps || []).slice(0, 2).map((x) => `<li class="gap">${esc(x)}</li>`).join('')
      return `<article class="job">
        <div class="job-head">
          <div class="score ${cls}"><strong>${esc(s.score)}</strong><span>${esc(s.verdict)}</span></div>
          <div>
            <div class="job-title">${esc(job.title)}</div>
            <div class="job-meta">${esc(job.company)} · ${esc(job.location)}${job.salary ? ` · ${esc(job.salary)}` : ''} · ${esc(job.source)}</div>
          </div>
          <div class="job-side">
            ${state.letters[id] ? '<span class="pill">Letter ready</span>' : ''}
            <a href="${safeUrl(job.url)}" target="_blank" rel="noopener">Apply ↗</a>
          </div>
        </div>
        ${notes ? `<ul class="notes">${notes}</ul>` : ''}
      </article>`
    })
    .join('')
}

function letters() {
  const ids = Object.keys(state.letters)
  $('docsEmpty').hidden = ids.length > 0 || state.hasPack
  $('letters').innerHTML = ids
    .map((id) => {
      const job = state.jobs[id] || {}
      return `<details class="doc"${ids.length === 1 ? ' open' : ''}>
        <summary><span>${esc(job.title)} · ${esc(job.company)}</span><span>Cover letter</span></summary>
        <div class="letter"><button class="copy-btn" type="button">Copy</button>${esc(state.letters[id])}</div>
      </details>`
    })
    .join('')
  $('letters').querySelectorAll('.copy-btn').forEach((b) =>
    (b.onclick = () => {
      navigator.clipboard?.writeText(state.letters[ids[[...$('letters').querySelectorAll('.copy-btn')].indexOf(b)]])
      b.textContent = 'Copied'
    }),
  )
}

function pack(p) {
  state.hasPack = true
  $('docsEmpty').hidden = true
  const list = (items) => (items || []).map((x) => `<li>${esc(x)}</li>`).join('')
  $('pack').hidden = false
  $('pack').innerHTML = `<section class="pnl">
    <header><h2>Application pack · ${esc(p.job)}, ${esc(p.company)}</h2><span class="pill">5 USDC · approved</span></header>
    <div class="pack-grid">
      <div><h4>CV summary</h4><p>${esc(p.summary)}</p></div>
      <div><h4>Keywords to include</h4><p class="muted">${esc((p.keywords || []).join(', '))}</p></div>
      <div><h4>CV bullets</h4><ul>${list(p.bullets)}</ul></div>
      <div><h4>Interview questions</h4><ol>${(p.interview || []).map((q) => `<li><b>${esc(q.question)}</b><br /><span class="muted">${esc(q.how_to_answer)}</span></li>`).join('')}</ol></div>
    </div>
    <details class="doc"><summary><span>Cover letter for this job</span><span>Show</span></summary><div class="letter">${esc(p.cover_letter)}</div></details>
  </section>`
}

function receipt() {
  $('receiptEmpty').hidden = state.payments.length > 0
  const time = (at) => new Date(at * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })
  $('receipt').innerHTML = `<table class="rtable">
    <thead><tr><th>Time</th><th>For</th><th>Transaction</th><th class="num">USDC</th></tr></thead>
    <tbody>${state.payments
      .map(
        (p) => `<tr><td class="muted">${esc(time(p.at))}</td><td>${esc(p.what)}</td>
          <td><a class="tx" href="${safeUrl(p.explorer)}" target="_blank" rel="noopener">${esc(short(p.tx, 10))} ↗</a></td>
          <td class="num">${esc(p.amount)}</td></tr>`,
      )
      .join('')}
    <tr class="total"><td></td><td>Total</td><td></td><td class="num">${usdc(state.spent)}</td></tr></tbody></table>`
}

load()
