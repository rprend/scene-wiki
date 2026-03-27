from __future__ import annotations

import json
import os
import re
import shutil
from html import unescape
from collections import Counter
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from .storage import read_json


CATEGORY_TITLES = {
    "venue": "Venues",
    "instagram_account": "Instagram Accounts",
    "publication": "Publications",
    "organization": "Organizations",
    "person": "People",
    "book": "Books",
    "film": "Films",
    "music": "Music",
    "place": "Places",
    "event": "Events",
    "concept": "Concepts",
    "brand": "Brands",
}

CATEGORY_SINGULAR = {
    "venue": "venue",
    "instagram_account": "Instagram account",
    "publication": "publication",
    "organization": "organization",
    "person": "person",
    "book": "book",
    "film": "film",
    "music": "music project",
    "place": "place",
    "event": "event",
    "concept": "concept",
    "brand": "brand",
}

CATEGORY_DESCRIPTIONS = {
    "venue": "Restaurants, galleries, cinemas, clubs, bars, and other places where this scene physically happens.",
    "instagram_account": "Accounts that organize, signal, or circulate information across the scene.",
    "publication": "Substacks, magazines, zines, journals, and publications referenced in the archive.",
    "organization": "Groups, collectives, magazines, venues, and institutions operating inside the scene.",
    "person": "Writers, artists, hosts, DJs, filmmakers, and recurring characters across the archive.",
    "book": "Books, collections, and literary works mentioned in the writing.",
    "film": "Films, screenings, and moving-image works that recur in the archive.",
    "music": "Bands, songs, albums, and musical projects referenced in the archive.",
    "place": "Cities, neighborhoods, regions, and other geographic anchors.",
    "event": "Named parties, readings, programs, and event series.",
    "concept": "Ideas, aesthetics, movements, and abstractions named in the archive.",
    "brand": "Brands and products that show up often enough to matter to the scene map.",
}

CATEGORY_INLINE_ENTRY_LIMIT = 1000

SEARCH_PAGE_BODY = [
    "# Semantic Search",
    "",
    "Use Gemini embeddings plus AlphaLoop to search the primary-source archive. The search starts from raw newsletter text, then expands into linked entity pages, issue pages, and bounded backlinks so the answer can reason over the scene graph instead of only a single excerpt.",
    "",
    '<div class="semantic-search-app" data-scene-search-app>',
    "  <p>Loading search…</p>",
    "</div>",
    "",
    '<script type="module" src="/static/scene-search-app.js"></script>',
]

CATEGORY_INDEX_SCRIPT = [
    "<script>",
    "(function () {",
    "  const entries = Array.from(document.querySelectorAll('[data-category-entry]'));",
    "  if (!entries.length) return;",
    "",
    "  function setOpen(entry, open) {",
    "    entry.open = open;",
    "    entry.dataset.open = open ? 'true' : 'false';",
    "    const toggle = entry.querySelector('[data-category-toggle]');",
    "    if (toggle) toggle.setAttribute('aria-expanded', open ? 'true' : 'false');",
    "  }",
    "",
    "  entries.forEach((entry) => setOpen(entry, false));",
    "",
    "  document.addEventListener('toggle', (event) => {",
    "    const entry = event.target.closest?.('[data-category-entry]');",
    "    if (!entry) return;",
    "    setOpen(entry, entry.open);",
    "  }, true);",
    "",
    "  document.addEventListener('click', (event) => {",
    "    const toggle = event.target.closest('[data-category-toggle]');",
    "    if (toggle) return;",
    "",
    "    const rail = event.target.closest('[data-category-rail]');",
    "    if (rail) {",
    "      event.preventDefault();",
    "      const entry = rail.closest('[data-category-entry]');",
    "      if (entry) setOpen(entry, false);",
    "      return;",
    "    }",
    "",
    "    const summary = event.target.closest('.category-entry-head');",
    "    if (!summary) return;",
    "    const entry = summary.closest('[data-category-entry]');",
    "    if (!entry) return;",
    "    const link = event.target.closest('a');",
    "    if (link) {",
    "      event.preventDefault();",
    "      window.location.assign(link.href);",
    "      return;",
    "    }",
    "    event.preventDefault();",
    "  });",
    "",
    "  const expandAll = document.querySelector('[data-category-expand-all]');",
    "  const collapseAll = document.querySelector('[data-category-collapse-all]');",
    "  if (expandAll) expandAll.addEventListener('click', () => entries.forEach((entry) => setOpen(entry, true)));",
    "  if (collapseAll) collapseAll.addEventListener('click', () => entries.forEach((entry) => setOpen(entry, false)));",
    "})();",
    "</script>",
]


def _display_category_title(category: str) -> str:
    return CATEGORY_TITLES.get(category, category.replace("_", " ").title())


def _display_category_singular(category: str) -> str:
    return CATEGORY_SINGULAR.get(category, category.replace("_", " "))


def _display_category_description(category: str) -> str:
    return CATEGORY_DESCRIPTIONS.get(category, f"Entities classified as { _display_category_singular(category) } within this archive.")


def _corpus_filename() -> str:
    return os.environ.get("WIKI_CORPUS_FILENAME", "corpus.json")


def _archive_label(run_dir: Path) -> str:
    metadata_path = run_dir / "metadata.json"
    if metadata_path.exists():
        metadata = read_json(metadata_path)
        subject = str(metadata.get("subject") or "").strip()
        if subject:
            return os.environ.get("SCENE_WIKI_ARCHIVE_LABEL", subject)
    return os.environ.get("SCENE_WIKI_ARCHIVE_LABEL", "Archive")


def _site_title(run_dir: Path) -> str:
    return os.environ.get("SCENE_WIKI_TITLE", f"{_archive_label(run_dir)} Wiki")


@dataclass
class Note:
    path: str
    title: str
    frontmatter: dict[str, Any]
    body: list[str]
    links_to: set[str]


def _slugify(value: str) -> str:
    text = value.strip().lower()
    text = re.sub(r"[’'`]+", "", text)
    text = re.sub(r"[^a-z0-9]+", "-", text)
    return text.strip("-") or "item"


def _category_shard_label(name: str) -> str:
    stripped = name.strip()
    if not stripped:
        return "other"
    first = stripped[0].upper()
    if first.isalpha():
        return first.lower()
    if first.isdigit():
        return "0-9"
    return "other"


def build_entity_note_paths(entities: list[dict[str, Any]]) -> dict[int, str]:
    entity_note_paths: dict[int, str] = {}
    seen_entity_slugs: dict[tuple[str, str], int] = defaultdict(int)
    for idx, entity in enumerate(entities):
        category = entity["category"]
        base_slug = _slugify(entity["name"])
        key = (category, base_slug)
        seen_entity_slugs[key] += 1
        if seen_entity_slugs[key] == 1:
            entity_slug = base_slug
        else:
            entity_slug = f"{base_slug}-{seen_entity_slugs[key]}"
        entity_note_paths[idx] = f"entities/{category}/{entity_slug}"
    return entity_note_paths


def build_doc_entities(
    docs: dict[str, dict[str, Any]], entities: list[dict[str, Any]]
) -> dict[str, list[int]]:
    doc_entities: dict[str, list[int]] = defaultdict(list)
    for idx, entity in enumerate(entities):
        for doc_id in entity.get("post_ids", []):
            if doc_id in docs:
                doc_entities[doc_id].append(idx)

    for doc_id in list(doc_entities.keys()):
        doc_entities[doc_id] = sorted(
            set(doc_entities[doc_id]),
            key=lambda item: (
                entities[item]["category"],
                -int(entities[item].get("mention_count", 0)),
                entities[item]["name"].lower(),
            ),
        )
    return doc_entities


def load_scene_corpus(run_dir: Path) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    normalized_dir = run_dir / "normalized"
    corpus_filename = _corpus_filename()
    corpus_path = run_dir / "artifacts" / corpus_filename

    if not normalized_dir.exists():
        raise SystemExit(f"Missing normalized directory: {normalized_dir}")
    if not corpus_path.exists():
        raise SystemExit(f"Missing corpus file: {corpus_path}")

    docs = {}
    for doc_path in sorted(normalized_dir.glob("*.json")):
        doc = read_json(doc_path)
        docs[doc["doc_id"]] = doc

    corpus = read_json(corpus_path)
    entities = corpus.get("entities", [])
    return docs, entities


def _quote(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def _frontmatter(data: dict[str, Any]) -> str:
    lines = ["---"]
    for key, value in data.items():
        if value is None:
            continue
        lines.append(f"{key}: {_quote(value)}")
    lines.append("---")
    return "\n".join(lines)


def _human_date(iso_value: str | None) -> str | None:
    if not iso_value:
        return None
    try:
        dt = datetime.fromisoformat(iso_value.replace("Z", "+00:00"))
    except ValueError:
        return iso_value
    return dt.strftime("%B %d, %Y")


def _short_date(iso_value: str | None) -> str | None:
    if not iso_value:
        return None
    try:
        dt = datetime.fromisoformat(iso_value.replace("Z", "+00:00"))
    except ValueError:
        return iso_value
    return dt.strftime("%Y-%m-%d")


def _description(text: str, limit: int = 170) -> str:
    clean = " ".join(text.split())
    if len(clean) <= limit:
        return clean
    clipped = clean[:limit].rsplit(" ", 1)[0].strip()
    return f"{clipped}..."


def _abstract(text: str, sentence_limit: int = 3) -> str:
    clean = " ".join(text.split())
    if not clean:
        return ""
    parts = re.split(r"(?<=[.!?])\s+", clean)
    kept: list[str] = []
    for part in parts:
        candidate = part.strip()
        if len(candidate) < 35:
            continue
        kept.append(candidate)
        if len(kept) >= sentence_limit:
            break
    if not kept:
        return clean[:240].strip()
    summary = " ".join(kept)
    return summary[:420].strip()


def _wikilink(path: str, label: str | None = None) -> str:
    if label and label != Path(path).name:
        return f"[[{path}|{label}]]"
    return f"[[{path}]]"


def _internal_href(path: str) -> str:
    return f"/{path.lstrip('/')}"


def _escape_html(value: str) -> str:
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _html_link(path: str, label: str) -> str:
    return f'<a href="{_escape_html(_internal_href(path))}">{_escape_html(label)}</a>'


def _sorted_links(paths: set[str], notes: dict[str, Note]) -> list[str]:
    return sorted(paths, key=lambda item: notes[item].title.lower())


def _render_backlinks(path: str, backlinks: dict[str, set[str]], notes: dict[str, Note]) -> list[str]:
    lines = ["## Backlinks", ""]
    incoming = _sorted_links(backlinks.get(path, set()), notes)
    if not incoming:
        lines.append("No other generated pages link here yet.")
        return lines
    for linked_path in incoming:
        lines.append(f"- {_wikilink(linked_path, notes[linked_path].title)}")
    return lines


def _render_links(title: str, values: list[str]) -> list[str]:
    lines = [f"## {title}", ""]
    if not values:
        lines.append("None.")
        return lines
    lines.extend(f"- {value}" for value in values)
    return lines


def _copy_tree(src: Path, dst: Path) -> None:
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst)


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    output: list[str] = []
    for value in values:
        key = value.strip()
        if not key or key in seen:
            continue
        seen.add(key)
        output.append(key)
    return output


def _clean_text(text: str) -> str:
    return " ".join((text or "").split())


def _entity_search_terms(entity: dict[str, Any]) -> list[str]:
    raw_terms = [entity.get("name", "")]
    raw_terms.extend(entity.get("aliases", []))
    if entity.get("instagram_handle"):
        raw_terms.append(entity["instagram_handle"])
        raw_terms.append(f"@{entity['instagram_handle']}")
    terms = []
    for term in raw_terms:
        clean = term.strip()
        if not clean:
            continue
        if len(clean) < 4 and " " not in clean and not clean.startswith("@"):
            continue
        terms.append(clean)
    return _dedupe(sorted(terms, key=lambda item: (-len(item), item.lower())))


def _excerpt_around(text: str, start: int, end: int, window: int = 170) -> str:
    left = max(0, start - window)
    right = min(len(text), end + window)
    snippet = text[left:right].strip()
    if left > 0:
        snippet = f"...{snippet}"
    if right < len(text):
        snippet = f"{snippet}..."
    return snippet


def _find_text_snippets(text: str, patterns: list[str], limit: int = 4) -> list[str]:
    clean = _clean_text(text)
    snippets: list[str] = []
    seen: set[str] = set()
    for pattern in patterns:
        escaped = re.escape(_clean_text(pattern))
        if not escaped:
            continue
        for match in re.finditer(escaped, clean, flags=re.IGNORECASE):
            snippet = _excerpt_around(clean, match.start(), match.end())
            if snippet in seen:
                continue
            seen.add(snippet)
            snippets.append(snippet)
            if len(snippets) >= limit:
                return snippets
    return snippets


def _raw_html_path(run_dir: Path, doc_id: str) -> Path:
    return run_dir / "raw" / "html" / f"{doc_id}.html"


def _strip_html(fragment: str) -> str:
    text = re.sub(r"<br\s*/?>", " ", fragment, flags=re.I)
    text = re.sub(r"</(p|li|div|ul|ol|h[1-6])>", " ", text, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    return _clean_text(unescape(text))


def _extract_anchor_links(fragment: str) -> list[dict[str, str]]:
    links: list[dict[str, str]] = []
    seen: set[str] = set()
    for match in re.finditer(r'<a [^>]*href="([^"]+)"[^>]*>(.*?)</a>', fragment, flags=re.I | re.S):
        url = unescape(match.group(1)).strip()
        label = _strip_html(match.group(2)).strip() or url
        if not url or url in seen:
            continue
        seen.add(url)
        links.append({"url": url, "label": label})
    return links


def _extract_raw_context_blocks(run_dir: Path, doc_id: str, patterns: list[str], limit: int = 3) -> list[dict[str, Any]]:
    html_path = _raw_html_path(run_dir, doc_id)
    if not html_path.exists():
        return []
    html = html_path.read_text(encoding="utf-8")
    blocks: list[dict[str, Any]] = []
    seen_text: set[str] = set()
    boundaries = [
        (r"<li[^>]*>", r"</li>"),
        (r"<p[^>]*>", r"</p>"),
    ]
    for pattern in patterns:
        escaped = re.escape(pattern.strip())
        if not escaped:
            continue
        for match in re.finditer(escaped, html, flags=re.I):
            fragment = None
            for open_pat, close_pat in boundaries:
                open_matches = list(re.finditer(open_pat, html[: match.start()], flags=re.I))
                close_match = re.search(close_pat, html[match.end() :], flags=re.I)
                if open_matches and close_match:
                    start = open_matches[-1].start()
                    end = match.end() + close_match.end()
                    fragment = html[start:end]
                    break
            if fragment is None:
                continue
            text = _strip_html(fragment)
            if not text or text in seen_text:
                continue
            seen_text.add(text)
            blocks.append(
                {
                    "text": text,
                    "links": _extract_anchor_links(fragment),
                }
            )
            if len(blocks) >= limit:
                return blocks
    return blocks


def _source_ref_lines(title: str, issue_path: str, doc: dict[str, Any], snippets: list[str]) -> list[str]:
    lines = [f"### {_wikilink(issue_path, title)}", ""]
    metadata = []
    if doc.get("published_at"):
        metadata.append(_human_date(doc["published_at"]))
    if doc.get("url"):
        metadata.append(f"[Original source]({doc['url']})")
    if metadata:
        lines.append(" · ".join(metadata))
        lines.append("")
    if snippets:
        lines.extend(f"- {snippet}" for snippet in snippets)
    else:
        lines.append("No direct inline mention snippet was recovered from the normalized text, but this issue is part of the entity's source trail.")
    lines.append("")
    return lines


def _render_source_record_html(record: dict[str, Any]) -> list[str]:
    lines = [
        '<div class="source-reference">',
        f'<div class="source-reference-header">{_html_link(record["issue_path"], record["issue_title"])}</div>',
    ]
    meta: list[str] = []
    if record.get("published_at"):
        meta.append(_escape_html(_human_date(record["published_at"]) or ""))
    if record.get("source_url"):
        meta.append(f'<a href="{_escape_html(record["source_url"])}" target="_blank" rel="noreferrer">Original source</a>')
    if meta:
        lines.append(f'<div class="source-reference-meta">{" · ".join(part for part in meta if part)}</div>')

    blocks = record.get("raw_blocks") or []
    if blocks:
        for block in blocks:
            lines.append(f'<blockquote class="source-reference-quote">{_escape_html(block["text"])}</blockquote>')
            if block.get("links"):
                link_html = ", ".join(
                    f'<a href="{_escape_html(link["url"])}" target="_blank" rel="noreferrer">{_escape_html(link["label"])}</a>'
                    for link in block["links"]
                )
                lines.append(f'<div class="source-reference-links">Inline links: {link_html}</div>')
    elif record.get("snippets"):
        for snippet in record["snippets"]:
            lines.append(f'<blockquote class="source-reference-quote">{_escape_html(snippet)}</blockquote>')
    else:
        lines.append('<div class="source-reference-empty">No direct inline source block was recovered for this mention.</div>')

    lines.append("</div>")
    return lines


def _source_ref_record(
    doc_id: str,
    issue_path: str,
    doc: dict[str, Any],
    snippets: list[str],
    raw_blocks: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "doc_id": doc_id,
        "issue_path": issue_path,
        "issue_title": doc.get("title", doc_id),
        "published_at": doc.get("published_at"),
        "source_url": doc.get("url"),
        "snippets": snippets,
        "raw_blocks": raw_blocks or [],
    }


def _raw_text_lines(text: str, words_per_paragraph: int = 100) -> list[str]:
    words = _clean_text(text).split()
    if not words:
        return ["No source text captured."]
    paragraphs = [
        " ".join(words[index : index + words_per_paragraph])
        for index in range(0, len(words), words_per_paragraph)
    ]
    return paragraphs


def _entity_blurb(
    entity: dict[str, Any],
    docs: dict[str, dict[str, Any]],
    related_counts: dict[int, Counter[int]],
    entities: list[dict[str, Any]],
    entity_id: int,
) -> str:
    title = entity["name"]
    category = entity["category"]
    mention_count = int(entity.get("mention_count", 0))
    post_ids = [doc_id for doc_id in entity.get("post_ids", []) if doc_id in docs]
    issue_count = len(post_ids)
    first_seen = _human_date(entity.get("first_seen")) or "an unknown date"
    last_seen = _human_date(entity.get("last_seen")) or "an unknown date"

    first_sentence = (
        f"{title} is a recurring {_display_category_singular(category)} in the {os.environ.get('SCENE_WIKI_ARCHIVE_LABEL', 'archive')} archive, "
        f"appearing {mention_count} times across {issue_count} issues between {first_seen} and {last_seen}."
    )

    evidence = [snippet.strip(" .") for snippet in entity.get("evidence", [])[:3] if snippet.strip()]
    if evidence:
        second_sentence = "The archive places it in contexts such as " + "; ".join(
            f'"{snippet}"' for snippet in evidence
        ) + "."
    else:
        second_sentence = (
            f"It belongs to the {_display_category_title(category)} layer of the scene map and is linked from the issues below."
        )

    related = sorted(
        related_counts[entity_id].items(),
        key=lambda item: (-item[1], entities[item[0]]["name"].lower()),
    )[:3]
    if related:
        third_sentence = "It most often appears alongside " + ", ".join(
            entities[related_id]["name"] for related_id, _ in related
        ) + "."
    else:
        third_sentence = "Its page links directly to every issue where the archive references it."

    return " ".join([first_sentence, second_sentence, third_sentence])


def _external_link_values(entity: dict[str, Any]) -> list[str]:
    links: list[str] = []
    if entity.get("instagram_handle"):
        links.append(f"Instagram: https://instagram.com/{entity['instagram_handle']}")
    for url in entity.get("urls", [])[:12]:
        links.append(url)
    return _dedupe(links)


def _render_external_link_lines(entity: dict[str, Any]) -> list[str]:
    return [f"- {value}" for value in _external_link_values(entity)]


def _extract_book_author(entity: dict[str, Any]) -> str | None:
    patterns = [
        entity.get("name", ""),
        *entity.get("evidence", [])[:8],
        *entity.get("urls", [])[:8],
    ]
    regexes = [
        re.compile(r"\bby ([A-Z][A-Za-z'’-]+(?: [A-Z][A-Za-z'’-]+){0,4})"),
        re.compile(r"\bfrom ([A-Z][A-Za-z'’-]+(?: [A-Z][A-Za-z'’-]+){0,4})"),
    ]
    for candidate in patterns:
        text = candidate.replace("-", " ")
        for regex in regexes:
            match = regex.search(text)
            if match:
                author = match.group(1).strip(" .,:;-)(")
                if author and author.lower() != entity.get("name", "").lower():
                    return author
    return None


def _render_category_entity_details(
    run_dir: Path,
    entity: dict[str, Any],
    entity_id: int,
    entity_path: str,
    entity_note_paths: dict[int, str],
    docs: dict[str, dict[str, Any]],
    issue_paths: dict[str, str],
    related_counts: dict[int, Counter[int]],
    entities: list[dict[str, Any]],
    links_to: set[str],
) -> list[str]:
    issue_ids = [doc_id for doc_id in entity.get("post_ids", []) if doc_id in docs]
    issue_ids.sort(key=lambda item: docs[item].get("published_at") or "")
    issue_count = len(issue_ids)
    mention_count = int(entity.get("mention_count", 0))
    source_records: list[dict[str, Any]] = []
    for doc_id in issue_ids:
        snippets = _find_text_snippets(
            docs[doc_id].get("text", ""),
            _entity_search_terms(entity) + entity.get("evidence", [])[:3],
            limit=4,
        )
        raw_blocks = _extract_raw_context_blocks(
            run_dir,
            doc_id,
            _entity_search_terms(entity) + entity.get("evidence", [])[:3],
            limit=3,
        )
        source_records.append(
            _source_ref_record(doc_id, issue_paths[doc_id], docs[doc_id], snippets, raw_blocks=raw_blocks)
        )
        links_to.add(issue_paths[doc_id])

    author = _extract_book_author(entity) if entity.get("category") == "book" else None
    related = sorted(
        related_counts[entity_id].items(),
        key=lambda item: (-item[1], entities[item[0]]["name"].lower()),
    )[:5]
    external_links = _external_link_values(entity)

    lines = [
        f'<details class="category-entry" data-category-entry data-open="false">',
        (
            '<summary class="category-entry-head">'
            f'<span class="category-entry-caret" role="button" tabindex="0" data-category-toggle aria-expanded="false" aria-label="Expand {_escape_html(entity["name"])}"></span>'
            f'<span class="category-entry-heading"><span class="category-entry-heading-main">{_html_link(entity_path, entity["name"])}</span>'
            f'<span class="category-entry-meta">{mention_count} mentions across {issue_count} issues</span></span>'
            "</summary>"
        ),
        '<div class="category-entry-body">',
        '<button class="category-entry-rail" type="button" data-category-rail aria-label="Collapse dossier"></button>',
        '<div class="category-entry-content">',
        f'<p class="category-entry-blurb">{_escape_html(_entity_blurb(entity, docs, related_counts, entities, entity_id))}</p>',
        '<dl class="category-facts">',
        f'<div><dt>Article page</dt><dd>{_html_link(entity_path, entity["name"])}</dd></div>',
        f'<div><dt>Mention count</dt><dd>{mention_count}</dd></div>',
        f'<div><dt>Issue count</dt><dd>{issue_count}</dd></div>',
        f'<div><dt>First seen</dt><dd>{_escape_html(_human_date(entity.get("first_seen")) or "Unknown")}</dd></div>',
        f'<div><dt>Last seen</dt><dd>{_escape_html(_human_date(entity.get("last_seen")) or "Unknown")}</dd></div>',
    ]
    if entity.get("category") == "book":
        lines.append(f'<div><dt>Book title</dt><dd>{_escape_html(entity["name"])}</dd></div>')
    if entity.get("instagram_handle"):
        lines.append(f'<div><dt>Instagram handle</dt><dd>@{_escape_html(entity["instagram_handle"])}</dd></div>')
    if author:
        lines.append(f'<div><dt>Likely author</dt><dd>{_escape_html(author)}</dd></div>')
    lines.append("</dl>")

    if external_links:
        lines += ['<div class="category-entry-section"><div class="category-entry-label">Outbound links</div><ul>']
        for value in external_links:
            if value.startswith("Instagram: "):
                url = value.split(": ", 1)[1]
                lines.append(f'<li><a href="{_escape_html(url)}" target="_blank" rel="noreferrer">Instagram</a></li>')
            else:
                lines.append(
                    f'<li><a href="{_escape_html(value)}" target="_blank" rel="noreferrer">{_escape_html(value)}</a></li>'
                )
        lines.append("</ul></div>")

    if issue_ids:
        lines += ['<div class="category-entry-section"><div class="category-entry-label">Issue trail</div><ul>']
        for doc_id in issue_ids:
            doc = docs[doc_id]
            lines.append(
                f'<li>{_html_link(issue_paths[doc_id], doc.get("title", doc_id))} <span class="category-inline-meta">{_escape_html(_human_date(doc.get("published_at")) or "Unknown date")}</span></li>'
            )
        lines.append("</ul></div>")

    if related:
        lines += ['<div class="category-entry-section"><div class="category-entry-label">Related pages</div><ul>']
        for related_id, count in related:
            related_entity = entities[related_id]
            related_path = entity_note_paths[related_id]
            links_to.add(related_path)
            lines.append(
                f'<li>{_html_link(related_path, related_entity["name"])} <span class="category-inline-meta">{count} shared issues</span></li>'
            )
        lines.append("</ul></div>")

    lines.append('<div class="category-entry-section"><div class="category-entry-label">Source context</div>')
    has_quotes = False
    for record in source_records:
        if record["snippets"] or record.get("raw_blocks"):
            has_quotes = True
        lines += _render_source_record_html(record)
    if not has_quotes and not source_records:
        lines.append('<div class="source-reference-empty">No source references were recovered for this entry.</div>')

    lines.append("</div></div></details>")
    return lines


def build_obsidian_vault(run_dir: Path, vault_dir: Path) -> dict[str, Any]:
    run_dir = run_dir.resolve()
    vault_dir = vault_dir.resolve()
    docs, entities = load_scene_corpus(run_dir)
    archive_label = _archive_label(run_dir)
    site_title = _site_title(run_dir)
    archive_slug = _slugify(archive_label)

    entity_note_paths = build_entity_note_paths(entities)
    doc_entities = build_doc_entities(docs, entities)
    category_order = list(CATEGORY_TITLES.keys())
    discovered_categories = sorted({entity["category"] for entity in entities if entity.get("category")})
    effective_categories = category_order + [category for category in discovered_categories if category not in CATEGORY_TITLES]

    related_counts: dict[int, Counter[int]] = defaultdict(Counter)
    for entity_ids in doc_entities.values():
        for i, source_id in enumerate(entity_ids):
            for target_id in entity_ids[i + 1 :]:
                related_counts[source_id][target_id] += 1
                related_counts[target_id][source_id] += 1

    notes: dict[str, Note] = {}

    category_paths = {category: f"categories/{category}" for category in effective_categories}
    index_paths = {
        "timeline": "indexes/timeline",
        "top-mentioned": "indexes/top-mentioned",
        "semantic-search": "indexes/semantic-search",
    }
    issue_paths = {doc_id: f"issues/{doc_id}" for doc_id in docs}

    issue_count = len(docs)
    entity_count = len(entities)
    category_counts = Counter(entity["category"] for entity in entities)

    home_links = set(category_paths.values()) | set(index_paths.values()) | set(issue_paths.values())
    home_body = [
        f"# {site_title}",
        "",
        f"An Obsidian-shaped wiki generated from the {archive_label} archive. This site maps the people, venues, publications, events, places, and references that recur across the source text.",
        "",
        "## Search the Archive",
        "",
        f"- {_wikilink(index_paths['semantic-search'], 'Open semantic search')} (Gemini embeddings + AlphaLoop over the original source text)",
        "",
        "## Atlas Snapshot",
        "",
        f"- {issue_count} issues",
        f"- {entity_count} extracted entities",
        "",
        "## Browse by Category",
        "",
    ]
    for category in effective_categories:
        title = _display_category_title(category)
        home_body.append(
            f"- {_wikilink(category_paths[category], title)} ({category_counts.get(category, 0)} pages)"
        )
    home_body += [
        "",
        "## Browse the Archive",
        "",
        f"- {_wikilink(index_paths['timeline'], 'Timeline of issues')}",
        f"- {_wikilink(index_paths['top-mentioned'], 'Most mentioned entities')}",
        f"- {_wikilink(index_paths['semantic-search'], 'Semantic search')}",
        "",
        "## Entry Points",
        "",
    ]
    top_entities = sorted(
        range(len(entities)),
        key=lambda idx: (-int(entities[idx].get("mention_count", 0)), entities[idx]["name"].lower()),
    )[:18]
    for entity_id in top_entities:
        entity = entities[entity_id]
        home_body.append(
            f"- {_wikilink(entity_note_paths[entity_id], entity['name'])} ({_display_category_title(entity['category'])})"
        )
        home_links.add(entity_note_paths[entity_id])
    notes["Home"] = Note(
        path="Home",
        title=site_title,
        frontmatter={
            "title": site_title,
            "kind": "home",
            "tags": ["scene-wiki", "home"],
            "aliases": ["Scene Wiki", site_title],
            "description": f"A generated wiki of the {archive_label} archive, published as an Obsidian-style site.",
        },
        body=home_body,
        links_to=home_links,
    )

    for doc_id, doc in sorted(docs.items(), key=lambda item: item[1].get("published_at") or ""):
        issue_path = issue_paths[doc_id]
        title = doc.get("title", doc_id)
        published_at = doc.get("published_at")
        entity_ids = doc_entities.get(doc_id, [])
        links_to = {category_paths[entities[idx]["category"]] for idx in entity_ids if entities[idx]["category"] in category_paths}
        grouped: dict[str, list[int]] = defaultdict(list)
        for entity_id in entity_ids:
            grouped[entities[entity_id]["category"]].append(entity_id)
            links_to.add(entity_note_paths[entity_id])

        body = [
            f"# {title}",
            "",
            _abstract(doc.get("text", "")),
            "",
            "## Metadata",
            "",
        ]
        if published_at:
            body.append(f"- Published: {_human_date(published_at)}")
        if doc.get("url"):
            body.append(f"- Source: [{doc['url']}]({doc['url']})")
        body.append(f"- Document ID: `{doc_id}`")
        body.append("")
        body.append("## Category Map")
        body.append("")
        for category in sorted(grouped.keys()):
            cat_title = _display_category_title(category)
            body.append(f"### {cat_title}")
            body.append("")
            for entity_id in grouped[category]:
                entity = entities[entity_id]
                body.append(
                    f"- {_wikilink(entity_note_paths[entity_id], entity['name'])} ({entity.get('mention_count', 0)} mentions)"
                )
            body.append("")
        body += [
            "## Full Primary Source Text",
            "",
            *_raw_text_lines(doc.get("text", "")),
        ]

        notes[issue_path] = Note(
            path=issue_path,
            title=title,
            frontmatter={
                "title": title,
                "kind": "issue",
                "doc_id": doc_id,
                "source_url": doc.get("url"),
                "published_at": published_at,
                "date": _short_date(published_at),
                "tags": ["scene-wiki", "issue", archive_slug, str((published_at or "")[:4])],
                "aliases": [doc_id],
                "description": _description(_abstract(doc.get("text", ""))),
            },
            body=body,
            links_to=links_to,
        )

    entities_by_category: dict[str, list[int]] = defaultdict(list)
    for idx, entity in enumerate(entities):
        entities_by_category[entity["category"]].append(idx)

    for category in effective_categories:
        title = _display_category_title(category)
        entity_ids = sorted(
            entities_by_category.get(category, []),
            key=lambda item: (-int(entities[item].get("mention_count", 0)), entities[item]["name"].lower()),
        )
        links_to = set(index_paths.values())

        if len(entity_ids) > CATEGORY_INLINE_ENTRY_LIMIT:
            shard_groups: dict[str, list[int]] = defaultdict(list)
            for entity_id in entity_ids:
                shard_groups[_category_shard_label(entities[entity_id]["name"])].append(entity_id)

            body = [
                f"# {title}",
                "",
                _display_category_description(category),
                "",
                "## Reference Sections",
                "",
                f"This category is split into alphabetical sections because the full inline dossier would exceed the site builder's limits at {len(entity_ids)} pages.",
                "",
            ]
            for shard_label in sorted(shard_groups.keys(), key=lambda value: ("other", "0-9").index(value) if value in {"other", "0-9"} else ord(value[0])):
                shard_title = shard_label.upper() if len(shard_label) == 1 else shard_label
                shard_path = f"{category_paths[category]}/{shard_label}"
                body.append(
                    f"- {_wikilink(shard_path, f'{title}: {shard_title}')} ({len(shard_groups[shard_label])} pages)"
                )
                links_to.add(shard_path)
            body.append("")

            notes[category_paths[category]] = Note(
                path=category_paths[category],
                title=title,
                frontmatter={
                    "title": title,
                    "kind": "category",
                    "category": category,
                    "tags": ["scene-wiki", "category", category],
                    "aliases": [category.replace("_", " ").title()],
                    "description": _display_category_description(category),
                },
                body=body,
                links_to=links_to,
            )

            for shard_label, shard_entity_ids in shard_groups.items():
                shard_title = shard_label.upper() if len(shard_label) == 1 else shard_label
                shard_path = f"{category_paths[category]}/{shard_label}"
                shard_links = set(index_paths.values()) | {category_paths[category]}
                shard_body = [
                    f"# {title}: {shard_title}",
                    "",
                    f"{_display_category_description(category)} This section collects the `{shard_title}` slice of the category index.",
                    "",
                    f"- Back to {_wikilink(category_paths[category], title)}",
                    "",
                    "## Reference Index",
                    "",
                    "Use the title to open the standalone article. Use the caret to expand a compact inline dossier with source context, issue trail, related pages, and outbound links.",
                    "",
                    '<div class="category-index-toolbar">',
                    '<button type="button" class="category-index-button" data-category-expand-all>Expand all</button>',
                    '<button type="button" class="category-index-button" data-category-collapse-all>Collapse all</button>',
                    "</div>",
                    "",
                ]
                for entity_id in shard_entity_ids:
                    entity = entities[entity_id]
                    shard_body += _render_category_entity_details(
                        run_dir=run_dir,
                        entity=entity,
                        entity_id=entity_id,
                        entity_path=entity_note_paths[entity_id],
                        entity_note_paths=entity_note_paths,
                        docs=docs,
                        issue_paths=issue_paths,
                        related_counts=related_counts,
                        entities=entities,
                        links_to=shard_links,
                    )
                    shard_body.append("")
                    shard_links.add(entity_note_paths[entity_id])
                shard_body += ["", *CATEGORY_INDEX_SCRIPT]
                notes[shard_path] = Note(
                    path=shard_path,
                    title=f"{title}: {shard_title}",
                    frontmatter={
                        "title": f"{title}: {shard_title}",
                        "kind": "category",
                        "category": category,
                        "tags": ["scene-wiki", "category", category],
                        "aliases": [f"{category.replace('_', ' ').title()} {shard_title}"],
                        "description": f"{title} section {shard_title}",
                    },
                    body=shard_body,
                    links_to=shard_links,
                )
            continue

        body = [
            f"# {title}",
            "",
            _display_category_description(category),
            "",
            "## Reference Index",
            "",
            "Use the title to open the standalone article. Use the caret to expand a compact inline dossier with source context, issue trail, related pages, and outbound links.",
            "",
            '<div class="category-index-toolbar">',
            '<button type="button" class="category-index-button" data-category-expand-all>Expand all</button>',
            '<button type="button" class="category-index-button" data-category-collapse-all>Collapse all</button>',
            "</div>",
            "",
        ]
        for entity_id in entity_ids:
            entity = entities[entity_id]
            body += _render_category_entity_details(
                run_dir=run_dir,
                entity=entity,
                entity_id=entity_id,
                entity_path=entity_note_paths[entity_id],
                entity_note_paths=entity_note_paths,
                docs=docs,
                issue_paths=issue_paths,
                related_counts=related_counts,
                entities=entities,
                links_to=links_to,
            )
            body.append("")
            links_to.add(entity_note_paths[entity_id])
        body += ["", *CATEGORY_INDEX_SCRIPT]
        notes[category_paths[category]] = Note(
            path=category_paths[category],
            title=title,
            frontmatter={
                "title": title,
                "kind": "category",
                "category": category,
                "tags": ["scene-wiki", "category", category],
                "aliases": [category.replace("_", " ").title()],
                "description": _display_category_description(category),
            },
            body=body,
            links_to=links_to,
        )

    public_entity_index: dict[str, dict[str, Any]] = {}
    public_issue_index: dict[str, dict[str, Any]] = {}
    entity_source_refs: dict[str, list[dict[str, Any]]] = {}

    for idx, entity in enumerate(entities):
        path = entity_note_paths[idx]
        category = entity["category"]
        title = entity["name"]
        links_to = {category_paths[category]}
        issue_links = []
        source_ref_blocks: list[str] = []
        source_ref_records: list[dict[str, Any]] = []

        for doc_id in sorted(entity.get("post_ids", []), key=lambda item: docs[item].get("published_at") or ""):
            if doc_id not in docs:
                continue
            issue_links.append(_wikilink(issue_paths[doc_id], docs[doc_id].get("title", doc_id)))
            links_to.add(issue_paths[doc_id])
            snippets = _find_text_snippets(
                docs[doc_id].get("text", ""),
                _entity_search_terms(entity) + entity.get("evidence", [])[:3],
                limit=4,
            )
            raw_blocks = _extract_raw_context_blocks(
                run_dir,
                doc_id,
                _entity_search_terms(entity) + entity.get("evidence", [])[:3],
                limit=3,
            )
            source_ref_blocks.extend(
                _render_source_record_html(
                    _source_ref_record(
                        doc_id,
                        issue_paths[doc_id],
                        docs[doc_id],
                        snippets,
                        raw_blocks=raw_blocks,
                    )
                )
            )
            source_ref_records.append(
                _source_ref_record(doc_id, issue_paths[doc_id], docs[doc_id], snippets, raw_blocks=raw_blocks)
            )

        related_lines = []
        for related_id, count in sorted(
            related_counts[idx].items(),
            key=lambda item: (-item[1], entities[item[0]]["name"].lower()),
        )[:12]:
            related_entity = entities[related_id]
            related_lines.append(
                f"- {_wikilink(entity_note_paths[related_id], related_entity['name'])} ({count} shared issues)"
            )
            links_to.add(entity_note_paths[related_id])

        external_links = _render_external_link_lines(entity)

        issue_count = len([doc_id for doc_id in entity.get("post_ids", []) if doc_id in docs])

        body = [
            f"# {title}",
            "",
            "## Article",
            "",
            _entity_blurb(entity, docs, related_counts, entities, idx),
            "",
            "## Metadata",
            "",
            f"- Category: {_wikilink(category_paths[category], _display_category_title(category))}",
            f"- Mention count: {entity.get('mention_count', 0)}",
            f"- Issue count: {issue_count}",
            f"- First seen: {_human_date(entity.get('first_seen')) or 'Unknown'}",
            f"- Last seen: {_human_date(entity.get('last_seen')) or 'Unknown'}",
            "",
        ]
        body += _render_links("Appears In", issue_links)
        body.append("")
        body += _render_links("Related Pages", related_lines)
        body.append("")
        body += _render_links("External Links", external_links)
        body.append("")
        body += [
            "## Source Context",
            "",
            "Recovered passages from the original issue text. When the raw archive preserved outbound links inside the source passage, they are listed directly under the quote.",
            "",
        ]
        body += source_ref_blocks or ['<div class="source-reference-empty">No primary source references were reconstructed for this page.</div>']

        source_urls = entity.get("urls", [])
        notes[path] = Note(
            path=path,
            title=title,
            frontmatter={
                "title": title,
                "kind": "entity",
                "category": category,
                "entity_name": title,
                "mention_count": entity.get("mention_count", 0),
                "first_seen": entity.get("first_seen"),
                "last_seen": entity.get("last_seen"),
                "source_url": source_urls[0] if source_urls else None,
                "tags": ["scene-wiki", "entity", category],
                "aliases": [title, *entity.get("aliases", [])[:6]],
                "description": _description(_entity_blurb(entity, docs, related_counts, entities, idx)),
            },
            body=body,
            links_to=links_to,
        )

        public_entity_index[path] = {
            "title": title,
            "category": category,
            "category_title": _display_category_title(category),
            "mention_count": int(entity.get("mention_count", 0)),
            "issue_count": issue_count,
        }
        entity_source_refs[path] = source_ref_records

    timeline_links = []
    for doc_id, doc in sorted(docs.items(), key=lambda item: item[1].get("published_at") or ""):
        timeline_links.append(
            f"- {_wikilink(issue_paths[doc_id], doc.get('title', doc_id))} ({_human_date(doc.get('published_at'))})"
        )
        public_issue_index[issue_paths[doc_id]] = {
            "title": doc.get("title", doc_id),
            "published_at": doc.get("published_at"),
            "published_at_human": _human_date(doc.get("published_at")),
            "url": doc.get("url"),
        }
    notes[index_paths["timeline"]] = Note(
        path=index_paths["timeline"],
        title="Timeline of Issues",
        frontmatter={
            "title": "Timeline of Issues",
            "kind": "index",
            "tags": ["scene-wiki", "index", "timeline"],
            "aliases": ["Issue Timeline"],
            "description": f"All {archive_label} issues in publication order.",
        },
        body=["# Timeline of Issues", "", *timeline_links],
        links_to=set(issue_paths.values()),
    )

    top_lines = []
    top_links = set(category_paths.values())
    for entity_id in top_entities:
        entity = entities[entity_id]
        top_lines.append(
            f"- {_wikilink(entity_note_paths[entity_id], entity['name'])} ({entity.get('mention_count', 0)} mentions, {_display_category_title(entity['category'])})"
        )
        top_links.add(entity_note_paths[entity_id])
    notes[index_paths["top-mentioned"]] = Note(
        path=index_paths["top-mentioned"],
        title="Most Mentioned Entities",
        frontmatter={
            "title": "Most Mentioned Entities",
            "kind": "index",
            "tags": ["scene-wiki", "index", "top-mentioned"],
            "aliases": ["Top Mentioned"],
            "description": f"The most frequently recurring pages in the {archive_label} scene graph.",
        },
        body=["# Most Mentioned Entities", "", *top_lines],
        links_to=top_links,
    )

    notes[index_paths["semantic-search"]] = Note(
        path=index_paths["semantic-search"],
        title="Semantic Search",
        frontmatter={
            "title": "Semantic Search",
            "kind": "index",
            "tags": ["scene-wiki", "index", "semantic-search"],
            "aliases": ["Archive Search", "Embedding Search"],
            "description": f"Embeddings-backed search across the original {archive_label} source text.",
        },
        body=SEARCH_PAGE_BODY,
        links_to=set(),
    )

    backlinks: dict[str, set[str]] = defaultdict(set)
    for note in notes.values():
        for target in note.links_to:
            if target in notes:
                backlinks[target].add(note.path)

    if vault_dir.exists():
        shutil.rmtree(vault_dir)
    vault_dir.mkdir(parents=True, exist_ok=True)

    for note in notes.values():
        note_path = vault_dir / f"{note.path}.md"
        note_path.parent.mkdir(parents=True, exist_ok=True)
        full_body = list(note.body)
        full_body.extend(["", *_render_backlinks(note.path, backlinks, notes)])
        note_path.write_text(
            _frontmatter(note.frontmatter) + "\n\n" + "\n".join(full_body).rstrip() + "\n",
            encoding="utf-8",
        )

    meta_dir = vault_dir / "_meta"
    meta_dir.mkdir(parents=True, exist_ok=True)
    (meta_dir / "entity-index.json").write_text(
        json.dumps(public_entity_index, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    (meta_dir / "issue-index.json").write_text(
        json.dumps(public_issue_index, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    (meta_dir / "backlink-map.json").write_text(
        json.dumps({key: sorted(value) for key, value in backlinks.items()}, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    (meta_dir / "related-entity-map.json").write_text(
        json.dumps(
            {
                entity_note_paths[idx]: [
                    {"path": entity_note_paths[related_id], "shared_issues": count}
                    for related_id, count in sorted(
                        related_counts[idx].items(),
                        key=lambda item: (-item[1], entities[item[0]]["name"].lower()),
                    )[:20]
                ]
                for idx in range(len(entities))
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (meta_dir / "entity-source-map.json").write_text(
        json.dumps(entity_source_refs, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    return {
        "vault_dir": str(vault_dir),
        "note_count": len(notes),
        "issue_count": issue_count,
        "entity_count": entity_count,
    }


def prepare_quartz_content(vault_dir: Path, content_dir: Path) -> dict[str, str]:
    vault_dir = vault_dir.resolve()
    content_dir = content_dir.resolve()
    if not vault_dir.exists():
        raise SystemExit(f"Vault directory does not exist: {vault_dir}")

    content_dir.parent.mkdir(parents=True, exist_ok=True)
    _copy_tree(vault_dir, content_dir)

    meta_dir = content_dir / "_meta"
    if meta_dir.exists():
        shutil.rmtree(meta_dir)

    home_path = content_dir / "Home.md"
    if home_path.exists():
        index_path = content_dir / "index.md"
        index_path.write_text(home_path.read_text(encoding="utf-8"), encoding="utf-8")
        home_path.unlink()

    return {"content_dir": str(content_dir)}


def build_scene_wiki(run_dir: Path, site_dir: Path, vault_dir: Path | None = None) -> dict[str, Any]:
    site_dir = site_dir.resolve()
    default_vault = (Path("vault") / _slugify(_archive_label(run_dir))).resolve()
    actual_vault = (vault_dir or default_vault).resolve()
    build_result = build_obsidian_vault(run_dir=run_dir, vault_dir=actual_vault)
    prepare_result = prepare_quartz_content(vault_dir=actual_vault, content_dir=site_dir / "content")
    return {
        **build_result,
        **prepare_result,
        "site_dir": str(site_dir),
    }
