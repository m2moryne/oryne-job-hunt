// The try-it page: a form, then a live view of the agent's run (Server-Sent Events).
const $ = (id) => document.getElementById(id)
const esc = (value) =>
  String(value ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c])
const safeUrl = (url) => (/^https:\/\//.test(url || '') ? url : '#')
const short = (text, n = 10) => (text ? `${text.slice(0, n)}…` : '')

let config = null
let mode = 'demo'
let state = null

async function load() {
  config = await (await fetch('/api/config')).json()
  $('boards').innerHTML = config.boards
    .map((b) => `<label class="chip" title="${esc(b.about)}"><input type="checkbox" value="${esc(b.id)}" checked /> ${esc(b.name)}</label>`)
    .join('')
  $('services').innerHTML = config.services
    .map(
      (s) => `
      <label class="service">
        <input type="checkbox" value="${esc(s.id)}" ${s.id === 'search' ? 'checked disabled' : 'checked'} />
        <span><span class="name">${esc(s.name)}</span><br /><span class="about">${esc(s.about)}</span>
          <span class="flag" data-flag="${esc(s.id)}"></span></span>
        <span class="price">${esc(s.price)} USDC</span>
      </label>`,
    )
    .join('')
  $('payee').textContent = config.careerkitAddress
  $('payee').onclick = () => navigator.clipboard?.writeText(config.careerkitAddress)
  $('modelNote').textContent = config.model
    ? `Thinking: ${config.model}. Payments: Oryne on Stellar testnet.`
    : 'No model key on this server: the agent follows a fixed plan and uses templates.'
  if (!config.demoReady) switchMode('own')
  document.querySelectorAll('#services input, #perTx, #budget, #maxLetters').forEach((el) => el.addEventListener('input', estimate))
  estimate()
}

function chosen(selector) {
  return [...document.querySelectorAll(`${selector} input:checked`)].map((el) => el.value)
}

function price(id) {
  return Number(config.services.find((s) => s.id === id)?.price ?? 0)
}

/** What the agent could spend, and which services the limits will stop. */
function estimate() {
  const services = chosen('#services')
  const perTx = Number($('perTx').value)
  const budget = Number($('budget').value)
  const letters = Math.min(Number($('maxLetters').value) || 0, 5)
  for (const flag of document.querySelectorAll('[data-flag]')) {
    const over = price(flag.dataset.flag) > perTx
    flag.textContent = over ? `Over the ${perTx} USDC per-payment limit: the wallet refuses it until you approve` : ''
  }
  let most = price('search') * 2
  if (services.includes('match')) most += price('match') * 6
  if (services.includes('cover_letter')) most += price('cover_letter') * letters
  const pack = services.includes('application_pack') ? price('application_pack') : 0
  $('estimate').innerHTML =
    `Typical run: about <b>${most.toFixed(2)} USDC</b> in small payments` +
    (pack ? `, plus <b>${pack} USDC</b> for the pack if you approve it.` : '.') +
    ` The wallet stops at <b>${budget} USDC</b> whatever happens.`
}

function switchMode(next) {
  mode = next
  document.querySelectorAll('.tab').forEach((tab) => tab.classList.toggle('active', tab.dataset.mode === next))
  $('mode-demo').hidden = next !== 'demo'
  $('mode-own').hidden = next !== 'own'
}
document.querySelectorAll('.tab').forEach((tab) => (tab.onclick = () => switchMode(tab.dataset.mode)))

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
  $('cv').value = data.text
}

function showError(text) {
  $('formError').textContent = text
  $('formError').hidden = !text
}

$('form').onsubmit = async (event) => {
  event.preventDefault()
  showError('')
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
  $('start').textContent = 'Starting…'
  try {
    const response = await fetch('/api/runs', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body) })
    const data = await response.json()
    if (!response.ok) throw new Error(data.error || 'Could not start')
    $('agentSecret').value = ''
    begin(data.id, data.mode, body)
  } catch (error) {
    showError(error.message)
    $('start').disabled = false
    $('start').textContent = 'Start the job hunt'
  }
}

// ---------------------------------------------------------------- the live view

function begin(id, runMode, form) {
  state = { id, mode: runMode, jobs: {}, scores: {}, letters: {}, payments: [], spent: 0, budget: Number(form.budget) }
  $('idle').hidden = true
  $('running').hidden = false
  ;['timeline', 'jobs'].forEach((el) => ($(el).innerHTML = ''))
  ;['summary', 'pack', 'receipt', 'approval', 'jobsTitle'].forEach((el) => ($(el).hidden = true))
  $('walletLine').textContent = 'Connecting…'
  $('start').textContent = 'Running…'
  if (window.innerWidth < 900) $('live').scrollIntoView({ behavior: 'smooth' })
  const source = new EventSource(`/api/runs/${id}/events`)
  source.onmessage = (message) => handle(JSON.parse(message.data), source)
  source.onerror = () => {
    if (state?.closed) source.close()
  }
}

$('stop').onclick = () => state && fetch(`/api/runs/${state.id}/cancel`, { method: 'POST' })

function line(icon, html, cls = '') {
  const li = document.createElement('li')
  li.innerHTML = `<span class="icon">${icon}</span><span class="${cls}">${html}</span>`
  $('timeline').appendChild(li)
  li.scrollIntoView({ block: 'nearest' })
}

const txLink = (event) => (event.explorer ? ` <a class="tx" href="${safeUrl(event.explorer)}" target="_blank" rel="noopener">tx ${esc(short(event.tx))}</a>` : '')

const TOOL_TEXT = {
  search_jobs: (a) => `Searching for “${esc(a.query)}”`,
  score_job: (a) => `Scoring ${esc(state.jobs[a.job_id]?.title || 'a job')} against your CV`,
  write_cover_letter: (a) => `Writing a cover letter for ${esc(state.jobs[a.job_id]?.title || 'a job')}`,
  buy_application_pack: (a) => `Buying the application pack for ${esc(state.jobs[a.job_id]?.title || 'the best match')}`,
  wallet_status: () => 'Checking its budget',
}

function handle(event, source) {
  switch (event.type) {
    case 'setup':
      line('🔐', esc(event.text))
      break
    case 'wallet':
      state.budget = Number(event.budget)
      $('walletLine').innerHTML =
        `${esc(event.mode === 'demo' ? 'Demo wallet' : 'Your wallet')} ${esc(short(event.wallet, 8))} · agent ${esc(short(event.agent, 8))}` +
        (event.explorer ? ` · <a href="${safeUrl(event.explorer)}" target="_blank" rel="noopener">add_agent tx</a>` : '')
      $('limitLabel').textContent = `limit ${event.perTx} per payment · budget ${event.budget}`
      line('✅', `Agent connected: <b>${esc(event.perTx)} USDC</b> per payment, <b>${esc(event.budget)} USDC</b> budget` + txLink(event))
      break
    case 'note':
      line('ℹ️', esc(event.text), 'muted')
      break
    case 'thinking':
      line('💭', esc(event.text), 'muted')
      break
    case 'tool':
      line('🔧', (TOOL_TEXT[event.name] || ((a) => esc(event.name)))(event.args || {}))
      break
    case 'paying':
      line('💳', `Paying ${esc(event.amount)} USDC: ${esc(event.what)}`, 'muted')
      break
    case 'paid':
      state.spent = Number(event.spent)
      state.payments.push(event)
      line('✔', `Paid <b>${esc(event.amount)} USDC</b>, settled on Stellar` + txLink(event), 'paid')
      meter()
      break
    case 'refused':
      line(
        '⛔',
        event.by === 'wallet'
          ? `<b>The wallet contract refused</b> ${esc(event.amount)} USDC: ${esc(event.reason)}. Nothing moved.`
          : `Not signed: ${esc(event.reason)}`,
        'refused',
      )
      break
    case 'approval_needed':
      line('🙋', `Asking you to approve <b>${esc(event.amount)} USDC</b> for the ${esc(event.what)}`, 'ask')
      showApproval(event)
      break
    case 'approving':
      $('approval').innerHTML = '<p>Signing <code>approve_once</code> on Stellar…</p>'
      break
    case 'approved_onchain':
      line('👍', `Owner signed approve_once` + txLink(event), 'paid')
      break
    case 'approval_decided':
      $('approval').hidden = true
      if (event.status !== 'approved') line('👎', `Not approved (${esc(event.status)}). The agent carries on without it.`, 'muted')
      break
    case 'jobs':
      for (const job of event.jobs) state.jobs[job.id] = job
      line(
        '📋',
        `Found ${event.jobs.length} jobs · ` +
          Object.entries(event.boards || {})
            .map(([board, outcome]) => `${esc(board)}: ${esc(outcome)}`)
            .join(', '),
      )
      break
    case 'score':
      state.scores[event.job_id] = event
      renderJobs()
      break
    case 'letter':
      state.letters[event.job_id] = event.letter
      renderJobs()
      break
    case 'pack':
      renderPack(event)
      break
    case 'error':
      line('⚠️', esc(event.text), 'refused')
      break
    case 'done':
      $('summary').hidden = false
      $('summary').textContent = event.summary
      renderReceipt()
      break
    case 'closed':
      state.closed = true
      source.close()
      $('start').disabled = false
      $('start').textContent = 'Run it again'
      $('approval').hidden = true
      break
  }
}

function meter() {
  const pct = state.budget ? Math.min(100, (100 * state.spent) / state.budget) : 0
  $('meterFill').style.width = `${pct}%`
  $('spentLabel').textContent = `${state.spent.toFixed(2)} USDC spent`
}

function showApproval(event) {
  const box = $('approval')
  box.hidden = false
  const head = `<h4>The agent wants to spend ${esc(event.amount)} USDC</h4>
    <div>${esc(event.what)}${event.why ? `: ${esc(event.why)}` : ''}</div>
    <div class="muted tiny">The wallet refused it (${esc(event.reason)}): it's over the agent's limit. Approving signs a one-time exception on Stellar.</div>`
  if (state.mode !== 'demo') {
    box.innerHTML = `${head}<div class="actions"><a class="button ok-btn" href="${safeUrl(config.dashboard)}" target="_blank" rel="noopener">Open Approvals in Oryne</a></div>
      <p class="muted tiny">The agent waits here until you decide.</p>`
    return
  }
  box.innerHTML = `${head}<div class="actions"><button class="ok-btn" id="yes">Approve ${esc(event.amount)} USDC</button><button class="bad-btn" id="no">Decline</button></div>`
  const decide = async (approve) => {
    $('yes').disabled = $('no').disabled = true
    const response = await fetch(`/api/runs/${state.id}/approvals/${event.id}`, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ approve }),
    })
    if (!response.ok) line('⚠️', esc((await response.json()).error || 'Could not record that'), 'refused')
  }
  $('yes').onclick = () => decide(true)
  $('no').onclick = () => decide(false)
}

function renderJobs() {
  const ids = Object.keys(state.scores).sort((a, b) => state.scores[b].score - state.scores[a].score)
  $('jobsTitle').hidden = !ids.length
  $('jobs').innerHTML = ids
    .map((id) => {
      const job = state.jobs[id] || {}
      const s = state.scores[id]
      const cls = s.score >= 70 ? 'hi' : s.score >= 45 ? 'mid' : 'lo'
      const letter = state.letters[id]
      return `<div class="job">
        <div class="job-head">
          <div>
            <div class="job-title">${esc(job.title)}</div>
            <div class="job-meta">${esc(job.company)} · ${esc(job.location)}${job.salary ? ` · ${esc(job.salary)}` : ''}</div>
          </div>
          <div class="score ${cls}">${esc(s.score)}<small>${esc(s.verdict)}</small></div>
        </div>
        <ul>${(s.strengths || []).slice(0, 3).map((x) => `<li>✓ ${esc(x)}</li>`).join('')}${(s.gaps || []).slice(0, 2).map((x) => `<li>△ ${esc(x)}</li>`).join('')}</ul>
        <div class="links">
          <a href="${safeUrl(job.url)}" target="_blank" rel="noopener">Apply on ${esc(job.source)} ↗</a>
          ${letter ? '<span class="muted">· cover letter ready</span>' : ''}
        </div>
        ${letter ? `<details><summary>Cover letter</summary><div class="letter">${esc(letter)}</div><button class="ghost small" onclick="navigator.clipboard.writeText(this.previousElementSibling.textContent)">Copy</button></details>` : ''}
      </div>`
    })
    .join('')
}

function renderPack(pack) {
  const box = $('pack')
  box.hidden = false
  const list = (items) => (items || []).map((x) => `<li>${esc(x)}</li>`).join('')
  box.innerHTML = `<h4>Application pack · ${esc(pack.job)} at ${esc(pack.company)}</h4>
    <h3>Summary for the top of your CV</h3><p>${esc(pack.summary)}</p>
    <h3>CV bullets for this job</h3><ul>${list(pack.bullets)}</ul>
    <h3>Likely interview questions</h3>
    <ol>${(pack.interview || []).map((q) => `<li><b>${esc(q.question)}</b><br /><span class="muted">${esc(q.how_to_answer)}</span></li>`).join('')}</ol>
    <h3>Keywords to include (where true)</h3><p class="muted">${esc((pack.keywords || []).join(', '))}</p>
    <details><summary>Cover letter</summary><div class="letter">${esc(pack.cover_letter)}</div></details>`
}

function renderReceipt() {
  if (!state.payments.length) return
  const box = $('receipt')
  box.hidden = false
  box.innerHTML = `<h3 style="margin-top:0">Receipt</h3><table class="receipt-table">
    ${state.payments.map((p) => `<tr><td>${esc(p.what)}<br /><a class="tx" href="${safeUrl(p.explorer)}" target="_blank" rel="noopener">tx ${esc(short(p.tx, 12))}</a></td><td>${esc(p.amount)} USDC</td></tr>`).join('')}
    <tr class="total"><td>Total, paid by the agent from its wallet</td><td>${state.spent.toFixed(2)} USDC</td></tr></table>`
}

load()
