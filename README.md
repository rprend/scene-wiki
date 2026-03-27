# Scene Wiki

`scene-wiki` turns a Substack archive into a browsable Quartz wiki with semantic search.

## What It Does

Given a Substack publication URL, `scene-wiki` can:

1. scrape the archive and individual post pages
2. normalize the source text and save raw HTML
3. extract recurring entities and outbound links into a corpus JSON
4. generate an Obsidian-style vault and Quartz content tree
5. build a static wiki site with a right-rail archive assistant
6. optionally deploy the built site to Cloudflare Pages

## Requirements

- Python 3.10+
- Node.js 20+
- `npm`
- `claude` CLI authenticated locally for entity extraction
- `GOOGLE_API_KEY` or `GEMINI_API_KEY` for search embeddings
- `CLOUDFLARE_ACCOUNT_ID` for deploys

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

## Repo Layout

- `src/scene_wiki/` — Python pipeline and CLI
- `wiki/` — vendored Quartz app plus assistant sidebar customizations
- `scripts/` — incremental build and Cloudflare deploy helpers

## Current Constraints

- The extraction pass currently uses the local `claude` CLI for newsletter entity extraction.
- The semantic search index currently uses Gemini embeddings.
- The scraper is tuned for Substack archive pages and may need adjustment for heavily customized publications.
