# MediaWiki Export Path

This path is for archives that are too large or too graph-heavy for the Quartz build.

Scene Wiki can now:

1. scrape and normalize a publication archive
2. extract entities and issue references into the existing corpus JSON
3. write article-style MediaWiki pages for issues, entities, and categories
4. export a MediaWiki XML import dump
5. emit sidecar semantic-search assets under `static/` plus the search worker bundle

The generated payload is designed to work with a standard MediaWiki install plus a small sidecar search deployment for AlphaLoop-style semantic search.

## Generate an export

From an existing preserved run:

```bash
scene-wiki build-mediawiki-export \
  data/runs/<run-id> \
  --output-dir dist/mediawiki/<slug> \
  --site-title "My Wiki"
```

From a generic publication root:

```bash
scene-wiki build-publication-mediawiki \
  https://conversationswithtyler.com/episodes/ \
  --subject "Conversations with Tyler" \
  --site-title "Conversations with Tyler"
```

The export directory contains:

- `_worker.js`
- `mediawiki-import.xml`
- `mediawiki-export.json`
- `pages/` individual wikitext files for inspection
- `scene-search-*.json` search manifests and shards
- `static/` semantic-search frontend assets

## Deployment shape

The practical deployment is:

1. run a normal MediaWiki instance backed by MariaDB
2. import `mediawiki-import.xml`
3. serve the generated search sidecar under `/scene-search`

The search sidecar is the generated export output reduced to:

- `_worker.js`
- `scene-search-*.json`
- `static/scene-search-app.js`

That sidecar can live behind:

- a reverse-proxy path such as `/scene-search`
- a sibling Cloudflare Worker or Pages deployment
- another static host mounted under the same domain

The included Compose file only launches MediaWiki and MariaDB. It does not pretend to run the AlphaLoop search worker itself.

This repo includes a starter Compose stack in:

- [`docker-compose.yml`](/Users/ryanprendergast/Documents/Zenobia%20Pay/scene-wiki/examples/mediawiki/docker-compose.yml)
- [`LocalSettings.php.example`](/Users/ryanprendergast/Documents/Zenobia%20Pay/scene-wiki/examples/mediawiki/LocalSettings.php.example)
- [`import-dump.sh`](/Users/ryanprendergast/Documents/Zenobia%20Pay/scene-wiki/examples/mediawiki/import-dump.sh)

## Search integration

The semantic search remains a sidecar asset set rather than being jammed into native MediaWiki search.

That means:

- MediaWiki owns article rendering, categories, and large dataset ingestion
- Scene Wiki still owns the semantic retrieval layer
- the search app can be embedded from a gadget, extension, custom skin template, or a separate route proxied next to the wiki

The example `LocalSettings.php` assumes the sidecar is reachable at:

- `/scene-search/static/scene-search-app.js`
- `/scene-search/api/search`

The bundle expects:

- `scene-search-index.json`
- `scene-search-entities-*.json`
- `scene-search-issues-*.json`
- `scene-search-chunks-*.json`
- `static/scene-search-app.js`
- `_worker.js`

## Why this path exists

Quartz was failing because it wanted to materialize and build an in-memory graph over a very large Markdown tree. MediaWiki is a better fit for:

- large article counts
- incremental XML imports
- long-running imports that can resume
- article-centric browsing rather than giant static-site compilation
