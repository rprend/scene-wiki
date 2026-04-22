from __future__ import annotations

import hashlib
import json
import os
import re
import time
from collections import Counter
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

from openai import OpenAI

from .config import get_settings
from .openai_usage import merge_usage_summaries
from .openai_usage import record_openai_usage
from .scene_search import build_scene_search_assets
from .scene_wiki import CATEGORY_DESCRIPTIONS
from .scene_wiki import CATEGORY_TITLES
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


@dataclass
class EntityArticleDraft:
    lede: str
    paragraphs: list[str]
    citations: list[dict[str, str]]


ENTITY_ARTICLE_STYLE_GUIDE = """\
Style guide:
- Voice: synthesize intellectual rigor with gleeful provocation. Write like someone who genuinely loves ideas and equally loves watching them get weird, uncomfortable, or self-defeating. Default to confident curiosity, not academic hedging.
- Sentence architecture: open with the sharpest declarative claim first, then unpack it. Vary sentence length aggressively: long build, short punch. Build paragraphs by stating the thesis, complicating it, giving examples, then landing a conclusion that is more right, more wrong, or weirder than expected.
- Point of view: use "I" only when explicitly staking a judgment. Avoid fake neutrality.
- Punctuation: use em dashes for interruptions that stay on track, rhetorical questions sparingly, and colons to introduce evidence or quotations without filler.
- Vocabulary: prefer precision over impressiveness. Use plain language when it hits harder.
- Tone: stay dry, sharp, and intellectually amused, but do not become flippant.
- Assertions: when uncertain, own the uncertainty directly.
- Formatting: return concise synthesis, then supporting direct quotation where evidence is strong.
- Non-negotiables: never bury the point, never dilute a clear conclusion, and always tie claims to quoted evidence when available.
"""


ENTITY_ARTICLE_PROMPT = """\
You are writing a concise encyclopedia article for a source-grounded community wiki.

Write clean, readable prose about the entity using ONLY the supplied source material.

Rules:
- Do not say "this archive", "this corpus", "recurring", or mention raw counts in the prose.
- Do not invent facts not supported by the supplied issue titles, issue excerpts, or evidence snippets.
- You may make light inferences that are directly supported by the source material.
- Write like a compact encyclopedia entry, not metadata or analytics output.
- Prefer concrete context over generic statements.
- If the evidence is thin, still write a short factual paragraph rather than a template blurb.
- Include direct quotation and citations when the evidence supports it.
- Prefer citation breadth when the evidence supports it.
- Surface uncertainty when evidence is thin or conflicting.
- Prefer 2 to 5 citations when the supplied material supports that many. Use multiple distinct quotations from the same issue when that is all you have.
- Pull quotations from both the issue excerpt and the retained evidence snippets when possible.
- The prose should make at least one hard claim up front, then cash it out with specifics.
- No bullet points. No headings. No markdown beyond plain text.

Return ONLY valid JSON in this shape:
{
  "lede": "one short opening paragraph",
  "paragraphs": ["optional second paragraph", "optional third paragraph"],
  "citations": [
    {
      "issue_title": "exact issue title from the supplied material",
      "quote": "exact or tightly cleaned quotation from the supplied material",
      "note": "what this quotation establishes"
    }
  ]
}
"""

DEFAULT_ENTITY_ARTICLE_MODEL = "gpt-4.1-mini"
ENTITY_ARTICLE_MAX_DOC_CHARS = 1600
ENTITY_ARTICLE_MAX_EVIDENCE = 6
ENTITY_ARTICLE_PROMPT_VERSION = "v3"
ENTITY_ARTICLE_REQUEST_TIMEOUT_SECONDS = 90.0
ENTITY_ARTICLE_MAX_RETRIES = 5


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


def _entity_article_model() -> str:
    return os.getenv("SCENE_WIKI_MEDIAWIKI_ARTICLE_MODEL", DEFAULT_ENTITY_ARTICLE_MODEL).strip() or DEFAULT_ENTITY_ARTICLE_MODEL


def _entity_article_cache_dir(run_dir: Path) -> Path:
    cache_dir = run_dir / "artifacts" / "mediawiki-entity-articles"
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir


def _entity_article_usage_dir(run_dir: Path) -> Path:
    usage_dir = run_dir / "artifacts" / "mediawiki-article-usage"
    usage_dir.mkdir(parents=True, exist_ok=True)
    return usage_dir


def _write_ai_usage_report(run_dir: Path) -> dict[str, Any]:
    summary_paths = [
        run_dir / "artifacts" / "openai-usage" / "summary.json",
        run_dir / "artifacts" / "mediawiki-article-usage" / "summary.json",
        run_dir / "artifacts" / "search-embedding-usage" / "summary.json",
    ]
    report = merge_usage_summaries(*summary_paths)
    report["sources"] = [str(path) for path in summary_paths if path.exists()]
    output_path = run_dir / "artifacts" / "ai-usage-report.json"
    output_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report


def _entity_article_should_use_ai(entity: dict[str, Any], issue_ids: list[str]) -> bool:
    if not issue_ids:
        return False
    category = (entity.get("category") or "").strip().lower()
    return category not in {"social_account", "social_accounts"}


def _trim_doc_excerpt(text: str) -> str:
    clean = re.sub(r"\s+", " ", (text or "").strip())
    return clean[:ENTITY_ARTICLE_MAX_DOC_CHARS].strip()


def _entity_article_payload(
    *,
    entity: dict[str, Any],
    issue_ids: list[str],
    docs: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    issue_contexts: list[dict[str, Any]] = []
    for doc_id in issue_ids[:3]:
        doc = docs.get(doc_id)
        if not doc:
            continue
        issue_contexts.append(
            {
                "title": doc.get("title", doc_id),
                "publishedAt": doc.get("published_at"),
                "excerpt": _trim_doc_excerpt(doc.get("text", "")),
            }
        )
    return {
        "entity": {
            "name": entity.get("name"),
            "category": entity.get("category"),
            "aliases": entity.get("aliases", []),
            "platform": entity.get("platform"),
            "handle": entity.get("handle"),
            "workType": entity.get("work_type"),
        },
        "evidence": [item for item in entity.get("evidence", []) if item][:ENTITY_ARTICLE_MAX_EVIDENCE],
        "issues": issue_contexts,
    }


def _entity_article_cache_path(cache_dir: Path, payload: dict[str, Any], model: str) -> Path:
    digest = hashlib.sha256(
        json.dumps(
            {
                "version": ENTITY_ARTICLE_PROMPT_VERSION,
                "model": model,
                "styleGuide": ENTITY_ARTICLE_STYLE_GUIDE,
                "prompt": ENTITY_ARTICLE_PROMPT,
                "payload": payload,
            },
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()
    safe_name = re.sub(r"[^A-Za-z0-9._-]+", "_", payload["entity"]["name"] or "entity")[:80]
    return cache_dir / f"{safe_name}-{digest[:16]}.json"


def _load_cached_entity_article(cache_path: Path) -> EntityArticleDraft | None:
    if not cache_path.exists():
        return None
    try:
        payload = json.loads(cache_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    lede = str(payload.get("lede", "")).strip()
    paragraphs = [str(item).strip() for item in payload.get("paragraphs", []) if str(item).strip()]
    citations = [
        {
            "issue_title": str(item.get("issue_title", "")).strip(),
            "quote": str(item.get("quote", "")).strip(),
            "note": str(item.get("note", "")).strip(),
        }
        for item in payload.get("citations", [])
        if str(item.get("issue_title", "")).strip() and str(item.get("quote", "")).strip()
    ]
    if not lede:
        return None
    return EntityArticleDraft(lede=lede, paragraphs=paragraphs, citations=citations)


def _entity_role_phrase(entity: dict[str, Any]) -> str:
    category = (entity.get("category", "") or "").strip().lower()
    platform = entity.get("platform")
    work_type = entity.get("work_type")
    if category in {"people", "person"}:
        return "a person"
    if category in {"organizations", "organization"}:
        return "an organization"
    if category in {"places", "place"}:
        return "a place"
    if category in {"concepts", "concept"}:
        return "an idea or concept"
    if category in {"books", "book"}:
        return f"a {work_type}" if work_type else "a written work"
    if category in {"publications", "publication"}:
        return "a publication"
    if category in {"social_accounts", "social_account"}:
        if platform:
            return f"a social account on {platform}"
        return "a social account"
    if category in {"films", "film"}:
        return "a film"
    if category in {"music", "song"}:
        return "a piece of music"
    if category in {"events", "event"}:
        return "an event"
    if category in {"brands", "brand"}:
        return "a brand"
    if category in {"venues", "venue"}:
        return "a venue"
    return "an entity"


def _clean_evidence_sentence(text: str) -> str:
    cleaned = re.sub(r"\s+", " ", (text or "").strip()).strip(' "')
    if not cleaned:
        return ""
    if cleaned[-1] not in ".!?":
        cleaned += "."
    return cleaned


def _issue_links(issue_ids: list[str], issue_titles: dict[str, str], docs: dict[str, dict[str, Any]], limit: int = 3) -> str:
    selected = issue_ids[:limit]
    links = [_wiki_link(issue_titles[doc_id], docs[doc_id].get("title", doc_id)) for doc_id in selected]
    if not links:
        return ""
    if len(links) == 1:
        return links[0]
    if len(links) == 2:
        return f"{links[0]} and {links[1]}"
    return f"{', '.join(links[:-1])}, and {links[-1]}"


def _entity_lede(
    *,
    entity: dict[str, Any],
    issue_ids: list[str],
    docs: dict[str, dict[str, Any]],
    issue_titles: dict[str, str],
) -> str:
    name = entity.get("name", "This entry")
    role = _entity_role_phrase(entity)
    first_issue = docs[issue_ids[0]] if issue_ids else None
    first_issue_link = _wiki_link(issue_titles[issue_ids[0]], first_issue.get("title", issue_ids[0])) if first_issue and issue_ids else None
    mention_count = int(entity.get("mention_count", 0) or 0)
    evidence = [item for item in entity.get("evidence", []) if item]
    primary_quote = _clean_evidence_sentence(evidence[0]) if evidence else ""
    secondary_quote = _clean_evidence_sentence(evidence[1]) if len(evidence) > 1 else ""

    lines: list[str] = []
    if first_issue_link:
        lines.append(
            f"'''{name}''' appears in this archive as {role} discussed in {first_issue_link}."
        )
    else:
        lines.append(f"'''{name}''' appears in this archive as {role}.")

    if primary_quote:
        if secondary_quote and secondary_quote != primary_quote:
            lines.append(
                f"The source material connects {name} to passages such as \"{primary_quote}\" and \"{secondary_quote}\"."
            )
        else:
            lines.append(f"The source material connects {name} to the passage \"{primary_quote}\".")

    linked_issues = _issue_links(issue_ids, issue_titles, docs)
    if linked_issues:
        if mention_count > 1:
            lines.append(
                f"In this corpus, {name} appears {mention_count} times across {len(issue_ids)} issues, including {linked_issues}."
            )
        else:
            lines.append(f"In this corpus, {name} appears in {linked_issues}.")

    return " ".join(lines).strip()


def _fallback_entity_article(
    *,
    entity: dict[str, Any],
    issue_ids: list[str],
    docs: dict[str, dict[str, Any]],
    issue_titles: dict[str, str],
) -> EntityArticleDraft:
    return EntityArticleDraft(
        lede=_entity_lede(entity=entity, issue_ids=issue_ids, docs=docs, issue_titles=issue_titles),
        paragraphs=[],
        citations=[
            {
                "issue_title": docs[doc_id].get("title", doc_id),
                "quote": _clean_evidence_sentence(item),
                "note": "Primary source evidence retained by the extraction pipeline.",
            }
            for doc_id in issue_ids[:1]
            for item in [e for e in entity.get("evidence", []) if e][:2]
            if _clean_evidence_sentence(item)
        ],
    )


def _synthesize_entity_article(
    *,
    run_dir: Path,
    entity_id: int,
    entity: dict[str, Any],
    issue_ids: list[str],
    docs: dict[str, dict[str, Any]],
    issue_titles: dict[str, str],
) -> EntityArticleDraft:
    if not _entity_article_should_use_ai(entity, issue_ids):
        return _fallback_entity_article(entity=entity, issue_ids=issue_ids, docs=docs, issue_titles=issue_titles)

    api_key = get_settings().openai_api_key
    if not api_key:
        return _fallback_entity_article(entity=entity, issue_ids=issue_ids, docs=docs, issue_titles=issue_titles)

    payload = _entity_article_payload(entity=entity, issue_ids=issue_ids, docs=docs)
    model = _entity_article_model()
    cache_path = _entity_article_cache_path(_entity_article_cache_dir(run_dir), payload, model)
    cached = _load_cached_entity_article(cache_path)
    if cached:
        return cached

    print(f"Synthesizing MediaWiki article for {entity.get('name', 'entity')} ({entity_id}).", flush=True)
    prompt = (
        f"{ENTITY_ARTICLE_PROMPT}\n\n"
        f"{ENTITY_ARTICLE_STYLE_GUIDE}\n\n"
        f"SOURCE MATERIAL:\n{json.dumps(payload, ensure_ascii=False, indent=2)}"
    )
    usage_dir = _entity_article_usage_dir(run_dir)
    previous_usage_dir = os.environ.get("SCENE_WIKI_AI_USAGE_DIR")
    os.environ["SCENE_WIKI_AI_USAGE_DIR"] = str(usage_dir)
    try:
        response = None
        for attempt in range(ENTITY_ARTICLE_MAX_RETRIES):
            try:
                client = OpenAI(
                    api_key=api_key,
                    timeout=ENTITY_ARTICLE_REQUEST_TIMEOUT_SECONDS,
                    max_retries=2,
                )
                response = client.responses.create(
                    model=model,
                    input=prompt,
                    max_output_tokens=900,
                )
                break
            except Exception as exc:
                if attempt == ENTITY_ARTICLE_MAX_RETRIES - 1:
                    raise
                delay = min(2**attempt, 30)
                print(
                    f"Retrying MediaWiki article for {entity.get('name', 'entity')} after "
                    f"{type(exc).__name__}: waiting {delay}s (attempt {attempt + 2}/{ENTITY_ARTICLE_MAX_RETRIES}).",
                    flush=True,
                )
                time.sleep(delay)
        if response is None:
            raise RuntimeError(f"No response received for entity article {entity.get('name', 'entity')}.")
        usage = getattr(response, "usage", None)
        record_openai_usage(
            requested_model=model,
            resolved_model=getattr(response, "model", None),
            response_id=getattr(response, "id", None),
            input_tokens=int(getattr(usage, "input_tokens", 0) or 0),
            output_tokens=int(getattr(usage, "output_tokens", 0) or 0),
            total_tokens=int(getattr(usage, "total_tokens", 0) or 0),
            prompt_chars=len(prompt),
            prompt_text=prompt,
            response_text=response.output_text,
            chunk_id=f"entity-{entity_id}",
            doc_id=",".join(issue_ids[:3]) or None,
        )
    finally:
        if previous_usage_dir is None:
            os.environ.pop("SCENE_WIKI_AI_USAGE_DIR", None)
        else:
            os.environ["SCENE_WIKI_AI_USAGE_DIR"] = previous_usage_dir

    raw = response.output_text.strip()
    if "```json" in raw:
        raw = raw.split("```json", 1)[1].split("```", 1)[0].strip()
    elif "```" in raw:
        raw = raw.split("```", 1)[1].split("```", 1)[0].strip()
    if not raw.startswith("{"):
        start = raw.find("{")
        end = raw.rfind("}")
        if start >= 0 and end > start:
            raw = raw[start : end + 1]

    try:
        data = json.loads(raw)
        draft = EntityArticleDraft(
            lede=str(data.get("lede", "")).strip(),
            paragraphs=[str(item).strip() for item in data.get("paragraphs", []) if str(item).strip()],
            citations=[
                {
                    "issue_title": str(item.get("issue_title", "")).strip(),
                    "quote": str(item.get("quote", "")).strip(),
                    "note": str(item.get("note", "")).strip(),
                }
                for item in data.get("citations", [])
                if str(item.get("issue_title", "")).strip() and str(item.get("quote", "")).strip()
            ],
        )
        if not draft.lede:
            raise ValueError("Missing lede")
    except Exception:
        draft = _fallback_entity_article(entity=entity, issue_ids=issue_ids, docs=docs, issue_titles=issue_titles)

    cache_path.write_text(
        json.dumps(
            {"lede": draft.lede, "paragraphs": draft.paragraphs, "citations": draft.citations},
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return draft


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
    top_categories = sorted(category_counts.items(), key=lambda item: (-item[1], item[0]))[:8]
    guided_entities = top_entities[:8]

    lines = [
        f"= {site_title} =",
        "",
        f"'''{site_title}''' is a source-grounded wiki built from the publication archive. It turns episodes and posts into browsable article pages for people, places, books, organizations, ideas, and other recurring references in the community.",
        "",
        "Use this wiki in three ways: start from a conversation, jump into a category, or open one of the frequently referenced entity pages below.",
        "",
        "== Start here ==",
        "* Visit [[Semantic search]] to run semantic search over the archive and jump directly into relevant pages.",
        "* Browse the latest imported conversations under ''Recent conversations''.",
        "* Open a category page under ''Browse by category'' to explore a specific slice of the corpus.",
        "",
        "== Browse by category ==",
    ]
    for category, count in top_categories:
        category_title = CATEGORY_TITLES.get(category, category.replace("_", " ").title())
        lines.append(
            f"* [[Category:{category_title}|{category_title}]]: {count} article pages grouped under {category_title.lower()}."
        )

    lines.extend(["", "== Good pages to start with =="])
    for entity_id in guided_entities:
        entity = entities[entity_id]
        lines.append(
            f"* {_wiki_link(entity_titles[entity_id])}: "
            f"{CATEGORY_TITLES.get(entity.get('category', ''), entity.get('category', '').title())} page with "
            f"{entity.get('mention_count', 0)} corpus mentions."
        )

    lines.extend(["", "== Recent conversations =="])
    for doc_id, doc in recent_issues:
        lines.append(f"* {_wiki_link(issue_titles[doc_id], doc.get('title', doc_id))}")

    lines.extend(
        [
            "",
            "== About this corpus ==",
            f"* Issues imported: {len(docs)}",
            f"* Entity pages imported: {len(entities)}",
            "* Each article is generated from extracted references in the source archive and keeps links back to the underlying conversations.",
            "",
            "== Semantic search ==",
            WIKITEXT_SEARCH_NOTE,
        ]
    )
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
    run_dir: Path,
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
    article = _synthesize_entity_article(
        run_dir=run_dir,
        entity_id=entity_id,
        entity=entity,
        issue_ids=issue_ids,
        docs=docs,
        issue_titles=issue_titles,
    )

    lines = [
        f"= {entity.get('name', entity_titles[entity_id])} =",
        "",
        article.lede,
    ]
    for paragraph in article.paragraphs:
        lines.extend(["", paragraph])
    if article.citations:
        lines.extend(["", "== Source citations =="])
        for citation in article.citations[:6]:
            issue_title = citation["issue_title"]
            issue_link = next(
                (
                    _wiki_link(issue_titles[doc_id], docs[doc_id].get("title", doc_id))
                    for doc_id in issue_ids
                    if docs[doc_id].get("title", doc_id) == issue_title
                ),
                issue_title,
            )
            lines.append(f"* {issue_link}")
            lines.append(f"*: \"{citation['quote']}\"")
            if citation.get("note"):
                lines.append(f"*: {citation['note']}")
    lines.extend([
        "",
        "== Corpus metadata ==",
        f"* Category: {CATEGORY_TITLES.get(entity.get('category', ''), entity.get('category', '').title())}",
        f"* Mention count: {entity.get('mention_count', 0)}",
        f"* Issue count: {len(issue_ids)}",
    ])
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
    previous_usage_dir = os.environ.get("SCENE_WIKI_AI_USAGE_DIR")
    os.environ["SCENE_WIKI_AI_USAGE_DIR"] = str(_entity_article_usage_dir(run_dir))
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

    try:
        for entity_id, entity in enumerate(entities):
            category = entity.get("category", "unknown")
            category_buckets[category].append(entity_id)
            pages.append(
                MediaWikiPage(
                    title=entity_titles[entity_id],
                    text=_render_entity_page(
                        run_dir=run_dir,
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
    finally:
        if previous_usage_dir is None:
            os.environ.pop("SCENE_WIKI_AI_USAGE_DIR", None)
        else:
            os.environ["SCENE_WIKI_AI_USAGE_DIR"] = previous_usage_dir

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
    search_manifest: dict[str, Any] | None = None
    if build_search:
        search_manifest = build_scene_search_assets(run_dir=run_dir, output_dir=search_dir)
        if wiki_dir is not None:
            bundle_frontend_assets(wiki_dir=wiki_dir.resolve(), output_dir=output_dir)

    ai_usage_report = _write_ai_usage_report(run_dir)

    manifest = {
        "site_title": resolved_site_title,
        "page_count": len(pages),
        "entity_count": len(entities),
        "issue_count": len(docs),
        "import_xml": str(import_xml_path),
        "search_dir": str(search_dir) if build_search else None,
        "search_manifest": search_manifest,
        "ai_usage_report": ai_usage_report,
    }
    (output_dir / "mediawiki-export.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest
