const state = {
  config: null,
  pollTimer: null,
  activeJobId: null,
  turnstileToken: "",
}

const LAST_JOB_STORAGE_KEY = "sceneWiki:lastJobId"

const form = document.querySelector("#submit-form")
const input = document.querySelector("#source-url")
const button = document.querySelector("#submit-button")
const statusPanel = document.querySelector("#status-panel")
const statusSection = document.querySelector("#status-section")
const statusBody = document.querySelector("#status-body")
const siteList = document.querySelector("#site-list")
const collectionNote = document.querySelector("#collection-note")
const turnstileWrap = document.querySelector("#turnstile-wrap")
const turnstileWidget = document.querySelector("#turnstile-widget")

async function fetchJson(url, options = {}) {
  const response = await fetch(url, {
    headers: {
      Accept: "application/json",
      ...(options.headers || {}),
    },
    ...options,
  })

  const payload = await response.json().catch(() => ({}))
  if (!response.ok) {
    throw new Error(payload.error || `Request failed (${response.status})`)
  }
  return payload
}

function formatDate(value) {
  if (!value) return "Unknown"
  const date = new Date(value)
  return Number.isNaN(date.valueOf()) ? value : date.toLocaleString()
}

function formatDuration(seconds) {
  if (seconds == null || Number.isNaN(Number(seconds))) return null
  const total = Math.max(0, Math.round(Number(seconds)))
  const mins = Math.floor(total / 60)
  const secs = total % 60
  const hours = Math.floor(mins / 60)
  const remMins = mins % 60
  if (hours) return `${hours}h ${remMins}m`
  if (mins) return `${mins}m ${secs}s`
  return `${secs}s`
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;")
}

function titleFromSlug(slug) {
  return String(slug || "Scene Wiki")
    .split("-")
    .filter(Boolean)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ")
}

function summarizeEvents(events) {
  const hiddenMessagePatterns = [
    /^Runner log initialized\.$/,
    /^Runner failed\.$/,
    /^Syncing Pages secret /,
    /^Ensuring Cloudflare Pages project exists\.$/,
    /^Deploying site bundle to Cloudflare Pages\.$/,
    /^Attaching custom domain\.$/,
    /^Job completed and site marked deployed\.$/,
    /^Scene wiki build still running\.$/,
  ]
  const filteredEvents = events.filter((event) => !hiddenMessagePatterns.some((pattern) => pattern.test(event.message)))
  const dedupedEvents = []
  for (const event of filteredEvents) {
    const previous = dedupedEvents[dedupedEvents.length - 1]
    if (previous && previous.message === event.message) {
      dedupedEvents[dedupedEvents.length - 1] = event
      continue
    }
    dedupedEvents.push(event)
  }
  const latestWithProgress = dedupedEvents.reduce((best, event) => {
    const progress = event.payload?.overallProgressPct
    if (progress == null) return best
    if (!best) return event
    const bestProgress = best.payload?.overallProgressPct ?? -1
    if (progress > bestProgress) return event
    if (progress < bestProgress) return best
    return new Date(event.createdAt) >= new Date(best.createdAt) ? event : best
  }, null)
  const newestEvent = dedupedEvents[dedupedEvents.length - 1] || null
  const visibleEvents = dedupedEvents.slice(-8)
  return { latestWithProgress, newestEvent, visibleEvents }
}

function summarizeEventLine(event) {
  const parts = [event.message]
  if (event.payload?.analysisProgressPct != null) {
    parts.push(`${event.payload.analysisProgressPct}%`)
  }
  if (event.payload?.totalChunks != null && event.payload?.completedChunks != null) {
    parts.push(`${event.payload.completedChunks}/${event.payload.totalChunks} chunks`)
  }
  if (event.payload?.postsSaved != null) {
    parts.push(`${event.payload.postsSaved} posts`)
  }
  if (event.payload?.totalTextCharacters != null) {
    parts.push(`${event.payload.totalTextCharacters.toLocaleString()} chars`)
  }
  if (event.payload?.etaLabel) {
    parts.push(`ETA ${event.payload.etaLabel}`)
  }
  return parts.join(" · ")
}

function renderStatus(job, events = []) {
  statusSection.classList.remove("hidden")
  statusPanel.classList.remove("hidden")
  const siteLink = job.customDomain
    ? `https://${job.customDomain}`
    : job.pagesUrl
      ? job.pagesUrl
      : null
  const { latestWithProgress, newestEvent, visibleEvents } = summarizeEvents(events)
  const title = job.title || titleFromSlug(job.siteSlug)
  const headline = newestEvent ? summarizeEventLine(newestEvent) : `${job.statusLabel} · ${job.siteSlug}`
  const isTerminal = ["deployed", "failed"].includes(job.status)
  const spinnerClass = job.status === "deployed" ? "status-done" : job.status === "failed" ? "status-failed" : ""
  const lines = []

  if (latestWithProgress?.payload?.overallProgressPct != null) {
    const etaLabel = latestWithProgress.payload.etaLabel || formatDuration(latestWithProgress.payload.etaSeconds)
    const stage = latestWithProgress.payload.stage || "running"
    lines.push(`
      <div class="progress-shell">
        <div class="progress-meta">
          <span>${escapeHtml(latestWithProgress.message)}</span>
          <span>${latestWithProgress.payload.overallProgressPct}%</span>
        </div>
        <div class="progress-bar"><span style="width: ${latestWithProgress.payload.overallProgressPct}%"></span></div>
        <p class="status-meta">Stage: ${stage}${etaLabel ? ` · ETA ${etaLabel}` : ""}</p>
      </div>
    `)
  }

  if (job.errorMessage) {
    lines.push(`<p class="status-error">${escapeHtml(job.errorMessage)}</p>`)
  }

  if (visibleEvents.length) {
    const items = visibleEvents
      .map((event, index) => {
        const isCurrent = !isTerminal && index === visibleEvents.length - 1
        return `
          <li>
            <span class="status-event-dot${isCurrent ? " in-progress" : ""}"></span>
            <div class="status-event-row${isCurrent ? " in-progress" : ""}">
              <span class="status-event-copy">${escapeHtml(summarizeEventLine(event))}</span>
              <span class="status-event-meta">${escapeHtml(formatDate(event.createdAt))}</span>
            </div>
          </li>
        `
      })
      .join("")
    lines.push(`<div class="status-events"><ol>${items}</ol></div>`)
  } else {
    lines.push(`<p class="status-empty">No tool events yet.</p>`)
  }

  if (siteLink) {
    lines.push(`<a class="status-open-link" href="${siteLink}" target="_blank" rel="noreferrer">Open site</a>`)
  }

  statusBody.innerHTML = `
    <details class="status-shell" ${isTerminal ? "" : "open"}>
      <summary class="status-summary">
        <div class="status-summary-main">
          <span class="status-spinner ${spinnerClass}" aria-hidden="true"></span>
          <div class="status-summary-copy">
            <div class="status-title-row">
              <span class="status-title">${escapeHtml(title)}</span>
            </div>
            <div class="status-subtitle">${escapeHtml(headline)}</div>
          </div>
        </div>
        <div class="status-summary-side">
          <span class="status-stage-pill">${escapeHtml(job.statusLabel)}</span>
          <span class="status-caret" aria-hidden="true">▾</span>
        </div>
      </summary>
      <div class="status-details">
        <p class="status-meta-inline">Job ${escapeHtml(job.id)} · queued ${escapeHtml(formatDate(job.queueTime))} · updated ${escapeHtml(formatDate(job.updatedAt))}</p>
        ${lines.join("")}
      </div>
    </details>
  `
}

function persistActiveJob(jobId) {
  state.activeJobId = jobId

  if (jobId) {
    window.localStorage.setItem(LAST_JOB_STORAGE_KEY, jobId)
    const url = new URL(window.location.href)
    url.searchParams.set("job", jobId)
    window.history.replaceState({}, "", url)
    return
  }

  window.localStorage.removeItem(LAST_JOB_STORAGE_KEY)
  const url = new URL(window.location.href)
  url.searchParams.delete("job")
  window.history.replaceState({}, "", url)
}

function renderSites(sites) {
  if (!sites.length) {
    collectionNote.textContent = "No deployed scene wikis yet."
    siteList.innerHTML = `<div class="empty-state">The first generated wiki will appear here once a job completes.</div>`
    return
  }

  collectionNote.textContent = `${sites.length} deployed ${sites.length === 1 ? "wiki" : "wikis"}`
  siteList.innerHTML = sites
    .map((site) => {
      const href = site.customDomain ? `https://${site.customDomain}` : site.pagesUrl
      const safeHref = href || "#"
      return `
        <article class="site-card">
          <h3><a href="${safeHref}" ${href ? 'target="_blank" rel="noreferrer"' : ""}>${site.title}</a></h3>
          <p class="site-meta">${site.sourceUrl}</p>
          <div class="site-status">${site.statusLabel} · ${site.slug}.scenewiki.net</div>
          <div class="site-foot">
            <span class="site-meta">Updated ${formatDate(site.updatedAt)}</span>
            ${href ? `<a href="${safeHref}" target="_blank" rel="noreferrer">Visit wiki</a>` : ""}
          </div>
        </article>
      `
    })
    .join("")
}

async function loadConfig() {
  state.config = await fetchJson("/api/config")
  if (state.config.turnstileSiteKey && turnstileWidget) {
    turnstileWrap.classList.remove("hidden")
    const renderWidget = () => {
      if (!window.turnstile) return false
      window.turnstile.render(turnstileWidget, {
        sitekey: state.config.turnstileSiteKey,
        theme: "light",
        callback(token) {
          state.turnstileToken = token
        },
        "expired-callback"() {
          state.turnstileToken = ""
        },
      })
      return true
    }

    if (!renderWidget()) {
      const interval = window.setInterval(() => {
        if (renderWidget()) {
          window.clearInterval(interval)
        }
      }, 250)
    }
  }
}

async function loadSites() {
  const payload = await fetchJson("/api/sites")
  renderSites(payload.sites || [])
}

async function loadJob(jobId) {
  const [payload, eventsPayload] = await Promise.all([
    fetchJson(`/api/jobs/${jobId}`),
    fetchJson(`/api/jobs/${jobId}/events`).catch(() => ({ events: [] })),
  ])
  renderStatus(payload.job, eventsPayload.events || [])
  const terminalStates = new Set(["deployed", "failed"])
  if (terminalStates.has(payload.job.status)) {
    window.clearInterval(state.pollTimer)
    state.pollTimer = null
    await loadSites()
  }
  return payload.job
}

function startPolling(jobId) {
  persistActiveJob(jobId)
  if (state.pollTimer) {
    window.clearInterval(state.pollTimer)
  }
  state.pollTimer = window.setInterval(() => {
    loadJob(jobId).catch((error) => {
      statusBody.innerHTML = `<p>${error.message}</p>`
    })
  }, 5000)
}

async function loadInitialJob() {
  const url = new URL(window.location.href)
  const jobId = url.searchParams.get("job") || window.localStorage.getItem(LAST_JOB_STORAGE_KEY)
  if (!jobId) {
    return
  }

  try {
    const job = await loadJob(jobId)
    if (!["deployed", "failed"].includes(job.status)) {
      startPolling(jobId)
    } else {
      persistActiveJob(jobId)
    }
  } catch {
    persistActiveJob(null)
  }
}

form.addEventListener("submit", async (event) => {
  event.preventDefault()
  const sourceUrl = input.value.trim()
  if (!sourceUrl) return

  button.disabled = true
  button.textContent = "Queueing…"

  try {
    const payload = await fetchJson("/api/jobs", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ sourceUrl, turnstileToken: state.turnstileToken }),
    })
    renderStatus(payload.job)
    startPolling(payload.job.id)
    await loadSites()
  } catch (error) {
    statusSection.classList.remove("hidden")
    statusPanel.classList.remove("hidden")
    statusBody.innerHTML = `<p>${error.message}</p>`
  } finally {
    button.disabled = false
    button.textContent = "Create wiki"
  }
})

Promise.all([loadConfig(), loadSites()]).catch((error) => {
  collectionNote.textContent = error.message
})

loadInitialJob().catch((error) => {
  statusSection.classList.remove("hidden")
  statusPanel.classList.remove("hidden")
  statusBody.innerHTML = `<p>${error.message}</p>`
})
