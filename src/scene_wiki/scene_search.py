from __future__ import annotations

import json
import math
import os
import re
import time
from collections import Counter
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from .config import get_settings
from .scene_wiki import _clean_text
from .scene_wiki import _entity_blurb
from .scene_wiki import _entity_search_terms
from .scene_wiki import _external_link_values
from .scene_wiki import _human_date
from .scene_wiki import CATEGORY_TITLES
from .scene_wiki import build_doc_entities
from .scene_wiki import build_entity_note_paths
from .scene_wiki import load_scene_corpus


EMBEDDING_MODEL = "gemini-embedding-2-preview"
EMBEDDING_DIMENSIONS = 768
DOCUMENT_TASK_TYPE = "RETRIEVAL_DOCUMENT"
QUERY_TASK_TYPE = "RETRIEVAL_QUERY"
GOOGLE_EMBED_ENDPOINT = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
    f"{EMBEDDING_MODEL}:batchEmbedContents"
)
MIN_BLOCK_CHARS = 120
TARGET_CHUNK_CHARS = 1350
MAX_CHUNK_CHARS = 1800
MAX_CHUNK_ENTITIES = 8
MAX_ENTITY_BACKLINKS = 6
MAX_ENTITY_CONTEXTS = 6
MAX_EMBED_RETRIES = 10
SEARCH_INDEX_SHARD_TARGET_BYTES = 12 * 1024 * 1024
DEFAULT_EMBED_BATCH_SIZE = 24
DEFAULT_EMBED_REQUEST_DELAY_SECONDS = 1.0
MAX_EMBED_RETRY_DELAY_SECONDS = 120.0


def _google_api_key() -> str:
    settings = get_settings()
    api_key = settings.google_api_key or settings.gemini_api_key
    if not api_key:
        raise RuntimeError("Set GOOGLE_API_KEY or GEMINI_API_KEY to build the scene search index.")
    return api_key


def _cosine_norm(values: list[float]) -> float:
    return math.sqrt(sum(value * value for value in values))


def _normalize_embedding(values: list[float]) -> list[float]:
    norm = _cosine_norm(values) or 1.0
    return [round(value / norm, 6) for value in values]


def _search_embedding_batch_size() -> int:
    raw = os.getenv("SCENE_WIKI_SEARCH_EMBED_BATCH_SIZE", "").strip()
    if not raw:
        return DEFAULT_EMBED_BATCH_SIZE
    try:
        return max(1, int(raw))
    except ValueError:
        return DEFAULT_EMBED_BATCH_SIZE


def _search_embed_request_delay_seconds() -> float:
    raw = os.getenv("SCENE_WIKI_SEARCH_EMBED_REQUEST_DELAY_SECONDS", "").strip()
    if not raw:
        return DEFAULT_EMBED_REQUEST_DELAY_SECONDS
    try:
        return max(0.0, float(raw))
    except ValueError:
        return DEFAULT_EMBED_REQUEST_DELAY_SECONDS


def _embed_text_batch(texts: list[str], *, task_type: str, api_key: str) -> list[list[float]]:
    if not texts:
        return []

    requests = [
        {
            "model": f"models/{EMBEDDING_MODEL}",
            "content": {"parts": [{"text": text}]},
            "taskType": task_type,
            "outputDimensionality": EMBEDDING_DIMENSIONS,
            "title": f"scene-search-{index}",
        }
        for index, text in enumerate(texts)
    ]

    with httpx.Client(timeout=120.0) as client:
        response: httpx.Response | None = None
        for attempt in range(MAX_EMBED_RETRIES):
            response = client.post(
                GOOGLE_EMBED_ENDPOINT,
                headers={
                    "content-type": "application/json",
                    "x-goog-api-key": api_key,
                },
                json={"requests": requests},
            )
            if response.status_code != 429:
                break

            if attempt == MAX_EMBED_RETRIES - 1:
                response.raise_for_status()

            retry_after = response.headers.get("retry-after")
            try:
                delay = float(retry_after) if retry_after else 2**attempt
            except ValueError:
                delay = 2**attempt
            delay = min(delay, MAX_EMBED_RETRY_DELAY_SECONDS)
            print(
                f"Search embedding rate limited for batch of {len(texts)}; retrying in {round(delay, 1)}s "
                f"(attempt {attempt + 1}/{MAX_EMBED_RETRIES}).",
                flush=True,
            )
            time.sleep(delay)

        if response is None:
            raise RuntimeError("Embedding request did not produce a response.")
        response.raise_for_status()
        payload = response.json()

    responses = payload.get("embeddings", [])
    if len(responses) != len(texts):
        raise RuntimeError(f"Embedding count mismatch: expected {len(texts)}, got {len(responses)}")

    embeddings: list[list[float]] = []
    for item in responses:
        values = item.get("values", [])
        if not values:
            raise RuntimeError("Embedding response was missing vector values.")
        embeddings.append(_normalize_embedding([float(value) for value in values]))
    return embeddings


def _search_embedding_cache_dir(run_dir: Path) -> Path:
    cache_dir = run_dir / "artifacts" / "search-embedding-batches"
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir


def _search_embedding_cache_path(cache_dir: Path, start: int, end: int) -> Path:
    return cache_dir / f"batch-{start:05d}-{end:05d}.json"


def _load_cached_search_embedding_batch(cache_path: Path, *, expected_chunk_ids: list[str]) -> list[list[float]] | None:
    if not cache_path.exists():
        return None
    try:
        payload = json.loads(cache_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    if payload.get("chunk_ids") != expected_chunk_ids:
        return None
    embeddings = payload.get("embeddings")
    if not isinstance(embeddings, list) or len(embeddings) != len(expected_chunk_ids):
        return None
    return embeddings


def _write_cached_search_embedding_batch(cache_path: Path, *, chunk_ids: list[str], embeddings: list[list[float]]) -> None:
    cache_path.write_text(
        json.dumps(
            {
                "chunk_ids": chunk_ids,
                "embeddings": embeddings,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )


def _split_primary_blocks(text: str) -> list[str]:
    raw_blocks = re.split(r"\n\s*\n+", text or "")
    blocks: list[str] = []
    for raw_block in raw_blocks:
        lines = [_clean_text(line) for line in raw_block.splitlines()]
        lines = [line for line in lines if line]
        if not lines:
            continue
        block = "\n".join(lines)
        if len(block) <= MAX_CHUNK_CHARS:
            blocks.append(block)
            continue

        sentences = re.split(r"(?<=[.!?])\s+", block.replace("\n", " "))
        current: list[str] = []
        current_len = 0
        for sentence in sentences:
            sentence = _clean_text(sentence)
            if not sentence:
                continue
            projected = current_len + len(sentence) + (1 if current else 0)
            if current and projected > MAX_CHUNK_CHARS:
                blocks.append(" ".join(current))
                current = [sentence]
                current_len = len(sentence)
            else:
                current.append(sentence)
                current_len = projected
        if current:
            blocks.append(" ".join(current))
    return [block for block in blocks if len(_clean_text(block)) >= MIN_BLOCK_CHARS]


def _chunk_primary_text(text: str) -> list[str]:
    blocks = _split_primary_blocks(text)
    if not blocks:
        clean = _clean_text(text)
        return [clean] if clean else []

    chunks: list[str] = []
    current: list[str] = []
    current_len = 0

    def flush() -> None:
        nonlocal current, current_len
        if current:
            chunks.append("\n\n".join(current).strip())
        current = []
        current_len = 0

    for block in blocks:
        projected = current_len + len(block) + (2 if current else 0)
        if current and projected > TARGET_CHUNK_CHARS:
            flush()
        current.append(block)
        current_len += len(block) + (2 if len(current) > 1 else 0)
        if current_len >= TARGET_CHUNK_CHARS:
            flush()
    flush()

    return chunks


def _matches_entity(chunk_text: str, entity: dict[str, Any]) -> bool:
    clean = _clean_text(chunk_text).lower()
    for term in _entity_search_terms(entity):
        needle = _clean_text(term).lower()
        if needle and needle in clean:
            return True
    return False


def _build_related_counts(doc_entities: dict[str, list[int]]) -> dict[int, Counter[int]]:
    related_counts: dict[int, Counter[int]] = defaultdict(Counter)
    for entity_ids in doc_entities.values():
        unique_ids = list(dict.fromkeys(entity_ids))
        for entity_id in unique_ids:
            for other_id in unique_ids:
                if other_id != entity_id:
                    related_counts[entity_id][other_id] += 1
    return related_counts


def _issue_path(doc_id: str) -> str:
    return f"issues/{doc_id}"


def _category_path(category: str) -> str:
    return f"categories/{category}"


def _json_size_bytes(value: Any) -> int:
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


def _split_chunk_shards(chunks: list[dict[str, Any]], *, target_bytes: int) -> list[list[dict[str, Any]]]:
    shards: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    current_bytes = 2  # []

    for chunk in chunks:
        chunk_bytes = _json_size_bytes(chunk)
        projected = current_bytes + chunk_bytes + (1 if current else 0)
        if current and projected > target_bytes:
            shards.append(current)
            current = [chunk]
            current_bytes = 2 + chunk_bytes
            continue
        current.append(chunk)
        current_bytes = projected

    if current:
        shards.append(current)
    return shards


def _split_mapping_shards(mapping: dict[str, Any], *, target_bytes: int) -> list[dict[str, Any]]:
    shards: list[dict[str, Any]] = []
    current: dict[str, Any] = {}
    current_bytes = 2  # {}

    for key, value in mapping.items():
        item_bytes = _json_size_bytes({key: value})
        projected = current_bytes + item_bytes + (1 if current else 0)
        if current and projected > target_bytes:
            shards.append(current)
            current = {key: value}
            current_bytes = 2 + item_bytes
            continue
        current[key] = value
        current_bytes = projected

    if current:
        shards.append(current)
    return shards


def _clear_existing_search_assets(output_dir: Path) -> None:
    for pattern in (
        "scene-search-index.json",
        "scene-search-issues.json",
        "scene-search-entities.json",
        "scene-search-entities-*.json",
        "scene-search-chunks-*.json",
    ):
        for path in output_dir.glob(pattern):
            if path.is_file():
                path.unlink()


def build_scene_search_assets(run_dir: Path, output_dir: Path) -> dict[str, Any]:
    run_dir = run_dir.resolve()
    output_dir = output_dir.resolve()
    _clear_existing_search_assets(output_dir)

    docs, entities = load_scene_corpus(run_dir)
    entity_note_paths = build_entity_note_paths(entities)
    doc_entities = build_doc_entities(docs, entities)
    related_counts = _build_related_counts(doc_entities)
    issue_paths = {doc_id: _issue_path(doc_id) for doc_id in docs}

    chunks: list[dict[str, Any]] = []
    for doc_id, doc in sorted(docs.items(), key=lambda item: item[1].get("published_at") or ""):
        doc_chunks = _chunk_primary_text(doc.get("text", ""))
        entity_ids = doc_entities.get(doc_id, [])
        for index, chunk_text in enumerate(doc_chunks):
            matched_entity_ids = [
                entity_id for entity_id in entity_ids if _matches_entity(chunk_text, entities[entity_id])
            ]
            if not matched_entity_ids:
                matched_entity_ids = entity_ids[:MAX_CHUNK_ENTITIES]

            chunks.append(
                {
                    "chunk_id": f"{doc_id}:{index}",
                    "doc_id": doc_id,
                    "issue_path": issue_paths[doc_id],
                    "issue_title": doc.get("title", doc_id),
                    "published_at": doc.get("published_at"),
                    "published_at_human": _human_date(doc.get("published_at")),
                    "source_url": doc.get("url"),
                    "primary_text": chunk_text,
                    "entity_paths": [entity_note_paths[entity_id] for entity_id in matched_entity_ids[:MAX_ENTITY_CONTEXTS]],
                }
            )

    if not chunks:
        raise RuntimeError("No searchable chunks were produced for the scene wiki.")

    api_key = _google_api_key()
    primary_texts = [chunk["primary_text"] for chunk in chunks]
    embeddings: list[list[float]] = []
    batch_size = _search_embedding_batch_size()
    request_delay_seconds = _search_embed_request_delay_seconds()
    cache_dir = _search_embedding_cache_dir(run_dir)
    total_batches = math.ceil(len(primary_texts) / batch_size)
    for start in range(0, len(primary_texts), batch_size):
        batch = primary_texts[start : start + batch_size]
        end = min(start + batch_size, len(primary_texts))
        batch_index = start // batch_size + 1
        batch_chunk_ids = [chunk["chunk_id"] for chunk in chunks[start:end]]
        cache_path = _search_embedding_cache_path(cache_dir, start, end)
        cached_batch = _load_cached_search_embedding_batch(cache_path, expected_chunk_ids=batch_chunk_ids)
        if cached_batch is not None:
            print(
                f"Reusing cached search embedding batch {batch_index}/{total_batches} "
                f"({len(cached_batch)} chunks).",
                flush=True,
            )
            embeddings.extend(cached_batch)
            continue

        print(
            f"Embedding search batch {batch_index}/{total_batches} ({len(batch)} chunks).",
            flush=True,
        )
        batch_embeddings = _embed_text_batch(batch, task_type=DOCUMENT_TASK_TYPE, api_key=api_key)
        _write_cached_search_embedding_batch(cache_path, chunk_ids=batch_chunk_ids, embeddings=batch_embeddings)
        embeddings.extend(batch_embeddings)
        if request_delay_seconds > 0 and batch_index < total_batches:
            time.sleep(request_delay_seconds)

    if len(chunks) != len(embeddings):
        raise RuntimeError(f"Embedding count mismatch: expected {len(chunks)}, got {len(embeddings)}")

    for chunk, embedding in zip(chunks, embeddings):
        chunk["embedding"] = embedding

    entity_payload = {}
    for entity_id, entity in enumerate(entities):
        path = entity_note_paths[entity_id]
        issue_ids = [doc_id for doc_id in entity.get("post_ids", []) if doc_id in docs]
        issue_ids.sort(key=lambda doc_id: docs[doc_id].get("published_at") or "", reverse=True)
        entity_payload[path] = {
            "title": entity["name"],
            "category": entity["category"],
            "category_title": CATEGORY_TITLES.get(entity["category"], entity["category"].title()),
            "mention_count": int(entity.get("mention_count", 0)),
            "issue_count": len(issue_ids),
            "blurb": _entity_blurb(
                entity=entity,
                docs=docs,
                related_counts=related_counts,
                entities=entities,
                entity_id=entity_id,
            ),
            "external_links": _external_link_values(entity)[:8],
            "backlinks": [
                {
                    "path": _category_path(entity["category"]),
                    "title": CATEGORY_TITLES.get(entity["category"], entity["category"].title()),
                    "kind": "category",
                    "reason": "Category index",
                },
                *[
                    {
                        "path": issue_paths[doc_id],
                        "title": docs[doc_id].get("title", doc_id),
                        "kind": "issue",
                        "reason": "Referenced in this issue",
                    }
                    for doc_id in issue_ids[:MAX_ENTITY_BACKLINKS]
                ],
            ],
        }

    chunk_shards = _split_chunk_shards(chunks, target_bytes=SEARCH_INDEX_SHARD_TARGET_BYTES)
    entity_shards = _split_mapping_shards(entity_payload, target_bytes=SEARCH_INDEX_SHARD_TARGET_BYTES)
    search_index = {
        "meta": {
            "model": EMBEDDING_MODEL,
            "dimensions": EMBEDDING_DIMENSIONS,
            "document_task_type": DOCUMENT_TASK_TYPE,
            "query_task_type": QUERY_TASK_TYPE,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "chunk_count": len(chunks),
            "search_index_version": 2,
        },
        "entity_shards": [
            {
                "path": f"scene-search-entities-{index:03d}.json",
                "entity_count": len(shard),
            }
            for index, shard in enumerate(entity_shards)
        ],
        "issues_path": "scene-search-issues.json",
        "chunk_shards": [
            {
                "path": f"scene-search-chunks-{index:03d}.json",
                "chunk_count": len(shard),
            }
            for index, shard in enumerate(chunk_shards)
        ],
    }

    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "scene-search-index.json").write_text(
        json.dumps(search_index, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    for index, shard in enumerate(entity_shards):
        (output_dir / f"scene-search-entities-{index:03d}.json").write_text(
            json.dumps({"entities": shard}, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )
    (output_dir / "scene-search-issues.json").write_text(
        json.dumps(
            {
                "issues": {
                    issue_paths[doc_id]: {
                        "title": doc.get("title", doc_id),
                        "published_at": doc.get("published_at"),
                        "published_at_human": _human_date(doc.get("published_at")),
                        "url": doc.get("url"),
                    }
                    for doc_id, doc in docs.items()
                }
            },
            ensure_ascii=False,
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )
    for index, shard in enumerate(chunk_shards):
        (output_dir / f"scene-search-chunks-{index:03d}.json").write_text(
            json.dumps({"chunks": shard}, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )

    return {
        "output_dir": str(output_dir),
        "chunk_count": len(chunks),
        "chunk_shard_count": len(chunk_shards),
        "embedding_model": EMBEDDING_MODEL,
    }
