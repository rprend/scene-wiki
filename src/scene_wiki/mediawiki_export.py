from __future__ import annotations

import json
import re
from collections import Counter
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

from .scene_search import build_scene_search_assets
from .scene_wiki import CATEGORY_DESCRIPTIONS
from .scene_wiki import CATEGORY_TITLES
from .scene_wiki import _entity_blurb
from .scene_wiki import _external_link_values
from .scene_wiki import _human_date
from .scene_wiki import build_doc_entities
from .scene_wiki import load_scene_corpus
from .site import bundle_frontend_assets


WIKITEXT_SEARCH_NOTE = """\
This wiki ships a sidecar semantic-search bundle generated from the primary-source archive.

* Search assets directory: '''static/'''
* Search manifest: '''static/scene-search-index.json'''
* Search frontend bundle: '''static/scene-search-app.js'''

If you want the AlphaLoop-style semantic search UI inside MediaWiki itself, load the frontend bundle from a gadget, extension, or reverse-proxied static path and point it at the generated search manifest.
"""


@dataclass
class MediaWikiPage:
    title: str
    text: str


def _sanitize_title(value: str) -> str:
    cleaned = re.sub(r"[\[\]{}<>|#]+", " ", value or "")
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned[:240] or "Untitled"


def _issue_title(doc_id: str, doc: dict[str, Any]) -> str:
    title = _sanitize_title(doc.get("title") or doc_id)
    published = _human_date(doc.get("published_at"))
    if published:
        return f"Issue/{published} - {title}"
    return f"Issue/{title}"


def _build_entity_titles(entities: list[dict[str, Any]]) -> dict[int, str]:
    counts = Counter(_sanitize_title(entity.get("name", "")) for entity in entities)
    titles: dict[int, str] = {}
    for idx, entity in enumerate(entities):
        base = _sanitize_title(entity.get("name", ""))
        if counts[base] > 1:
            category = CATEGORY_TITLES.get(entity.get("category", ""), entity.get("category", "").title())
            titles[idx] = f"{base} ({category})"
        else:
            titles[idx] = base
    return titles


def _wiki_link(target: str, label: str | None = None) -> str:
    if label and label != target:
        return f"[[{target}|{label}]]"
    return f"[[{target}]]"


def _category_tag(category: str) -> str:
    category_title = CATEGORY_TITLES.get(category, category.replace("_", " ").title())
    return f"[[Category:{category_title}]]"


def _render_main_page(
    *,
    site_title: str,
    docs: dict[str, dict[str, Any]],
    entities: list[dict[str, Any]],
    issue_titles: dict[str, str],
    entity_titles: dict[int, str],
) -> str:
    category_counts = Counter(entity.get("category", "unknown") for entity in entities)
    top_entities = sorted(
        range(len(entities)),
        key=lambda idx: (-int(entities[idx].get("mention_count", 0)), entity_titles[idx].lower()),
    )[:25]
    recent_issues = sorted(docs.items(), key=lambda item: item[1].get("published_at") or "", reverse=True)[:25]

    lines = [
        f"= {site_title} =",
        "",
        "This MediaWiki export is generated from the primary-source archive and the extracted scene graph.",
        "",
        "== Corpus summary ==",
        f"* Issues imported: {len(docs)}",
        f"* Entities imported: {len(entities)}",
        "",
        "== Entity categories ==",
    ]
    for category, count in sorted(category_counts.items(), key=lambda item: (-item[1], item[0])):
        category_title = CATEGORY_TITLES.get(category, category.replace("_", " ").title())
        lines.append(f"* [[Category:{category_title}|{category_title}]] ({count})")

    lines.extend(["", "== Most-mentioned entities =="])
    for entity_id in top_entities:
        entity = entities[entity_id]
        lines.append(
            f"* {_wiki_link(entity_titles[entity_id])} ({entity.get('mention_count', 0)} mentions, "
            f"{CATEGORY_TITLES.get(entity.get('category', ''), entity.get('category', '').title())})"
        )

    lines.extend(["", "== Recent issues =="])
    for doc_id, doc in recent_issues:
        lines.append(f"* {_wiki_link(issue_titles[doc_id], doc.get('title', doc_id))}")

    lines.extend(["", "== Semantic search ==", WIKITEXT_SEARCH_NOTE])
    return "\n".join(lines).strip() + "\n"


def _render_issue_page(
    *,
    doc_id: str,
    doc: dict[str, Any],
    entity_ids: list[int],
    entity_titles: dict[int, str],
    entities: list[dict[str, Any]],
) -> str:
    lines = [
        f"= {doc.get('title', doc_id)} =",
        "",
        "== Source ==",
    ]
    if doc.get("published_at"):
        lines.append(f"* Published: {_human_date(doc.get('published_at'))}")
    if doc.get("url"):
        lines.append(f"* Original URL: {doc['url']}")
    lines.extend(["", "== Referenced entities =="])
    if entity_ids:
        for entity_id in entity_ids:
            entity = entities[entity_id]
            lines.append(
                f"* {_wiki_link(entity_titles[entity_id], entity['name'])} "
                f"({CATEGORY_TITLES.get(entity.get('category', ''), entity.get('category', '').title())})"
            )
    else:
        lines.append("No entities were linked to this issue.")

    lines.extend(["", "== Primary text ==", doc.get("text", "").strip() or "No normalized text was available."])
    return "\n".join(lines).strip() + "\n"


def _render_entity_page(
    *,
    entity_id: int,
    entity: dict[str, Any],
    docs: dict[str, dict[str, Any]],
    issue_titles: dict[str, str],
    related_counts: dict[int, Counter[int]],
    entities: list[dict[str, Any]],
    entity_titles: dict[int, str],
) -> str:
    issue_ids = [doc_id for doc_id in entity.get("post_ids", []) if doc_id in docs]
    issue_ids.sort(key=lambda doc_id: docs[doc_id].get("published_at") or "", reverse=True)
    related = sorted(
        related_counts[entity_id].items(),
        key=lambda item: (-item[1], entity_titles[item[0]].lower()),
    )[:20]

    lines = [
        f"= {entity.get('name', entity_titles[entity_id])} =",
        "",
        _entity_blurb(entity, docs, related_counts, entities, entity_id),
        "",
        "== Corpus metadata ==",
        f"* Category: {CATEGORY_TITLES.get(entity.get('category', ''), entity.get('category', '').title())}",
        f"* Mention count: {entity.get('mention_count', 0)}",
        f"* Issue count: {len(issue_ids)}",
    ]
    if entity.get("first_seen"):
        lines.append(f"* First seen: {_human_date(entity.get('first_seen'))}")
    if entity.get("last_seen"):
        lines.append(f"* Last seen: {_human_date(entity.get('last_seen'))}")
    if entity.get("platform"):
        lines.append(f"* Platform: {entity['platform']}")
    if entity.get("handle"):
        lines.append(f"* Handle: @{entity['handle']}")
    if entity.get("work_type"):
        lines.append(f"* Work type: {entity['work_type']}")

    evidence = [item for item in entity.get("evidence", []) if item]
    lines.extend(["", "== Evidence =="])
    if evidence:
        for item in evidence[:10]:
            lines.append(f"* {item}")
    else:
        lines.append("No evidence snippets were retained.")

    lines.extend(["", "== Issues where this appears =="])
    if issue_ids:
        for doc_id in issue_ids:
            lines.append(f"* {_wiki_link(issue_titles[doc_id], docs[doc_id].get('title', doc_id))}")
    else:
        lines.append("No issue references were attached.")

    lines.extend(["", "== Related entities =="])
    if related:
        for related_id, count in related:
            lines.append(f"* {_wiki_link(entity_titles[related_id], entities[related_id]['name'])} ({count} shared issues)")
    else:
        lines.append("No related entities crossed the co-occurrence threshold.")

    external_links = _external_link_values(entity)
    lines.extend(["", "== External links =="])
    if external_links:
        for value in external_links:
            lines.append(f"* {value}")
    else:
        lines.append("No external links were retained.")

    lines.extend(["", _category_tag(entity.get("category", "unknown"))])
    return "\n".join(lines).strip() + "\n"


def _render_category_page(category: str, entity_ids: list[int], entities: list[dict[str, Any]], entity_titles: dict[int, str]) -> str:
    category_title = CATEGORY_TITLES.get(category, category.replace("_", " ").title())
    lines = [
        f"= {category_title} =",
        "",
        CATEGORY_DESCRIPTIONS.get(category, f"Entities categorized as {category_title.lower()}."),
        "",
        "== Articles ==",
    ]
    for entity_id in sorted(entity_ids, key=lambda idx: (-int(entities[idx].get("mention_count", 0)), entity_titles[idx].lower())):
        entity = entities[entity_id]
        lines.append(f"* {_wiki_link(entity_titles[entity_id], entity['name'])} ({entity.get('mention_count', 0)} mentions)")
    lines.extend(["", _category_tag(category)])
    return "\n".join(lines).strip() + "\n"


def _write_mediawiki_import_xml(output_path: Path, pages: list[MediaWikiPage]) -> None:
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    parts = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<mediawiki xmlns="http://www.mediawiki.org/xml/export-0.11/" version="0.11" xml:lang="en">',
        "  <siteinfo>",
        "    <sitename>Scene Wiki</sitename>",
        "    <dbname>scenewiki</dbname>",
        "    <base>https://example.org/wiki/Main_Page</base>",
        "    <generator>scene-wiki</generator>",
        "    <case>first-letter</case>",
        "  </siteinfo>",
    ]
    for page in pages:
        parts.extend(
            [
                "  <page>",
                f"    <title>{escape(page.title)}</title>",
                "    <revision>",
                f"      <timestamp>{timestamp}</timestamp>",
                "      <contributor><username>Scene Wiki</username></contributor>",
                "      <comment>Generated by scene-wiki MediaWiki exporter</comment>",
                f'      <text xml:space="preserve">{escape(page.text)}</text>',
                "    </revision>",
                "  </page>",
            ]
        )
    parts.append("</mediawiki>")
    output_path.write_text("\n".join(parts) + "\n", encoding="utf-8")


def build_mediawiki_export(
    *,
    run_dir: Path,
    output_dir: Path,
    site_title: str | None = None,
    build_search: bool = True,
    wiki_dir: Path | None = None,
) -> dict[str, Any]:
    run_dir = run_dir.resolve()
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    docs, entities = load_scene_corpus(run_dir)
    doc_entities = build_doc_entities(docs, entities)
    entity_titles = _build_entity_titles(entities)
    issue_titles = {doc_id: _issue_title(doc_id, doc) for doc_id, doc in docs.items()}
    related_counts: dict[int, Counter[int]] = defaultdict(Counter)
    for entity_ids in doc_entities.values():
        unique_ids = list(dict.fromkeys(entity_ids))
        for source_id in unique_ids:
            for target_id in unique_ids:
                if source_id != target_id:
                    related_counts[source_id][target_id] += 1

    pages: list[MediaWikiPage] = []
    category_buckets: dict[str, list[int]] = defaultdict(list)

    resolved_site_title = site_title or json.loads((run_dir / "metadata.json").read_text(encoding="utf-8")).get("subject") or "Scene Wiki"
    pages.append(
        MediaWikiPage(
            title="Main Page",
            text=_render_main_page(
                site_title=resolved_site_title,
                docs=docs,
                entities=entities,
                issue_titles=issue_titles,
                entity_titles=entity_titles,
            ),
        )
    )
    pages.append(MediaWikiPage(title="Semantic search", text=WIKITEXT_SEARCH_NOTE + "\n"))

    for doc_id, doc in sorted(docs.items(), key=lambda item: item[1].get("published_at") or ""):
        pages.append(
            MediaWikiPage(
                title=issue_titles[doc_id],
                text=_render_issue_page(
                    doc_id=doc_id,
                    doc=doc,
                    entity_ids=doc_entities.get(doc_id, []),
                    entity_titles=entity_titles,
                    entities=entities,
                ),
            )
        )

    for entity_id, entity in enumerate(entities):
        category = entity.get("category", "unknown")
        category_buckets[category].append(entity_id)
        pages.append(
            MediaWikiPage(
                title=entity_titles[entity_id],
                text=_render_entity_page(
                    entity_id=entity_id,
                    entity=entity,
                    docs=docs,
                    issue_titles=issue_titles,
                    related_counts=related_counts,
                    entities=entities,
                    entity_titles=entity_titles,
                ),
            )
        )

    for category, entity_ids in sorted(category_buckets.items()):
        pages.append(
            MediaWikiPage(
                title=f"Category:{CATEGORY_TITLES.get(category, category.replace('_', ' ').title())}",
                text=_render_category_page(category, entity_ids, entities, entity_titles),
            )
        )

    pages_dir = output_dir / "pages"
    pages_dir.mkdir(parents=True, exist_ok=True)
    for page in pages:
        page_path = pages_dir / f"{re.sub(r'[^A-Za-z0-9._-]+', '_', page.title)[:180]}.wikitext"
        page_path.write_text(page.text, encoding="utf-8")

    import_xml_path = output_dir / "mediawiki-import.xml"
    _write_mediawiki_import_xml(import_xml_path, pages)

    search_dir = output_dir / "static"
    if build_search:
        build_scene_search_assets(run_dir=run_dir, output_dir=search_dir)
        if wiki_dir is not None:
            bundle_frontend_assets(wiki_dir=wiki_dir.resolve(), output_dir=output_dir)

    manifest = {
        "site_title": resolved_site_title,
        "page_count": len(pages),
        "entity_count": len(entities),
        "issue_count": len(docs),
        "import_xml": str(import_xml_path),
        "search_dir": str(search_dir) if build_search else None,
    }
    (output_dir / "mediawiki-export.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest
