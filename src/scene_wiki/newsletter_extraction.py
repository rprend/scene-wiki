from __future__ import annotations

import json
import os
import subprocess
import sys
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Optional

from openai import OpenAI
from pydantic import BaseModel, Field

from .chunking import chunk_documents
from .models import NormalizedDocument
from .storage import write_json


class NewsletterEntity(BaseModel):
    name: str
    category: str = Field(
        description=(
            "One of: person, venue, event, publication, instagram_account, "
            "book, film, music, brand, place, concept, organization"
        )
    )
    evidence: str = Field(description="Short quote or context from the source text")
    aliases: list[str] = Field(default_factory=list)
    confidence: Optional[float] = None


class NewsletterExtractionBatch(BaseModel):
    items: list[NewsletterEntity] = Field(default_factory=list)


NEWSLETTER_EXTRACTION_PROMPT = """\
You extract proper noun references and culturally relevant entities from a lifestyle/culture newsletter.

The newsletter is written by a young woman in New York City. It covers her daily life, recommendations \
for events, art openings, literary readings, film screenings, parties, restaurants, bars, galleries, \
and cultural happenings. Extract every concrete, named reference.

Categories (use exactly these):
- person: Named individuals — artists, writers, DJs, filmmakers, friends, public figures
- venue: Restaurants, bars, cafes, galleries, cinemas, clubs, parks, shops, hotels, specific named locations
- event: Specific named events, shows, readings, parties, screenings, runway shows, symposiums
- publication: Magazines, zines, Substacks, literary journals, newspapers, blogs, newsletters
- instagram_account: When an Instagram handle or account is referenced (by name or @mention)
- book: Books, novels, poetry collections, essays, specific written works
- film: Films, documentaries, TV shows, specific screenings
- music: Songs, albums, bands, musicians/artists referenced for their music
- brand: Fashion brands, clothing labels, product brands
- place: Neighborhoods, cities, countries, geographic regions (not specific venues)
- concept: Ideas, movements, cultural references, named aesthetic or intellectual concepts
- organization: Non-venue organizations — nonprofits, collectives, media companies, agencies

Rules:
- Extract EVERY named entity, even if mentioned casually
- For venues, include the type if clear (e.g. "Metrograph" is a cinema, "Balthazar" is a restaurant)
- If someone is both a person and a musician, categorize based on how they appear in context
- Keep evidence short — just enough to show the context of the mention
- Use the most specific category that fits
- Do NOT extract generic/common nouns or unnamed things

Respond with ONLY a JSON object matching this schema, no other text:
{
  "items": [
    {
      "name": "entity name",
      "category": "one of the categories above",
      "evidence": "short quote showing context",
      "aliases": ["optional", "alternate names"],
      "confidence": 0.95
    }
  ]
}

Subject: SUBJECT_PLACEHOLDER

Extract all named references from this text:

TEXT_PLACEHOLDER"""


class CorpusEntityAggregate(BaseModel):
    name: str
    category: str
    aliases: list[str] = Field(default_factory=list)
    mention_count: int = 0
    evidence: list[str] = Field(default_factory=list)
    confidence: Optional[float] = None
    post_ids: list[str] = Field(default_factory=list)
    first_seen: Optional[str] = None
    last_seen: Optional[str] = None


def extract_newsletter_chunk_claude(
    subject: str,
    chunk_text: str,
    model: str = "sonnet",
) -> NewsletterExtractionBatch:
    """Use the Claude CLI to extract entities, leveraging the user's OAuth session."""
    prompt = NEWSLETTER_EXTRACTION_PROMPT.replace("SUBJECT_PLACEHOLDER", subject).replace("TEXT_PLACEHOLDER", chunk_text)

    env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}
    try:
        result = subprocess.run(
            ["claude", "-p", "--output-format", "json", "--model", model, prompt],
            capture_output=True,
            text=True,
            timeout=180,
            env=env,
        )
    except subprocess.TimeoutExpired:
        print(f" TIMEOUT (skipping)", flush=True)
        return NewsletterExtractionBatch(items=[])

    if result.returncode != 0:
        print(f"    Claude CLI error: {result.stderr[:200]}", flush=True)
        return NewsletterExtractionBatch(items=[])

    try:
        response = json.loads(result.stdout)
        text_content = response.get("result", "")
    except json.JSONDecodeError:
        text_content = result.stdout

    # Extract JSON from the response text (it might have markdown fences)
    json_str = text_content
    if "```json" in json_str:
        json_str = json_str.split("```json", 1)[1].split("```", 1)[0]
    elif "```" in json_str:
        json_str = json_str.split("```", 1)[1].split("```", 1)[0]

    json_str = json_str.strip()
    if not json_str.startswith("{"):
        idx = json_str.find("{")
        if idx >= 0:
            json_str = json_str[idx:]

    try:
        data = json.loads(json_str)
        return NewsletterExtractionBatch.model_validate(data)
    except (json.JSONDecodeError, Exception) as e:
        print(f"    Failed to parse extraction response: {e}", flush=True)
        print(f"    Raw output (first 300 chars): {text_content[:300]}", flush=True)
        return NewsletterExtractionBatch(items=[])


def extract_newsletter_chunk_openai(
    subject: str,
    chunk_text: str,
    model: str | None = None,
) -> NewsletterExtractionBatch:
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not set.")

    prompt = NEWSLETTER_EXTRACTION_PROMPT.replace("SUBJECT_PLACEHOLDER", subject).replace("TEXT_PLACEHOLDER", chunk_text)
    client = OpenAI(api_key=api_key)
    response = client.responses.create(
        model=model or os.getenv("SCENE_WIKI_EXTRACTION_OPENAI_MODEL", "gpt-4.1-mini"),
        input=prompt,
        max_output_tokens=4000,
    )
    text_content = response.output_text.strip()
    if "```json" in text_content:
        text_content = text_content.split("```json", 1)[1].split("```", 1)[0].strip()
    elif "```" in text_content:
        text_content = text_content.split("```", 1)[1].split("```", 1)[0].strip()

    if not text_content.startswith("{"):
        start = text_content.find("{")
        end = text_content.rfind("}")
        if start >= 0 and end > start:
            text_content = text_content[start:end + 1]

    try:
        data = json.loads(text_content)
        return NewsletterExtractionBatch.model_validate(data)
    except (json.JSONDecodeError, Exception) as exc:
        print(f"    Failed to parse OpenAI extraction response: {exc}", flush=True)
        print(f"    Raw output (first 300 chars): {text_content[:300]}", flush=True)
        return NewsletterExtractionBatch(items=[])


def extract_newsletter_chunk(
    subject: str,
    chunk_text: str,
    model: str = "sonnet",
) -> NewsletterExtractionBatch:
    backend = os.getenv("SCENE_WIKI_EXTRACTION_BACKEND", "").strip().lower()
    if backend == "openai":
        return extract_newsletter_chunk_openai(subject=subject, chunk_text=chunk_text, model=None)
    if backend == "claude":
        return extract_newsletter_chunk_claude(subject=subject, chunk_text=chunk_text, model=model)

    if os.getenv("OPENAI_API_KEY", "").strip():
        return extract_newsletter_chunk_openai(subject=subject, chunk_text=chunk_text, model=None)
    return extract_newsletter_chunk_claude(subject=subject, chunk_text=chunk_text, model=model)


def _extract_single_chunk(
    chunk,
    doc_by_id: dict[str, NormalizedDocument],
    subject: str,
    model: str,
    chunks_dir: Optional[Path],
    index: int,
    total: int,
) -> dict:
    """Extract entities from a single chunk. Thread-safe."""
    chunk_path = chunks_dir / f"{chunk.chunk_id}.json" if chunks_dir else None

    # Check if already extracted (resume support)
    if chunk_path and chunk_path.exists():
        try:
            return json.loads(chunk_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, KeyError):
            pass

    batch = extract_newsletter_chunk(subject=subject, chunk_text=chunk.text, model=model)
    doc = doc_by_id.get(chunk.doc_id)
    published_at = doc.published_at if doc else None

    chunk_result = {
        "chunk_id": chunk.chunk_id,
        "doc_id": chunk.doc_id,
        "published_at": published_at,
        "entity_count": len(batch.items),
        "entities": [item.model_dump(mode="json") for item in batch.items],
    }

    # Save immediately to disk
    if chunk_path:
        write_json(chunk_path, chunk_result)

    print(f"  [{index + 1}/{total}] {chunk.doc_id} -> {len(batch.items)} entities", flush=True)
    return chunk_result


def extract_all_documents(
    documents: list[NormalizedDocument],
    subject: str,
    model: str = "sonnet",
    chunk_size: int = 2200,
    overlap: int = 250,
    output_dir: Optional[Path] = None,
    max_workers: int = 10,
) -> tuple[list[dict], list[CorpusEntityAggregate]]:
    """Extract entities from all documents using Claude CLI.

    Runs up to max_workers extractions in parallel.
    Saves each chunk result to disk immediately so progress survives crashes.
    Skips chunks that already have saved results (resume support).
    """
    chunks = chunk_documents(documents, chunk_size=chunk_size, overlap=overlap)
    doc_by_id = {doc.doc_id: doc for doc in documents}

    # Set up incremental save directory
    chunks_dir: Optional[Path] = None
    if output_dir:
        chunks_dir = output_dir / "extraction-chunks"
        chunks_dir.mkdir(parents=True, exist_ok=True)

    # Separate already-done from pending
    cached_results: list[dict] = []
    pending_chunks: list[tuple[int, object]] = []

    for i, chunk in enumerate(chunks):
        chunk_path = chunks_dir / f"{chunk.chunk_id}.json" if chunks_dir else None
        if chunk_path and chunk_path.exists():
            try:
                existing = json.loads(chunk_path.read_text(encoding="utf-8"))
                cached_results.append(existing)
                continue
            except (json.JSONDecodeError, KeyError):
                pass
        pending_chunks.append((i, chunk))

    print(f"  {len(cached_results)} chunks cached, {len(pending_chunks)} to extract (workers={max_workers})", flush=True)

    # Run pending chunks in parallel
    new_results: list[dict] = []
    if pending_chunks:
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(
                    _extract_single_chunk,
                    chunk, doc_by_id, subject, model, chunks_dir, idx, len(chunks),
                ): chunk.chunk_id
                for idx, chunk in pending_chunks
            }
            for future in as_completed(futures):
                try:
                    result = future.result()
                    new_results.append(result)
                except Exception as e:
                    chunk_id = futures[future]
                    print(f"  FAILED {chunk_id}: {e}", flush=True)

    per_chunk_results = cached_results + new_results
    print(f"  Done: {len(per_chunk_results)} total chunks", flush=True)

    # Aggregate all results
    aggregated = aggregate_chunk_results(per_chunk_results)
    return per_chunk_results, aggregated


def aggregate_chunk_results(per_chunk_results: list[dict]) -> list[CorpusEntityAggregate]:
    """Aggregate per-chunk extraction results into deduplicated entities."""
    grouped: dict[tuple[str, str], dict] = {}

    for chunk_result in per_chunk_results:
        doc_id = chunk_result["doc_id"]
        published_at = chunk_result.get("published_at")

        for entity_data in chunk_result.get("entities", []):
            entity = NewsletterEntity.model_validate(entity_data)
            key = (entity.category, entity.name.lower().strip())
            if key not in grouped:
                grouped[key] = {
                    "name": entity.name,
                    "category": entity.category,
                    "aliases": set(),
                    "evidence": [],
                    "confidence_values": [],
                    "post_ids": set(),
                    "dates": [],
                }
            record = grouped[key]
            if entity.evidence and entity.evidence not in record["evidence"]:
                record["evidence"].append(entity.evidence)
            for alias in entity.aliases:
                record["aliases"].add(alias)
            if entity.confidence is not None:
                record["confidence_values"].append(entity.confidence)
            record["post_ids"].add(doc_id)
            if published_at:
                date_part = published_at.split("T")[0] if "T" in published_at else published_at
                record["dates"].append(date_part)

    aggregated: list[CorpusEntityAggregate] = []
    for record in grouped.values():
        dates = sorted(set(record["dates"]))
        confidences = record["confidence_values"]
        aggregated.append(
            CorpusEntityAggregate(
                name=record["name"],
                category=record["category"],
                aliases=sorted(record["aliases"]),
                mention_count=len(record["post_ids"]),
                evidence=record["evidence"][:10],
                confidence=(sum(confidences) / len(confidences)) if confidences else None,
                post_ids=sorted(record["post_ids"]),
                first_seen=dates[0] if dates else None,
                last_seen=dates[-1] if dates else None,
            )
        )
    aggregated.sort(key=lambda e: (-e.mention_count, e.category, e.name.lower()))
    return aggregated
