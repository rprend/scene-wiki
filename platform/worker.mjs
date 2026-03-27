const JSON_HEADERS = {
  "content-type": "application/json; charset=utf-8",
  "cache-control": "no-store",
}

const PUBLIC_SITE_STATUSES = new Set(["deployed"])
const ACTIVE_JOB_STATUSES = new Set(["queued", "running"])

export default {
  async fetch(request, env, ctx) {
    try {
      const url = new URL(request.url)
      if (!url.pathname.startsWith("/api/")) {
        return env.ASSETS.fetch(request)
      }

      if (request.method === "GET" && url.pathname === "/api/config") {
        return jsonResponse({
          title: env.PLATFORM_TITLE,
          tagline: env.PLATFORM_TAGLINE,
          mainDomain: env.MAIN_DOMAIN,
          turnstileSiteKey: env.TURNSTILE_SITE_KEY || null,
        })
      }

      if (request.method === "GET" && url.pathname === "/api/sites") {
        return await handleListSites(env)
      }

      const siteMatch = url.pathname.match(/^\/api\/sites\/([^/]+)$/)
      if (request.method === "GET" && siteMatch) {
        return await handleGetSite(env, siteMatch[1])
      }

      if (request.method === "POST" && url.pathname === "/api/jobs") {
        return await handleCreateJob(request, env)
      }

      const jobMatch = url.pathname.match(/^\/api\/jobs\/([^/]+)$/)
      if (request.method === "GET" && jobMatch) {
        return await handleGetJob(env, jobMatch[1])
      }

      const jobEventsMatch = url.pathname.match(/^\/api\/jobs\/([^/]+)\/events$/)
      if (request.method === "GET" && jobEventsMatch) {
        return await handleGetJobEvents(env, jobEventsMatch[1])
      }

      if (url.pathname.startsWith("/api/runner/")) {
        requireRunnerToken(request, env)
        if (request.method === "POST" && url.pathname === "/api/runner/claim-next") {
          return await handleClaimNextJob(env)
        }

        const eventMatch = url.pathname.match(/^\/api\/runner\/jobs\/([^/]+)\/event$/)
        if (request.method === "POST" && eventMatch) {
          return await handleAppendEvent(request, env, eventMatch[1])
        }

        const heartbeatMatch = url.pathname.match(/^\/api\/runner\/jobs\/([^/]+)\/heartbeat$/)
        if (request.method === "POST" && heartbeatMatch) {
          return await handleHeartbeat(request, env, heartbeatMatch[1])
        }

        const completeMatch = url.pathname.match(/^\/api\/runner\/jobs\/([^/]+)\/complete$/)
        if (request.method === "POST" && completeMatch) {
          return await handleCompleteJob(request, env, completeMatch[1])
        }

        const failMatch = url.pathname.match(/^\/api\/runner\/jobs\/([^/]+)\/fail$/)
        if (request.method === "POST" && failMatch) {
          return await handleFailJob(request, env, failMatch[1])
        }
      }

      return jsonError("Not found", 404)
    } catch (error) {
      if (error instanceof HttpError) {
        return jsonError(error.message, error.status)
      }
      return jsonError(error instanceof Error ? error.message : "Unknown error", 500)
    }
  },
}

class HttpError extends Error {
  constructor(status, message) {
    super(message)
    this.status = status
  }
}

function jsonResponse(payload, init = {}) {
  return new Response(JSON.stringify(payload, null, 2), {
    ...init,
    headers: {
      ...JSON_HEADERS,
      ...(init.headers || {}),
    },
  })
}

function jsonError(message, status = 400) {
  return jsonResponse({ error: message }, { status })
}

function nowIso() {
  return new Date().toISOString()
}

function normalizeWhitespace(value) {
  return value.replace(/\s+/g, " ").trim()
}

function slugify(value) {
  return normalizeWhitespace(value)
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 48) || "scene-wiki"
}

async function urlLooksLive(url) {
  try {
    const response = await fetch(url, {
      method: "GET",
      redirect: "manual",
      headers: {
        accept: "text/html,application/xhtml+xml",
        "user-agent": "SceneWikiBot/1.0 (+https://scenewiki.net)",
      },
      cf: {
        cacheTtl: 0,
        cacheEverything: false,
      },
    })
    return response.status >= 200 && response.status < 500
  } catch {
    return false
  }
}

async function siteRecordLooksReachable(site) {
  if (site?.custom_domain) {
    return await urlLooksLive(`https://${site.custom_domain}`)
  }
  if (site?.pages_url) {
    if (await urlLooksLive(site.pages_url)) {
      return true
    }
  }
  return false
}

async function allocateSiteSlug(env, title, sourceUrl) {
  const baseSlug = slugify(title).slice(0, 40)
  const { results } = await env.SCENE_WIKI_DB.prepare(
    `SELECT slug
     FROM sites
     WHERE slug = ?
        OR slug GLOB ?`,
  )
    .bind(baseSlug, `${baseSlug}-*`)
    .all()

  const used = new Set(results.map((row) => row.slug))
  if (!used.has(baseSlug)) {
    return baseSlug
  }

  for (let suffix = 2; suffix < 1000; suffix += 1) {
    const candidate = `${baseSlug}-${suffix}`
    if (!used.has(candidate)) {
      return candidate
    }
  }

  return `${baseSlug}-999`
}

function deriveTitleFromHost(hostname) {
  const label = hostname.replace(/\.substack\.com$/, "").replace(/^www\./, "")
  return label
    .split(/[.-]/g)
    .filter(Boolean)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ")
}

async function detectSubstackSite(hostname) {
  const archiveUrl = `https://${hostname}/api/v1/archive?sort=new&search=&offset=0&limit=1`

  try {
    const archiveResponse = await fetch(archiveUrl, {
      headers: {
        accept: "application/json",
        "user-agent": "SceneWikiBot/1.0 (+https://scenewiki.net)",
      },
    })
    if (archiveResponse.ok) {
      const posts = await archiveResponse.json()
      if (Array.isArray(posts) && posts.length > 0) {
        const publication =
          posts[0]?.publishedBylines?.[0]?.publicationUsers?.find((entry) => entry?.is_primary)?.publication
          || posts[0]?.publishedBylines?.[0]?.publicationUsers?.[0]?.publication
        return {
          isSubstack: true,
          title: normalizeWhitespace(publication?.name || deriveTitleFromHost(hostname)),
        }
      }
    }
  } catch {}

  try {
    const response = await fetch(`https://${hostname}`, {
      headers: {
        accept: "text/html,application/xhtml+xml",
        "user-agent": "SceneWikiBot/1.0 (+https://scenewiki.net)",
      },
    })
    if (!response.ok) {
      return { isSubstack: false, title: null }
    }

    const servedBy = response.headers.get("x-served-by") || ""
    const cluster = response.headers.get("x-cluster") || ""
    if (/substack/i.test(servedBy) || /substack/i.test(cluster)) {
      return { isSubstack: true, title: deriveTitleFromHost(hostname) }
    }

    const html = await response.text()
    if (/substack/i.test(html) || /substackcdn\.com/i.test(html)) {
      const titleMatch = html.match(/<title>([^<]+)<\/title>/i)
      const title = titleMatch?.[1]?.replace(/\s*\|\s*Substack\s*$/i, "").trim() || deriveTitleFromHost(hostname)
      return { isSubstack: true, title }
    }
  } catch {}

  return { isSubstack: false, title: null }
}

async function resolveSourceUrl(rawUrl) {
  let parsed
  try {
    parsed = new URL(rawUrl)
  } catch {
    throw new HttpError(400, "Enter a valid URL.")
  }

  if (!["https:", "http:"].includes(parsed.protocol)) {
    throw new HttpError(400, "Only http and https URLs are supported.")
  }

  const hostname = parsed.hostname.toLowerCase()
  if (hostname.endsWith("substack.com")) {
    return {
      sourceUrl: `https://${hostname}`,
      sourceType: "substack",
      title: deriveTitleFromHost(hostname),
    }
  }

  const detection = await detectSubstackSite(hostname)
  if (!detection.isSubstack) {
    throw new HttpError(400, "v1 only supports Substack publication roots.")
  }

  return {
    sourceUrl: `https://${hostname}`,
    sourceType: "substack",
    title: detection.title || deriveTitleFromHost(hostname),
  }
}

async function parseJson(request) {
  const contentType = request.headers.get("content-type") || ""
  if (!contentType.includes("application/json")) {
    throw new HttpError(415, "Expected application/json.")
  }
  return await request.json()
}

async function getRateBucket(env, ip) {
  if (!env.SCENE_WIKI_RATE_LIMITS || !ip) {
    return { count: 0, key: null }
  }
  const bucket = new Date().toISOString().slice(0, 13)
  const key = `submit:${ip}:${bucket}`
  const raw = await env.SCENE_WIKI_RATE_LIMITS.get(key)
  return { count: Number.parseInt(raw || "0", 10) || 0, key }
}

async function incrementRateBucket(env, key, nextCount) {
  if (!key) return
  await env.SCENE_WIKI_RATE_LIMITS.put(key, String(nextCount), { expirationTtl: 3600 })
}

async function verifyTurnstile(env, token, ip) {
  if (!env.TURNSTILE_SECRET_KEY) {
    return env.ALLOW_INSECURE_SUBMISSIONS === "true"
  }
  if (!token) return false
  const body = new URLSearchParams({
    secret: env.TURNSTILE_SECRET_KEY,
    response: token,
  })
  if (ip) {
    body.set("remoteip", ip)
  }
  const response = await fetch("https://challenges.cloudflare.com/turnstile/v0/siteverify", {
    method: "POST",
    body,
  })
  if (!response.ok) return false
  const payload = await response.json()
  return Boolean(payload.success)
}

function requireRunnerToken(request, env) {
  const authHeaderName = env.RUNNER_POLL_TOKEN_HEADER || "authorization"
  const headerValue = request.headers.get(authHeaderName)
  const expected = env.RUNNER_API_TOKEN
  if (!expected || !headerValue) {
    throw new HttpError(401, "Runner authentication failed.")
  }
  const presented = headerValue.startsWith("Bearer ") ? headerValue.slice(7) : headerValue
  if (presented !== expected) {
    throw new HttpError(403, "Runner authentication failed.")
  }
}

function friendlyStatus(status) {
  switch (status) {
    case "queued":
      return "Queued"
    case "running":
      return "Generating"
    case "deployed":
      return "Live"
    case "failed":
      return "Failed"
    default:
      return status
  }
}

function mapJobRecord(row) {
  if (!row) return null
  return {
    id: row.id,
    siteSlug: row.site_slug,
    sourceUrl: row.source_url,
    sourceType: row.source_type,
    status: row.status,
    statusLabel: friendlyStatus(row.status),
    queueTime: row.queue_time,
    startedAt: row.started_at,
    heartbeatAt: row.heartbeat_at,
    finishedAt: row.finished_at,
    updatedAt: row.updated_at,
    errorMessage: row.error_message,
    runDir: row.run_dir,
    pagesProjectName: row.pages_project_name,
    pagesUrl: row.pages_url,
    customDomain: row.custom_domain,
  }
}

function mapSiteRecord(row) {
  return {
    slug: row.slug,
    title: row.title,
    sourceUrl: row.source_url,
    sourceType: row.source_type,
    status: row.status,
    statusLabel: friendlyStatus(row.status),
    pagesProjectName: row.pages_project_name,
    pagesUrl: row.pages_url,
    customDomain: row.custom_domain,
    updatedAt: row.updated_at,
  }
}

async function appendEvent(env, jobId, level, message, payload) {
  const createdAt = nowIso()
  await env.SCENE_WIKI_DB.prepare(
    `INSERT INTO job_events (job_id, level, message, payload_json, created_at)
     VALUES (?, ?, ?, ?, ?)`,
  )
    .bind(jobId, level, message, payload ? JSON.stringify(payload) : null, createdAt)
    .run()
}

async function handleListSites(env) {
  const { results } = await env.SCENE_WIKI_DB.prepare(
    `SELECT slug, title, source_url, source_type, status, pages_project_name, pages_url, custom_domain, updated_at
     FROM sites
     WHERE status = 'deployed'
     ORDER BY updated_at DESC`,
  ).all()

  return jsonResponse({ sites: results.map(mapSiteRecord) })
}

async function handleGetSite(env, slug) {
  const site = await env.SCENE_WIKI_DB.prepare(
    `SELECT slug, title, source_url, source_type, status, pages_project_name, pages_url, custom_domain, updated_at
     FROM sites
     WHERE slug = ?`,
  )
    .bind(slug)
    .first()

  if (!site || !PUBLIC_SITE_STATUSES.has(site.status)) {
    throw new HttpError(404, "Site not found.")
  }

  return jsonResponse({ site: mapSiteRecord(site) })
}

async function handleGetJob(env, jobId) {
  const job = await env.SCENE_WIKI_DB.prepare(
    `SELECT id, site_slug, source_url, source_type, status, queue_time, started_at, heartbeat_at,
            finished_at, updated_at, error_message, run_dir, pages_project_name, pages_url, custom_domain
     FROM jobs
     WHERE id = ?`,
  )
    .bind(jobId)
    .first()

  if (!job) {
    throw new HttpError(404, "Job not found.")
  }

  return jsonResponse({ job: mapJobRecord(job) })
}

async function handleGetJobEvents(env, jobId) {
  const job = await env.SCENE_WIKI_DB.prepare(`SELECT id FROM jobs WHERE id = ?`).bind(jobId).first()
  if (!job) {
    throw new HttpError(404, "Job not found.")
  }

  const { results } = await env.SCENE_WIKI_DB.prepare(
    `SELECT id, level, message, payload_json, created_at
     FROM job_events
     WHERE job_id = ?
     ORDER BY created_at ASC
     LIMIT 500`,
  )
    .bind(jobId)
    .all()

  return jsonResponse({
    events: results.map((row) => ({
      id: row.id,
      level: row.level,
      message: row.message,
      payload: row.payload_json ? JSON.parse(row.payload_json) : null,
      createdAt: row.created_at,
    })),
  })
}

async function handleCreateJob(request, env) {
  const body = await parseJson(request)
  const sourceUrlRaw = typeof body.sourceUrl === "string" ? body.sourceUrl : ""
  const turnstileToken = typeof body.turnstileToken === "string" ? body.turnstileToken : ""
  const ip = request.headers.get("cf-connecting-ip") || ""
  const { sourceUrl, sourceType, title } = await resolveSourceUrl(sourceUrlRaw)
  let slug = await allocateSiteSlug(env, title, sourceUrl)

  const rateBucket = await getRateBucket(env, ip)
  if (rateBucket.count >= 5) {
    throw new HttpError(429, "Submission limit reached for this IP. Try again in an hour.")
  }

  const verified = await verifyTurnstile(env, turnstileToken, ip)
  if (!verified) {
    throw new HttpError(400, "Turnstile verification failed.")
  }

  const existingSite = await env.SCENE_WIKI_DB.prepare(
    `SELECT slug, title, source_url, source_type, status, pages_project_name, pages_url, custom_domain, updated_at
     FROM sites WHERE source_url = ?
     ORDER BY updated_at DESC
     LIMIT 1`,
  )
    .bind(sourceUrl)
    .first()

  if (existingSite && PUBLIC_SITE_STATUSES.has(existingSite.status) && await siteRecordLooksReachable(existingSite)) {
    const existingJob = await env.SCENE_WIKI_DB.prepare(
      `SELECT id, site_slug, source_url, source_type, status, queue_time, started_at, heartbeat_at,
              finished_at, updated_at, error_message, run_dir, pages_project_name, pages_url, custom_domain
       FROM jobs
       WHERE site_slug = ?
       ORDER BY created_at DESC
       LIMIT 1`,
    )
      .bind(existingSite.slug)
      .first()

    if (existingJob) {
      return jsonResponse({ duplicate: true, job: mapJobRecord(existingJob), site: mapSiteRecord(existingSite) })
    }
  }

  if (existingSite && PUBLIC_SITE_STATUSES.has(existingSite.status) && !(await siteRecordLooksReachable(existingSite))) {
    await env.SCENE_WIKI_DB.prepare(
      `UPDATE sites
       SET status = 'failed', last_error_message = ?, updated_at = ?
       WHERE slug = ?`,
    )
      .bind("Saved deployment was not reachable. Retry queued.", nowIso(), existingSite.slug)
      .run()
  }

  if (existingSite && existingSite.slug !== slug) {
    slug = existingSite.slug
  }

  const existingActiveJob = await env.SCENE_WIKI_DB.prepare(
    `SELECT id, site_slug, source_url, source_type, status, queue_time, started_at, heartbeat_at,
            finished_at, updated_at, error_message, run_dir, pages_project_name, pages_url, custom_domain
     FROM jobs
     WHERE source_url = ? AND status IN ('queued', 'running')
     ORDER BY created_at DESC
     LIMIT 1`,
  )
    .bind(sourceUrl)
    .first()

  if (existingActiveJob) {
    return jsonResponse({ duplicate: true, job: mapJobRecord(existingActiveJob) })
  }

  const id = crypto.randomUUID()
  const timestamp = nowIso()
  await env.SCENE_WIKI_DB.batch([
    env.SCENE_WIKI_DB.prepare(
      `INSERT INTO sites (slug, title, source_url, source_type, status, latest_job_id, created_at, updated_at)
       VALUES (?, ?, ?, ?, 'queued', ?, ?, ?)
       ON CONFLICT(slug) DO UPDATE SET
         title = excluded.title,
         source_url = excluded.source_url,
         source_type = excluded.source_type,
         status = 'queued',
         latest_job_id = excluded.latest_job_id,
         updated_at = excluded.updated_at`,
    ).bind(slug, title, sourceUrl, sourceType, id, timestamp, timestamp),
    env.SCENE_WIKI_DB.prepare(
      `INSERT INTO jobs (
         id, site_slug, source_url, source_type, status, submitted_by, submitter_ip,
         queue_time, created_at, updated_at
       )
       VALUES (?, ?, ?, ?, 'queued', ?, ?, ?, ?, ?)`,
    ).bind(id, slug, sourceUrl, sourceType, "public", ip || null, timestamp, timestamp, timestamp),
  ])

  await appendEvent(env, id, "info", "Job queued from public submission.", { sourceUrl })
  await incrementRateBucket(env, rateBucket.key, rateBucket.count + 1)

  const job = await env.SCENE_WIKI_DB.prepare(
    `SELECT id, site_slug, source_url, source_type, status, queue_time, started_at, heartbeat_at,
            finished_at, updated_at, error_message, run_dir, pages_project_name, pages_url, custom_domain
     FROM jobs
     WHERE id = ?`,
  )
    .bind(id)
    .first()

  return jsonResponse({ job: mapJobRecord(job) }, { status: 201 })
}

async function handleClaimNextJob(env) {
  const queued = await env.SCENE_WIKI_DB.prepare(
    `SELECT id, site_slug, source_url, source_type
     FROM jobs
     WHERE status = 'queued'
     ORDER BY queue_time ASC
     LIMIT 1`,
  ).first()

  if (!queued) {
    return jsonResponse({ job: null })
  }

  const timestamp = nowIso()
  const updated = await env.SCENE_WIKI_DB.prepare(
    `UPDATE jobs
     SET status = 'running', started_at = COALESCE(started_at, ?), heartbeat_at = ?, updated_at = ?
     WHERE id = ? AND status = 'queued'`,
  )
    .bind(timestamp, timestamp, timestamp, queued.id)
    .run()

  if ((updated.meta?.changes || 0) === 0) {
    return jsonResponse({ job: null })
  }

  await env.SCENE_WIKI_DB.prepare(
    `UPDATE sites SET status = 'running', updated_at = ? WHERE slug = ?`,
  )
    .bind(timestamp, queued.site_slug)
    .run()
  await appendEvent(env, queued.id, "info", "Runner claimed queued job.", null)

  const fullJob = await env.SCENE_WIKI_DB.prepare(
    `SELECT j.id, j.site_slug, j.source_url, j.source_type, j.status, j.queue_time, j.started_at, j.heartbeat_at,
            j.finished_at, j.updated_at, j.error_message, j.run_dir, j.pages_project_name, j.pages_url, j.custom_domain,
            s.title
     FROM jobs j
     JOIN sites s ON s.slug = j.site_slug
     WHERE j.id = ?`,
  )
    .bind(queued.id)
    .first()

  return jsonResponse({
    job: {
      ...mapJobRecord(fullJob),
      title: fullJob.title,
      mainDomain: env.MAIN_DOMAIN,
      pagesProjectPrefix: env.PAGES_PROJECT_PREFIX || "scene-wiki",
    },
  })
}

async function handleAppendEvent(request, env, jobId) {
  const body = await parseJson(request)
  const level = typeof body.level === "string" ? body.level : "info"
  const message = typeof body.message === "string" ? body.message.trim() : ""
  if (!message) {
    throw new HttpError(400, "Event message is required.")
  }
  await appendEvent(env, jobId, level, message, body.payload || null)
  await env.SCENE_WIKI_DB.prepare(`UPDATE jobs SET updated_at = ? WHERE id = ?`).bind(nowIso(), jobId).run()
  return jsonResponse({ ok: true })
}

async function handleHeartbeat(request, env, jobId) {
  const body = await parseJson(request)
  const timestamp = nowIso()
  await env.SCENE_WIKI_DB.prepare(
    `UPDATE jobs SET heartbeat_at = ?, updated_at = ? WHERE id = ?`,
  )
    .bind(timestamp, timestamp, jobId)
    .run()
  if (typeof body.message === "string" && body.message.trim()) {
    await appendEvent(env, jobId, "info", body.message.trim(), null)
  }
  return jsonResponse({ ok: true })
}

async function handleCompleteJob(request, env, jobId) {
  const body = await parseJson(request)
  const timestamp = nowIso()
  const pagesProjectName = typeof body.pagesProjectName === "string" ? body.pagesProjectName : null
  const pagesUrl = typeof body.pagesUrl === "string" ? body.pagesUrl : null
  const customDomain = typeof body.customDomain === "string" ? body.customDomain : null
  const runDir = typeof body.runDir === "string" ? body.runDir : null
  const title = typeof body.title === "string" ? body.title.trim() : null
  const siteSlug = await env.SCENE_WIKI_DB.prepare(`SELECT site_slug FROM jobs WHERE id = ?`).bind(jobId).first()
  if (!siteSlug) {
    throw new HttpError(404, "Job not found.")
  }

  await env.SCENE_WIKI_DB.batch([
    env.SCENE_WIKI_DB.prepare(
      `UPDATE jobs
       SET status = 'deployed', finished_at = ?, updated_at = ?, pages_project_name = ?, pages_url = ?, custom_domain = ?, run_dir = ?, error_message = NULL
       WHERE id = ?`,
    ).bind(timestamp, timestamp, pagesProjectName, pagesUrl, customDomain, runDir, jobId),
    env.SCENE_WIKI_DB.prepare(
      `UPDATE sites
       SET title = COALESCE(?, title), status = 'deployed', pages_project_name = ?, pages_url = ?, custom_domain = ?, latest_job_id = ?, last_error_message = NULL, updated_at = ?
       WHERE slug = ?`,
    ).bind(title, pagesProjectName, pagesUrl, customDomain, jobId, timestamp, siteSlug.site_slug),
  ])
  await appendEvent(env, jobId, "info", "Scene wiki is live.", {
    pagesProjectName,
    pagesUrl,
    customDomain,
  })
  return jsonResponse({ ok: true })
}

async function handleFailJob(request, env, jobId) {
  const body = await parseJson(request)
  const timestamp = nowIso()
  const errorMessage = typeof body.errorMessage === "string" ? body.errorMessage : "Job failed."
  const runDir = typeof body.runDir === "string" ? body.runDir : null
  const siteSlug = await env.SCENE_WIKI_DB.prepare(`SELECT site_slug FROM jobs WHERE id = ?`).bind(jobId).first()
  if (!siteSlug) {
    throw new HttpError(404, "Job not found.")
  }

  await env.SCENE_WIKI_DB.batch([
    env.SCENE_WIKI_DB.prepare(
      `UPDATE jobs
       SET status = 'failed', finished_at = ?, updated_at = ?, error_message = ?, run_dir = ?
       WHERE id = ?`,
    ).bind(timestamp, timestamp, errorMessage, runDir, jobId),
    env.SCENE_WIKI_DB.prepare(
      `UPDATE sites
       SET status = 'failed', latest_job_id = ?, last_error_message = ?, updated_at = ?
       WHERE slug = ?`,
    ).bind(jobId, errorMessage, timestamp, siteSlug.site_slug),
  ])
  await appendEvent(env, jobId, "error", errorMessage, body.payload || null)
  return jsonResponse({ ok: true })
}
