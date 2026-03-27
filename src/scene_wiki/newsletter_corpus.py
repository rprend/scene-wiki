from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .link_extraction import extract_all_links, save_link_manifests
from .newsletter_extraction import CorpusEntityAggregate, extract_all_documents
from .storage import load_normalized_documents, read_json, write_json


def _corpus_filename() -> str:
    import os

    return os.environ.get("WIKI_CORPUS_FILENAME", "corpus.json")


def merge_entities_with_links(
    entities: list[CorpusEntityAggregate],
    all_post_links: list,
) -> list[dict[str, Any]]:
    instagram_urls: dict[str, set[str]] = defaultdict(set)
    anchor_to_links: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for post_links in all_post_links:
        for link in post_links.links:
            if link.instagram_handle:
                instagram_urls[link.instagram_handle].add(link.url)
            if link.anchor_text:
                anchor_to_links[link.anchor_text.lower()].append(
                    {
                        "url": link.url,
                        "domain": link.domain,
                        "link_type": link.link_type,
                        "doc_id": post_links.doc_id,
                        "instagram_handle": link.instagram_handle,
                    }
                )

    enriched: list[dict[str, Any]] = []
    matched_handles: set[str] = set()

    for entity in entities:
        record = entity.model_dump(mode="json")
        entity_urls: set[str] = set()
        entity_instagram: str | None = None
        entity_link_types: set[str] = set()

        if entity.category == "instagram_account":
            handle = entity.name.lower().lstrip("@")
            if handle in instagram_urls:
                entity_urls.update(instagram_urls[handle])
                entity_instagram = handle
                matched_handles.add(handle)

        name_lower = entity.name.lower()
        for anchor, link_infos in anchor_to_links.items():
            if name_lower in anchor or anchor in name_lower:
                for info in link_infos:
                    entity_urls.add(info["url"])
                    entity_link_types.add(info["link_type"])
                    if info.get("instagram_handle"):
                        entity_instagram = info["instagram_handle"]
                        matched_handles.add(info["instagram_handle"])

        record["urls"] = sorted(entity_urls)
        record["link_types"] = sorted(entity_link_types)
        if entity_instagram:
            record["instagram_handle"] = entity_instagram
        enriched.append(record)

    for post_links in all_post_links:
        for link in post_links.links:
            if link.instagram_handle and link.instagram_handle not in matched_handles:
                matched_handles.add(link.instagram_handle)
                handle_post_ids: set[str] = set()
                handle_urls: set[str] = set()
                for other in all_post_links:
                    for candidate in other.links:
                        if candidate.instagram_handle == link.instagram_handle:
                            handle_post_ids.add(other.doc_id)
                            handle_urls.add(candidate.url)
                enriched.append(
                    {
                        "name": f"@{link.instagram_handle}",
                        "category": "instagram_account",
                        "aliases": [],
                        "mention_count": len(handle_post_ids),
                        "evidence": [f"Linked as: {link.anchor_text}" if link.anchor_text else "Instagram link"],
                        "confidence": None,
                        "post_ids": sorted(handle_post_ids),
                        "first_seen": None,
                        "last_seen": None,
                        "urls": sorted(handle_urls),
                        "link_types": ["instagram"],
                        "instagram_handle": link.instagram_handle,
                    }
                )

    enriched.sort(key=lambda entity: (-entity.get("mention_count", 0), entity.get("category", ""), entity.get("name", "").lower()))
    return enriched


def build_link_summary(all_post_links: list) -> dict[str, Any]:
    total_links = 0
    by_type: dict[str, int] = defaultdict(int)
    by_domain: dict[str, int] = defaultdict(int)
    instagram_handles: set[str] = set()

    for post_links in all_post_links:
        for link in post_links.links:
            total_links += 1
            by_type[link.link_type] += 1
            by_domain[link.domain] += 1
            if link.instagram_handle:
                instagram_handles.add(link.instagram_handle)

    return {
        "total_links": total_links,
        "posts_with_links": sum(1 for item in all_post_links if item.links),
        "unique_instagram_handles": len(instagram_handles),
        "by_type": dict(sorted(by_type.items(), key=lambda item: -item[1])),
        "by_domain_top30": dict(sorted(by_domain.items(), key=lambda item: -item[1])[:30]),
    }


def build_newsletter_corpus(
    run_dir: Path,
    *,
    model: str = "sonnet",
    workers: int = 10,
    skip_llm: bool = False,
) -> dict[str, Any]:
    run_dir = run_dir.resolve()
    if not (run_dir / "normalized").exists():
        raise SystemExit(f"No normalized/ directory found in {run_dir}")

    metadata = read_json(run_dir / "metadata.json")
    subject = metadata.get("subject", "Newsletter Archive")

    html_dir = run_dir / "raw" / "html"
    if html_dir.exists() and any(html_dir.glob("*.html")):
        all_post_links = extract_all_links(run_dir)
        save_link_manifests(run_dir, all_post_links)
        link_summary = build_link_summary(all_post_links)
    else:
        all_post_links = []
        link_summary = {}

    if skip_llm:
        entities: list[CorpusEntityAggregate] = []
        per_chunk_results: list[dict[str, Any]] = []
    else:
        documents = load_normalized_documents(run_dir)
        per_chunk_results, entities = extract_all_documents(
            documents=documents,
            subject=subject,
            model=model,
            output_dir=run_dir / "artifacts",
            max_workers=workers,
        )
        write_json(run_dir / "artifacts" / "extraction-chunks.json", per_chunk_results)

    enriched_entities = merge_entities_with_links(entities, all_post_links)

    corpus = {
        "subject": subject,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "post_count": len(list((run_dir / "normalized").glob("*.json"))),
        "entity_count": len(enriched_entities),
        "link_summary": link_summary,
        "entities": enriched_entities,
    }

    output_path = run_dir / "artifacts" / _corpus_filename()
    write_json(output_path, corpus)
    return {
        "run_dir": str(run_dir),
        "output_path": str(output_path),
        "entity_count": len(enriched_entities),
        "post_count": corpus["post_count"],
    }
