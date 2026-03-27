const state = {
  config: null,
  pollTimer: null,
  activeJobId: null,
  turnstileToken: "",
}

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

function renderStatus(job) {
  statusSection.classList.remove("hidden")
  statusPanel.classList.remove("hidden")
  const siteLink = job.customDomain
    ? `https://${job.customDomain}`
    : job.pagesUrl
      ? job.pagesUrl
      : null
  const lines = [
    `<p><strong>${job.statusLabel}</strong> for <code>${job.siteSlug}</code></p>`,
    `<p class="status-meta">Job ${job.id} · queued ${formatDate(job.queueTime)} · updated ${formatDate(job.updatedAt)}</p>`,
  ]

  if (job.errorMessage) {
    lines.push(`<p>${job.errorMessage}</p>`)
  }

  if (siteLink) {
    lines.push(`<p><a href="${siteLink}" target="_blank" rel="noreferrer">Open site</a></p>`)
  }

  statusBody.innerHTML = lines.join("")
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
  const payload = await fetchJson(`/api/jobs/${jobId}`)
  renderStatus(payload.job)
  const terminalStates = new Set(["deployed", "failed"])
  if (terminalStates.has(payload.job.status)) {
    window.clearInterval(state.pollTimer)
    state.pollTimer = null
    await loadSites()
  }
}

function startPolling(jobId) {
  state.activeJobId = jobId
  if (state.pollTimer) {
    window.clearInterval(state.pollTimer)
  }
  state.pollTimer = window.setInterval(() => {
    loadJob(jobId).catch((error) => {
      statusBody.innerHTML = `<p>${error.message}</p>`
    })
  }, 5000)
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
