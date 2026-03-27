# Scene Wiki

`scene-wiki` now has two layers:

1. the generator, which turns a Substack archive into a browsable Quartz wiki with semantic search
2. the platform app, which accepts public submissions, tracks jobs in D1, and publishes a collection of deployed scene wikis on Cloudflare

## What It Does

Given a Substack publication URL, `scene-wiki` can:

1. scrape the archive and individual post pages
2. normalize the source text and save raw HTML
3. extract recurring entities and outbound links into a corpus JSON
4. generate an Obsidian-style vault and Quartz content tree
5. build a static wiki site with a right-rail archive assistant
6. optionally deploy the built site to Cloudflare Pages

The platform layer adds:

1. a Quartz-adjacent landing page with a single submission form
2. a collection page showing all deployed scene wikis
3. D1-backed job and site metadata
4. runner-only API endpoints for claiming jobs, heartbeats, logs, success, and failure
5. a VM polling script that builds and deploys each wiki on its own Pages project and subdomain

## Requirements

- Python 3.10+
- Node.js 20+
- `npm`
- `claude` CLI authenticated locally for entity extraction
- `GOOGLE_API_KEY` or `GEMINI_API_KEY` for search embeddings
- `CLOUDFLARE_ACCOUNT_ID` for deploys
- `CLOUDFLARE_API_TOKEN` for Pages domain attachment in the platform runner

## Quickstart

Create a virtualenv and install the package:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
npm --prefix wiki ci
```

Build a wiki from a Substack archive:

```bash
scene-wiki build-substack \
  https://example.substack.com \
  --subject "Example Publication" \
  --site-title "Example Wiki"
```

That command will:

- create a run under `data/runs/`
- scrape posts from the archive
- build `artifacts/corpus.json`
- generate a vault under `vault/<slug>/`
- build a static site under `dist/wiki/`

## Environment

The generator reads `.env`, `.dev.vars`, and shell environment variables.

Relevant keys:

- `OPENAI_API_KEY`
- `GOOGLE_API_KEY`
- `GEMINI_API_KEY`
- `SUBSTACK_COOKIE`
- `SCENE_WIKI_TITLE`
- `SCENE_WIKI_ARCHIVE_LABEL`
- `QUARTZ_PAGE_TITLE`
- `QUARTZ_BASE_URL`
- `WIKI_CORPUS_FILENAME`
- `RUNNER_API_TOKEN`
- `MAIN_DOMAIN`
- `PLATFORM_BASE_URL`
- `SCENE_WIKI_MAX_ARTICLES`

If `SUBSTACK_COOKIE` is set, the scraper will send it with requests so paid or subscriber-only posts can resolve when your session has access.

## Shell Build / Deploy

For iterative rebuilds, copy the sample env file:

```bash
cp scripts/scene_wiki.env.example .scene-wiki.env
```

Then build and deploy with:

```bash
./scripts/build_wiki_site.sh
./scripts/deploy_wiki.sh
```

## Scene Wiki Platform

The Cloudflare app lives in:

- `platform/worker.mjs` — API router plus static asset host
- `platform/public/` — landing page and collection UI
- `platform/migrations/` — D1 schema
- `wrangler.toml` — Worker, assets, D1, and KV bindings
- `scripts/platform_runner.py` — VM polling runner

### Bootstrap Cloudflare resources

Copy the runner env template and fill in the values you want to use:

```bash
cp scripts/platform.env.example .env.platform
```

Then create the Worker resources and patch `wrangler.toml` with the generated IDs:

```bash
./scripts/bootstrap_platform.sh
```

That script:

- creates the D1 database if needed
- creates the KV namespace used for rate limiting
- writes the generated IDs into `wrangler.toml`
- applies the D1 migrations in `platform/migrations/`

### Deploy the platform

Once `wrangler.toml` has real binding IDs:

```bash
./scripts/deploy_platform.sh
```

The platform Worker serves:

- `GET /api/config`
- `POST /api/jobs`
- `GET /api/jobs/:id`
- `GET /api/jobs/:id/events`
- `GET /api/sites`
- `POST /api/runner/claim-next`
- `POST /api/runner/jobs/:id/event`
- `POST /api/runner/jobs/:id/heartbeat`
- `POST /api/runner/jobs/:id/complete`
- `POST /api/runner/jobs/:id/fail`

### Run the VM worker

On the VM that has `scene-wiki`, `claude`, Wrangler, and your generation secrets installed:

```bash
python3 scripts/platform_runner.py --env-file .env.platform
```

That loop will:

1. claim the next queued job from the Worker
2. run `scene-wiki build-substack`
3. create or reuse a Pages project for the slug
4. deploy the output bundle
5. attach `<slug>.<MAIN_DOMAIN>` as the custom domain, for example `example.scenewiki.net`
6. mark the job deployed or failed in D1

For debug runs, you can cap the build size:

```bash
scene-wiki build-substack \
  https://www.astralcodexten.com \
  --subject "Astral Codex Ten" \
  --site-title "Astral Codex Ten" \
  --max-articles 1
```

The platform runner also honors `SCENE_WIKI_MAX_ARTICLES`, and writes a per-job log file under `logs/jobs/<job-id>.log`.

### Current platform constraints

- public submissions accept Substack publication roots, including custom-domain Substacks that expose the standard archive API
- Turnstile verification is enforced only when `TURNSTILE_SECRET_KEY` is set; local/dev mode can opt into insecure submissions by setting `ALLOW_INSECURE_SUBMISSIONS=true`
- the collection app is intended to live at `scenewiki.net`
- generated wikis are intended to live at `<slug>.scenewiki.net`
- custom domain attachment assumes `scenewiki.net` is active on the same Cloudflare account

## Repo Layout

- `src/scene_wiki/` — Python pipeline and CLI
- `wiki/` — vendored Quartz app plus assistant sidebar customizations
- `scripts/` — incremental build and Cloudflare deploy helpers

## Current Constraints

- The extraction pass currently uses the local `claude` CLI for newsletter entity extraction.
- The semantic search index currently uses Gemini embeddings.
- The scraper is tuned for Substack archive pages and may need adjustment for heavily customized publications.
