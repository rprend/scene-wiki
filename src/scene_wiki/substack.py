from __future__ import annotations

import json
import math
import os
import re
import time
from dataclasses import dataclass
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from .models import NormalizedDocument
from .newsletter_extraction import NEWSLETTER_EXTRACTION_PROMPT
from .storage import make_run, save_normalized_documents, write_json, write_text


ARCHIVE_PAGE_SIZE = 12
DEFAULT_TIMEOUT = 30.0
MAX_RETRIES = 6
USER_AGENT = "Mozilla/5.0 (compatible; scene-wiki/0.1; +https://example.invalid)"
DEFAULT_COST_SAMPLE_POSTS = 12


class BodyTextExtractor(HTMLParser):
    BLOCK_TAGS = {
        "address",
        "article",
        "blockquote",
        "br",
        "caption",
        "dd",
        "div",
        "dl",
        "dt",
        "figcaption",
        "figure",
        "footer",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "header",
        "hr",
        "li",
        "ol",
        "p",
        "pre",
        "section",
        "table",
        "td",
        "th",
        "tr",
        "ul",
    }

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.list_depth = 0
        self.skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style"}:
            self.skip_depth += 1
            return
        if self.skip_depth:
            return
        if tag in {"ul", "ol"}:
            self.list_depth += 1
            self._newline()
        elif tag == "li":
            self._newline()
            self.parts.append(f'{"  " * max(self.list_depth - 1, 0)}- ')
        elif tag == "br":
            self._newline()
        elif tag in self.BLOCK_TAGS:
            self._newline()

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style"} and self.skip_depth:
            self.skip_depth -= 1
            return
        if self.skip_depth:
            return
        if tag in {"ul", "ol"}:
            self.list_depth = max(self.list_depth - 1, 0)
            self._newline()
        elif tag == "li":
            self._newline()
        elif tag in self.BLOCK_TAGS:
            self._newline()

    def handle_data(self, data: str) -> None:
        if self.skip_depth:
            return
        text = re.sub(r"\s+", " ", data)
        if not text.strip():
            return
        if self.parts and not self.parts[-1].endswith(("\n", " ", "- ")):
            self.parts.append(" ")
        self.parts.append(text.strip())

    def text(self) -> str:
        raw = "".join(self.parts)
        raw = unescape(raw)
        raw = re.sub(r"[ \t]+\n", "\n", raw)
        raw = re.sub(r"\n{3,}", "\n\n", raw)
        return raw.strip()

    def _newline(self) -> None:
        if self.parts and not self.parts[-1].endswith("\n"):
            self.parts.append("\n")


@dataclass
class ExtractedPost:
    post_id: int
    slug: str
    title: str
    subtitle: str | None
    canonical_url: str
    published_at: str | None
    section_slug: str | None
    audience: str | None
    full_text_available: bool
    text: str
    body_html: str = ""


def slugify(value: str) -> str:
    text = re.sub(r"[^a-z0-9]+", "-", value.strip().lower())
    return text.strip("-") or "document"


def sanitize_text(text: str) -> str:
    return text.encode("utf-8", "ignore").decode("utf-8")


def _decode_preloads_json(html: str) -> dict[str, Any]:
    match = re.search(r'window\._preloads\s*=\s*JSON\.parse\("(.+?)"\)\s*</script>', html, re.DOTALL)
    if not match:
        raise ValueError("Could not locate window._preloads JSON")
    raw = match.group(1)
    decoded = bytes(raw, "utf-8").decode("unicode_escape")
    return json.loads(decoded)


def _extract_text_from_html(body_html: str) -> str:
    parser = BodyTextExtractor()
    parser.feed(body_html)
    parser.close()
    return sanitize_text(parser.text())


def get_with_retries(client: httpx.Client, url: str, **kwargs: Any) -> httpx.Response:
    last_error: Exception | None = None
    for attempt in range(MAX_RETRIES):
        try:
            response = client.get(url, **kwargs)
            if response.status_code == 429:
                retry_after = response.headers.get("Retry-After")
                sleep_seconds = float(retry_after) if retry_after and retry_after.isdigit() else min(2**attempt, 30)
                time.sleep(sleep_seconds)
                continue
            response.raise_for_status()
            return response
        except (httpx.HTTPError, httpx.TimeoutException) as exc:
            last_error = exc
            time.sleep(min(2**attempt, 30))
    if last_error is not None:
        raise last_error
    raise RuntimeError(f"Request failed without an exception for {url}")


def fetch_archive_posts(client: httpx.Client, archive_url: str, max_posts: int | None = None) -> list[dict[str, Any]]:
    base = archive_url.rstrip("/")
    all_posts: list[dict[str, Any]] = []
    seen_ids: set[int] = set()
    offset = 0

    while True:
        response = get_with_retries(
            client,
            f"{base}/api/v1/archive",
            params={"sort": "new", "search": "", "offset": offset, "limit": ARCHIVE_PAGE_SIZE},
        )
        batch = response.json()
        if not isinstance(batch, list) or not batch:
            break

        new_count = 0
        for item in batch:
            post_id = item.get("id")
            if isinstance(post_id, int) and post_id not in seen_ids:
                seen_ids.add(post_id)
                all_posts.append(item)
                new_count += 1
                if max_posts is not None and len(all_posts) >= max_posts:
                    return all_posts

        if len(batch) < ARCHIVE_PAGE_SIZE or new_count == 0:
            break

        offset += ARCHIVE_PAGE_SIZE
        time.sleep(0.5)

    return all_posts


def fetch_post_text(client: httpx.Client, url: str) -> ExtractedPost:
    response = get_with_retries(client, url)
    preloads = _decode_preloads_json(response.text)
    post = preloads["post"]
    body_html = post.get("body_html") or ""
    text = _extract_text_from_html(body_html)
    return ExtractedPost(
        post_id=post["id"],
        slug=post["slug"],
        title=post["title"],
        subtitle=sanitize_text(post.get("subtitle") or "") or None,
        canonical_url=post["canonical_url"],
        published_at=post.get("post_date"),
        section_slug=(post.get("section") or {}).get("slug"),
        audience=post.get("audience"),
        full_text_available='data-testid="paywall"' not in response.text,
        text=text,
        body_html=body_html,
    )


def _estimated_chunk_count(text_length: int, *, chunk_size: int = 2200, overlap: int = 250) -> int:
    if text_length <= 0:
        return 0
    if text_length <= chunk_size:
        return 1
    step = max(chunk_size - overlap, 1)
    return 1 + math.ceil((text_length - chunk_size) / step)


def _estimate_input_tokens(total_chunks: int, *, avg_chunk_chars: float, subject_name: str) -> int:
    prompt_without_text = NEWSLETTER_EXTRACTION_PROMPT.replace("SUBJECT_PLACEHOLDER", subject_name).replace(
        "TEXT_PLACEHOLDER",
        "",
    )
    avg_prompt_chars = len(prompt_without_text) + avg_chunk_chars
    return math.ceil((avg_prompt_chars * total_chunks) / 4)


def _select_sample_posts(posts: list[dict[str, Any]], sample_size: int) -> list[dict[str, Any]]:
    if sample_size <= 0 or len(posts) <= sample_size:
        return posts
    indices = {round(index * (len(posts) - 1) / (sample_size - 1)) for index in range(sample_size)}
    return [posts[index] for index in sorted(indices)]


def estimate_archive_cost(
    client: httpx.Client,
    posts: list[dict[str, Any]],
    *,
    subject_name: str,
    sample_size: int = DEFAULT_COST_SAMPLE_POSTS,
) -> tuple[dict[str, Any], dict[int, ExtractedPost]]:
    sampled_posts = _select_sample_posts(posts, sample_size)
    sampled_extracted: dict[int, ExtractedPost] = {}
    sample_lengths: list[int] = []
    sample_chunk_counts: list[int] = []

    for post in sampled_posts:
        extracted = fetch_post_text(client, post["canonical_url"])
        sampled_extracted[post["id"]] = extracted
        text_length = len(extracted.text)
        sample_lengths.append(text_length)
        sample_chunk_counts.append(_estimated_chunk_count(text_length))
        time.sleep(0.25)

    avg_text_chars = (sum(sample_lengths) / len(sample_lengths)) if sample_lengths else 0.0
    avg_chunks_per_post = (sum(sample_chunk_counts) / len(sample_chunk_counts)) if sample_chunk_counts else 0.0
    estimated_total_chunks = math.ceil(avg_chunks_per_post * len(posts))
    estimated_input_tokens = _estimate_input_tokens(
        estimated_total_chunks,
        avg_chunk_chars=min(avg_text_chars, 2200),
        subject_name=subject_name,
    )

    return (
        {
            "sampledPosts": len(sample_lengths),
            "averageTextCharacters": round(avg_text_chars),
            "averageChunksPerPost": round(avg_chunks_per_post, 2),
            "estimatedTotalChunks": estimated_total_chunks,
            "estimatedInputTokens": estimated_input_tokens,
            "largestSampleTextCharacters": max(sample_lengths, default=0),
        },
        sampled_extracted,
    )


def infer_subject(archive_url: str, section_slug: str | None = None) -> str:
    host = (urlparse(archive_url).hostname or "substack").split(".")[0].replace("-", " ").strip()
    base = " ".join(part.capitalize() for part in host.split())
    if section_slug:
        section = " ".join(part.capitalize() for part in section_slug.replace("-", " ").split())
        return f"{base} - {section}"
    return base or "Substack Archive"


def scrape_substack_archive(
    archive_url: str,
    *,
    subject: str | None = None,
    section_slug: str | None = None,
    max_articles: int | None = None,
    run_dir: Path | None = None,
) -> dict[str, Any]:
    parsed = urlparse(archive_url)
    if not parsed.scheme or not parsed.netloc:
        raise SystemExit(f"Invalid archive URL: {archive_url}")

    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Cache-Control": "max-age=0",
        "Upgrade-Insecure-Requests": "1",
    }
    if os.getenv("SUBSTACK_COOKIE"):
        headers["Cookie"] = os.environ["SUBSTACK_COOKIE"]
        headers["Referer"] = f"{archive_url.rstrip('/')}/account/cancel"

    subject_name = subject or infer_subject(archive_url, section_slug=section_slug)

    with httpx.Client(headers=headers, follow_redirects=True, timeout=DEFAULT_TIMEOUT) as client:
        archive_posts = fetch_archive_posts(client, archive_url, max_posts=max_articles)
        filtered_posts = archive_posts
        if section_slug:
            filtered_posts = [post for post in archive_posts if post.get("section_slug") == section_slug]
        if max_articles is not None:
            filtered_posts = filtered_posts[:max_articles]

        sample_size = int(os.getenv("SCENE_WIKI_COST_SAMPLE_POSTS", str(DEFAULT_COST_SAMPLE_POSTS)))
        cost_estimate, sampled_extracted = estimate_archive_cost(
            client,
            filtered_posts,
            subject_name=subject_name,
            sample_size=sample_size,
        )
        max_estimated_input_tokens = int(os.getenv("SCENE_WIKI_MAX_ESTIMATED_INPUT_TOKENS", "0") or "0")
        if max_estimated_input_tokens and cost_estimate["estimatedInputTokens"] > max_estimated_input_tokens:
            raise SystemExit(
                "Estimated extraction cost too high for this archive: "
                f"{cost_estimate['estimatedInputTokens']:,} input tokens across about "
                f"{cost_estimate['estimatedTotalChunks']:,} chunks and {len(filtered_posts):,} posts. "
                f"Cap is {max_estimated_input_tokens:,} input tokens."
            )

        extracted_posts: list[ExtractedPost] = []
        for post in filtered_posts:
            extracted = sampled_extracted.get(post["id"])
            if extracted is None:
                extracted = fetch_post_text(client, post["canonical_url"])
            extracted_posts.append(extracted)
            time.sleep(1.0)

    if run_dir is None:
        run_dir, metadata = make_run(
            source="browser",
            subject=subject_name,
            provider="substack-public-web",
            notes=f"Scraped from {archive_url}; section={section_slug or 'all'}",
        )
    else:
        run_dir = run_dir.resolve()
        for child in ("raw", "normalized", "artifacts"):
            (run_dir / child).mkdir(parents=True, exist_ok=True)
        metadata = {
            "run_id": run_dir.name,
            "subject": subject_name,
        }
        write_json(
            run_dir / "metadata.json",
            {
                "run_id": run_dir.name,
                "source": "browser",
                "subject": subject_name,
                "provider": "substack-public-web",
                "notes": f"Scraped from {archive_url}; section={section_slug or 'all'}",
            },
        )

    manifest: list[dict[str, Any]] = []
    documents: list[NormalizedDocument] = []

    for index, post in enumerate(sorted(extracted_posts, key=lambda item: item.published_at or "")):
        body_kind = "full" if post.full_text_available else "preview"
        date_prefix = (post.published_at or "").split("T", 1)[0] or f"post-{index:03d}"
        doc_id = f"{date_prefix}_{slugify(post.slug)}_{body_kind}"
        manifest.append(
            {
                "doc_id": doc_id,
                "post_id": post.post_id,
                "slug": post.slug,
                "title": sanitize_text(post.title),
                "subtitle": post.subtitle,
                "url": post.canonical_url,
                "published_at": post.published_at,
                "section_slug": post.section_slug,
                "audience": post.audience,
                "body_kind": body_kind,
                "text_length": len(post.text),
            }
        )
        documents.append(
            NormalizedDocument(
                doc_id=doc_id,
                title=sanitize_text(post.title),
                source="browser",
                text=sanitize_text(post.text),
                url=post.canonical_url,
                published_at=post.published_at,
            )
        )

    write_json(run_dir / "raw" / "archive-posts.json", archive_posts)
    write_json(run_dir / "raw" / "post-manifest.json", manifest)
    save_normalized_documents(run_dir, documents)

    html_dir = run_dir / "raw" / "html"
    html_dir.mkdir(parents=True, exist_ok=True)
    for index, post in enumerate(sorted(extracted_posts, key=lambda item: item.published_at or "")):
        body_kind = "full" if post.full_text_available else "preview"
        date_prefix = (post.published_at or "").split("T", 1)[0] or f"post-{index:03d}"
        doc_id = f"{date_prefix}_{slugify(post.slug)}_{body_kind}"
        if post.body_html:
            safe_html = post.body_html.encode("utf-8", "replace").decode("utf-8")
            write_text(html_dir / f"{doc_id}.html", safe_html)

    summary = {
        "run_id": metadata["run_id"] if isinstance(metadata, dict) else metadata.run_id,
        "run_dir": str(run_dir),
        "archive_url": archive_url,
        "section_slug": section_slug,
        "archive_posts_seen": len(archive_posts),
        "archive_posts_selected": len(filtered_posts),
        "posts_saved": len(documents),
        "total_text_characters": sum(item["text_length"] for item in manifest),
        "full_posts": sum(1 for item in manifest if item["body_kind"] == "full"),
        "preview_only_posts": sum(1 for item in manifest if item["body_kind"] == "preview"),
        "estimated_input_tokens": cost_estimate["estimatedInputTokens"],
        "estimated_total_chunks": cost_estimate["estimatedTotalChunks"],
        "average_text_characters": cost_estimate["averageTextCharacters"],
        "average_chunks_per_post": cost_estimate["averageChunksPerPost"],
        "sampled_posts": cost_estimate["sampledPosts"],
        "largest_sample_text_characters": cost_estimate["largestSampleTextCharacters"],
    }
    write_json(run_dir / "artifacts" / "summary.json", summary)

    return {
        **summary,
        "subject": subject_name,
        "post_count": len(documents),
    }
