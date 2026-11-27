// The try-it page: a form, then a live view of the agent's run (Server-Sent Events).
const $ = (id) => document.getElementById(id)
const esc = (value) =>
  String(value ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c])
const safeUrl = (url) => (/^https:\/\//.test(url || '') ? url : '#')
const short = (text, n = 8) => (text ? `${text.slice(0, n)}…` : '')

let config = null
let mode = 'demo'
let state = null

async function load() {
  config = await (await fetch('/api/config')).json()
  $('boards').innerHTML = config.boards
    .map((b) => `<label class="chip" title="${esc(b.about)}"><input type="checkbox" value="${esc(b.id)}" checked />${esc(b.name)}</label>`)
    .join('')
  $('services').innerHTML = config.services
    .map(
      (s) => `
      <label class="item">
        <input type="checkbox" value="${esc(s.id)}" checked ${s.id === 'search' ? 'disabled' : ''} />
        <span><span class="name">${esc(s.name)}</span><span class="about">${esc(s.about)}</span><span class="flag" data-flag="${esc(s.id)}"></span></span>
        <span class="price">${esc(s.price)}<em>USDC</em></span>
      </label>`,
    )
    .join('')
  $('payee').textContent = short(config.careerkitAddress, 12)
  $('payee').title = `${config.careerkitAddress} (click to copy)`
  $('payee').onclick = () => navigator.clipboard?.writeText(config.careerkitAddress)
  $('modelNote').textContent = config.model ? '' : 'No model key on this server: the agent follows a fixed plan.'
  if (!config.demoReady) switchMode('own')
  document.querySelectorAll('#services input, #perTx, #budget, #maxLetters').forEach((el) => el.addEventListener('input', estimate))
  estimate()
  $('priceTable').innerHTML = config.services
    .map((s) => `<div class="price-row"><div><h3>${esc(s.name)}</h3><p>${esc(s.about)}</p></div><strong>${esc(s.price)}<em>USDC</em></strong></div>`)
    .join('')
}

// ---------------------------------------------------------------- the home page and the panel

const sheet = $('sheet')

function openSheet() {
  sheet.hidden = false
  sheet.classList.remove('closing-out')
  document.body.classList.add('locked')
  $('runPill').hidden = true
  if (location.hash !== '#hunt') history.replaceState(null, '', '#hunt')
  if (!state) setTimeout(() => $('cv').focus({ preventScroll: true }), 300)
}

function closeSheet() {
  sheet.classList.add('closing-out')
  setTimeout(() => {
    sheet.hidden = true
    document.body.classList.remove('locked')
  }, 240)
  history.replaceState(null, '', location.pathname)
  pill()
}

/** While a run goes on with the panel closed, a pill in the corner shows it and reopens the panel. */
function pill() {
  const panelClosed = sheet.hidden || sheet.classList.contains('closing-out')
  const show = Boolean(state && !state.closed && panelClosed)
  $('runPill').hidden = !show
  if (!show) return
  const asks = !$('approval').hidden
  $('runPill').classList.toggle('asks', asks)
  $('runPillText').textContent = asks ? 'Your agent needs your approval' : `Job hunt running · ${state.spent.toFixed(2)} USDC`
}

document.querySelectorAll('[data-open]').forEach((el) => el.addEventListener('click', openSheet))
$('closeSheet').onclick = closeSheet
document.addEventListener('keydown', (event) => event.key === 'Escape' && !sheet.hidden && closeSheet())
if (location.hash === '#hunt') openSheet()

// Sections fade up as they come into view.
const seen = new IntersectionObserver(
  (entries) => entries.forEach((entry) => entry.isIntersecting && (entry.target.classList.add('in'), seen.unobserve(entry.target))),
  { threshold: 0.12 },
)
document.querySelectorAll('.reveal').forEach((el) => seen.observe(el))

const chosen = (selector) => [...document.querySelectorAll(`${selector} input:checked`)].map((el) => el.value)
const price = (id) => Number(config.services.find((s) => s.id === id)?.price ?? 0)

/** What the agent could spend, and which services its limits will stop. */
function estimate() {
  const services = chosen('#services')
  const perTx = Number($('perTx').value)
  const budget = Number($('budget').value)
  const letters = Math.min(Number($('maxLetters').value) || 0, 5)
  for (const flag of document.querySelectorAll('[data-flag]')) {
    flag.textContent = price(flag.dataset.flag) > perTx ? 'Over the per-payment limit: needs your approval' : ''
  }
  let most = price('search') * 2
  if (services.includes('match')) most += price('match') * 6
  if (services.includes('cover_letter')) most += price('cover_letter') * letters
  const pack = services.includes('application_pack') ? price('application_pack') : 0
  $('estimate').innerHTML =
    `A typical run spends about <b>${most.toFixed(2)} USDC</b>` +
    (pack ? `, plus <b>${pack}</b> for the pack if you approve it` : '') +
    `. The wallet stops at <b>${budget} USDC</b>, whatever happens.`
}

function switchMode(next) {
  mode = next
  document.querySelectorAll('.seg').forEach((seg) => seg.classList.toggle('active', seg.dataset.mode === next))
  $('mode-demo').hidden = next !== 'demo'
  $('mode-own').hidden = next !== 'own'
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
  setStart('Starting…', true)
  try {
    const response = await fetch('/api/runs', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body) })
    const data = await response.json()
    if (!response.ok) throw new Error(data.error || 'Could not start')
    $('agentSecret').value = ''
    begin(data.id, data.mode, body)
  } catch (error) {
    showError(error.message)
    setStart('Start', false)
  }
}

function setStart(text, disabled) {
  $('start').textContent = text
  $('start').disabled = disabled
}

// ---------------------------------------------------------------- the live view

function begin(id, runMode, form) {
  state = { id, mode: runMode, jobs: {}, scores: {}, letters: {}, payments: [], spent: 0, budget: Number(form.budget) }
  $('idle').hidden = true
  $('running').hidden = false
  $('timeline').innerHTML = ''
  $('jobs').innerHTML = ''
  for (const el of ['summary', 'pack', 'receipt', 'approval', 'jobsTitle']) $(el).hidden = true
  $('walletLine').textContent = 'Connecting…'
  $('limitLabel').textContent = ''
  meter()
  setStart('Running…', true)
  if (window.innerWidth < 1024) $('live').scrollIntoView({ behavior: 'smooth' })
  const source = new EventSource(`/api/runs/${id}/events`)
  source.onmessage = (message) => {
    handle(JSON.parse(message.data), source)
    pill()
  }
  source.onerror = () => state?.closed && source.close()
}

$('stop').onclick = () => state && fetch(`/api/runs/${state.id}/cancel`, { method: 'POST' })

function line(tag, html, kind = '') {
  const li = document.createElement('li')
  li.className = kind
  li.innerHTML = `<span class="tag">${esc(tag)}</span><span>${html}</span>`
  $('timeline').appendChild(li)
  li.scrollIntoView({ block: 'nearest' })
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
      $('walletLine').innerHTML = `${event.mode === 'demo' ? 'Demo wallet' : 'Your wallet'} ${esc(short(event.wallet))}`
      $('limitLabel').textContent = `${event.perTx} per payment · ${event.budget} budget`
      line('Wallet', `Agent connected: ${esc(event.perTx)} USDC per payment, ${esc(event.budget)} budget ${txLink(event)}`)
      break
    case 'note':
      line('Note', esc(event.text), 'quiet')
      break
    case 'thinking':
      line('Thinks', esc(event.text), 'quiet')
      break
    case 'tool': {
      const [tag, text] = (TOOL_TEXT[event.name] || (() => ['Tool', esc(event.name)]))(event.args || {})
      line(tag, text)
      break
    }
    case 'paid':
      state.spent = Number(event.spent)
      state.payments.push(event)
      line('Paid', `${esc(event.amount)} USDC, settled on Stellar ${txLink(event)}`, 'paid')
      meter()
      break
    case 'refused':
      line(
        'Refused',
        event.by === 'wallet'
          ? `The wallet contract refused ${esc(event.amount)} USDC (${esc(event.reason)}). Nothing moved.`
          : `Not signed: ${esc(event.reason)}`,
        'refused',
      )
      break
    case 'approval_needed':
      line('Asks', `Approval for ${esc(event.amount)} USDC: ${esc(event.what)}`, 'ask')
      showApproval(event)
      break
    case 'approving':
      $('approval').innerHTML = '<p class="label">Approval</p><p>Signing approve_once on Stellar…</p>'
      break
    case 'approved_onchain':
      line('Approved', `One-time approval signed ${txLink(event)}`, 'paid')
      break
    case 'approval_decided':
      $('approval').hidden = true
      if (event.status !== 'approved') line('Declined', 'The agent carries on without it', 'quiet')
      break
    case 'jobs':
      for (const job of event.jobs) state.jobs[job.id] = job
      line(
        'Found',
        `${event.jobs.length} jobs <span class="tx boards">${Object.entries(event.boards || {})
          .map(([board, outcome]) => `${esc(board)} ${esc(outcome.replace(' jobs', ''))}`)
          .join(' · ')}</span>`,
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
      line('Error', esc(event.text), 'refused')
      break
    case 'done':
      $('summary').hidden = !event.summary
      $('summary').textContent = event.summary
      renderReceipt()
      break
    case 'closed':
      state.closed = true
      source.close()
      $('approval').hidden = true
      setStart('Run again', false)
      break
  }
}

function meter() {
  const pct = state.budget ? Math.min(100, (100 * state.spent) / state.budget) : 0
  $('meterFill').style.width = `${pct}%`
  $('spentLabel').textContent = state.spent.toFixed(2)
}

function showApproval(event) {
  const box = $('approval')
  box.hidden = false
  const head = `<p class="label">Needs your approval</p>
    <h3>${esc(event.amount)} USDC for the ${esc(event.what.toLowerCase())}</h3>
    <p>${event.why ? `${esc(event.why)}. ` : ''}It's over the agent's limit, so the wallet refused it. Approving signs a one-time exception on Stellar.</p>`
  if (state.mode !== 'demo') {
    box.innerHTML = `${head}<div class="actions"><a class="button primary sm" href="${safeUrl(config.dashboard)}/approvals" target="_blank" rel="noopener">Open Oryne approvals</a></div>`
    return
  }
  box.innerHTML = `${head}<div class="actions">
    <button class="button primary sm" id="yes">Approve</button>
    <button class="button outline sm" id="no">Decline</button></div>`
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

function renderJobs() {
  const ids = Object.keys(state.scores).sort((a, b) => state.scores[b].score - state.scores[a].score)
  $('jobsTitle').hidden = !ids.length
  $('jobs').innerHTML = ids
    .map((id) => {
      const job = state.jobs[id] || {}
      const s = state.scores[id]
      const cls = s.score >= 70 ? 'hi' : s.score < 40 ? 'lo' : ''
      const letter = state.letters[id]
      const notes =
        (s.strengths || []).slice(0, 2).map((x) => `<li>${esc(x)}</li>`).join('') +
        (s.gaps || []).slice(0, 2).map((x) => `<li class="gap">${esc(x)}</li>`).join('')
      return `<article class="job">
        <div class="job-head">
          <div>
            <div class="job-title">${esc(job.title)}</div>
            <div class="job-meta">${esc(job.company)} · ${esc(job.location)}${job.salary ? ` · ${esc(job.salary)}` : ''}</div>
          </div>
          <div class="score ${cls}"><strong>${esc(s.score)}</strong><span>${esc(s.verdict)}</span></div>
        </div>
        ${notes ? `<ul class="notes">${notes}</ul>` : ''}
        <div class="job-links">
          <a href="${safeUrl(job.url)}" target="_blank" rel="noopener">Apply on ${esc(job.source)} ↗</a>
          ${letter ? '<details><summary>Cover letter</summary></details>' : ''}
        </div>
        ${letter ? `<div class="letter" hidden>${esc(letter)}</div>` : ''}
      </article>`
    })
    .join('')
  // The letter opens below the links, full width.
  for (const details of $('jobs').querySelectorAll('details')) {
    const letter = details.closest('.job').querySelector('.letter')
    details.ontoggle = () => (letter.hidden = !details.open)
  }
}

function renderPack(pack) {
  const box = $('pack')
  box.hidden = false
  const list = (items) => (items || []).map((x) => `<li>${esc(x)}</li>`).join('')
  box.innerHTML = `<p class="label muted">Application pack</p>
    <h3>${esc(pack.job)}, ${esc(pack.company)}</h3>
    <h4>CV summary</h4><p>${esc(pack.summary)}</p>
    <h4>CV bullets</h4><ul>${list(pack.bullets)}</ul>
    <h4>Interview questions</h4>
    <ol>${(pack.interview || []).map((q) => `<li><b>${esc(q.question)}</b><br /><span class="muted">${esc(q.how_to_answer)}</span></li>`).join('')}</ol>
    <h4>Keywords</h4><p class="muted">${esc((pack.keywords || []).join(', '))}</p>
    <h4>Cover letter</h4><div class="letter">${esc(pack.cover_letter)}</div>`
}

function renderReceipt() {
  if (!state.payments.length) return
  const box = $('receipt')
  box.hidden = false
  box.innerHTML = `<p class="label muted section-label">Receipt</p><table>
    ${state.payments
      .map((p) => `<tr><td>${esc(p.what)}<a class="tx" href="${safeUrl(p.explorer)}" target="_blank" rel="noopener">${esc(short(p.tx))} ↗</a></td><td>${esc(p.amount)}</td></tr>`)
      .join('')}
    <tr class="total"><td>Paid by the agent from its wallet</td><td>${state.spent.toFixed(2)} USDC</td></tr></table>`
}

load()
