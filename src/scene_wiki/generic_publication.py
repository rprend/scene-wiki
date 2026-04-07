from __future__ import annotations

import email.utils
import json
import os
import re
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx

from .models import NormalizedDocument
from .newsletter_extraction import NEWSLETTER_EXTRACTION_PROMPT
from .storage import make_run, save_normalized_documents, write_json, write_text
from .substack import (
    DEFAULT_COST_SAMPLE_POSTS,
    DEFAULT_TIMEOUT,
    USER_AGENT,
    _estimate_input_tokens,
    _estimated_chunk_count,
    sanitize_text,
    slugify,
)


PREFERRED_CONTAINER_HINTS = (
    "transcript",
    "entry-content",
    "post-content",
    "article-content",
    "episode-content",
    "generic__content",
    "content-body",
    "main-content",
)


@dataclass
class PublicationPost:
    url: str
    title: str
    published_at: str | None
    text: str
    body_html: str = ""


class LinkCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[tuple[str, dict[str, str]]] = []
        self.feed_links: list[str] = []
        self.title_parts: list[str] = []
        self.in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_dict = {key: value or "" for key, value in attrs}
        if tag == "a":
            href = attrs_dict.get("href", "").strip()
            if href:
                self.links.append((href, attrs_dict))
        elif tag == "link":
            rel = attrs_dict.get("rel", "").lower()
            content_type = attrs_dict.get("type", "").lower()
            href = attrs_dict.get("href", "").strip()
            if href and "alternate" in rel and ("rss" in content_type or "atom" in content_type or "xml" in content_type):
                self.feed_links.append(href)
        elif tag == "title":
            self.in_title = True

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self.in_title = False

    def handle_data(self, data: str) -> None:
        if self.in_title and data.strip():
            self.title_parts.append(data.strip())


class CandidateContentExtractor(HTMLParser):
    BLOCK_TAGS = {
        "address",
        "article",
        "blockquote",
        "br",
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
        "main",
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
        self.skip_depth = 0
        self.candidate_stack: list[dict[str, Any]] = []
        self.completed: list[tuple[int, str]] = []
        self.full_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_dict = {key: value or "" for key, value in attrs}
        if tag in {"script", "style", "noscript"}:
            self.skip_depth += 1
            return
        if self.skip_depth:
            return

        candidate_score = self._candidate_score(tag, attrs_dict)
        if candidate_score > 0:
            self.candidate_stack.append({"score": candidate_score, "parts": []})

        if tag in self.BLOCK_TAGS:
            self._newline(self.full_parts)
            for candidate in self.candidate_stack:
                self._newline(candidate["parts"])
        elif tag == "br":
            self._newline(self.full_parts)
            for candidate in self.candidate_stack:
                self._newline(candidate["parts"])

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript"} and self.skip_depth:
            self.skip_depth -= 1
            return
        if self.skip_depth:
            return
        if tag in self.BLOCK_TAGS:
            self._newline(self.full_parts)
            for candidate in self.candidate_stack:
                self._newline(candidate["parts"])
        if self.candidate_stack and tag in {"article", "main", "section", "div"}:
            candidate = self.candidate_stack.pop()
            text = self._collapse(candidate["parts"])
            if text:
                self.completed.append((candidate["score"], text))

    def handle_data(self, data: str) -> None:
        if self.skip_depth:
            return
        text = re.sub(r"\s+", " ", data)
        if not text.strip():
            return
        clean = text.strip()
        self._append_text(self.full_parts, clean)
        for candidate in self.candidate_stack:
            self._append_text(candidate["parts"], clean)

    def best_text(self) -> str:
        if self.completed:
            best = max(self.completed, key=lambda item: (len(item[1]) * item[0], len(item[1])))
            if len(best[1]) >= 800:
                return sanitize_text(best[1])
        return sanitize_text(self._collapse(self.full_parts))

    def _candidate_score(self, tag: str, attrs: dict[str, str]) -> int:
        score = 0
        attr_text = f"{attrs.get('class', '')} {attrs.get('id', '')}".lower()
        if tag == "article":
            score += 8
        elif tag == "main":
            score += 6
        elif tag == "section":
            score += 3
        elif tag == "div":
            score += 2
        for hint in PREFERRED_CONTAINER_HINTS:
            if hint in attr_text:
                score += 8
        if any(token in attr_text for token in ("content", "entry", "post", "article", "episode", "transcript", "body")):
            score += 3
        if any(token in attr_text for token in ("footer", "header", "nav", "menu", "sidebar")):
            score -= 6
        return max(score, 0)

    def _append_text(self, parts: list[str], text: str) -> None:
        if parts and not parts[-1].endswith(("\n", " ")):
            parts.append(" ")
        parts.append(text)

    def _newline(self, parts: list[str]) -> None:
        if parts and not parts[-1].endswith("\n"):
            parts.append("\n")

    def _collapse(self, parts: list[str]) -> str:
        text = unescape("".join(parts))
        text = re.sub(r"[ \t]+\n", "\n", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()


def normalize_publication_title(title: str) -> str:
    clean = re.sub(r"\s+", " ", unescape(title)).strip()
    if " | " in clean:
        parts = [part.strip() for part in clean.split("|") if part.strip()]
        if len(parts) >= 2:
            return parts[-1]
    if " – " in clean:
        parts = [part.strip() for part in clean.split(" – ") if part.strip()]
        if len(parts) >= 2 and parts[0].lower() in {"episodes", "posts", "archive", "blog"}:
            return parts[-1]
    if " - " in clean:
        parts = [part.strip() for part in clean.split(" - ") if part.strip()]
        if len(parts) >= 2 and parts[0].lower() in {"episodes", "posts", "archive", "blog"}:
            return parts[-1]
    return clean


def feed_candidate_urls(source_url: str, html: str) -> list[str]:
    collector = LinkCollector()
    collector.feed(html)
    parsed = urlparse(source_url)
    base_root = f"{parsed.scheme}://{parsed.netloc}"
    path = parsed.path.rstrip("/")
    candidates = [urljoin(source_url, href) for href in collector.feed_links]
    if path:
        candidates.append(urljoin(base_root, f"{path}/feed/"))
    candidates.extend(
        [
            urljoin(source_url, "feed/"),
            urljoin(base_root, "/feed/"),
            urljoin(base_root, "/rss/"),
            urljoin(base_root, "/rss.xml"),
        ]
    )
    seen: set[str] = set()
    unique: list[str] = []
    for candidate in candidates:
        if candidate not in seen:
            seen.add(candidate)
            unique.append(candidate)
    return unique


def parse_feed(xml_text: str) -> tuple[str | None, list[dict[str, Any]]]:
    root = ET.fromstring(xml_text)
    channel = root.find("channel")
    if channel is not None:
        title = channel.findtext("title")
        items = []
        for item in channel.findall("item"):
            items.append(
                {
                    "title": (item.findtext("title") or "").strip(),
                    "link": (item.findtext("link") or "").strip(),
                    "published_at": _parse_feed_datetime(item.findtext("pubDate")),
                }
            )
        return title, [item for item in items if item["link"]]

    namespace = {"atom": "http://www.w3.org/2005/Atom"}
    if root.tag.endswith("feed"):
        title = root.findtext("atom:title", default="", namespaces=namespace)
        items = []
        for entry in root.findall("atom:entry", namespace):
            link = ""
            link_node = entry.find("atom:link", namespace)
            if link_node is not None:
                link = (link_node.attrib.get("href") or "").strip()
            items.append(
                {
                    "title": (entry.findtext("atom:title", default="", namespaces=namespace) or "").strip(),
                    "link": link,
                    "published_at": _parse_feed_datetime(
                        entry.findtext("atom:updated", default="", namespaces=namespace)
                        or entry.findtext("atom:published", default="", namespaces=namespace)
                    ),
                }
            )
        return title, [item for item in items if item["link"]]

    return None, []


def _parse_feed_datetime(value: str | None) -> str | None:
    if not value:
        return None
    try:
        dt = email.utils.parsedate_to_datetime(value)
        return dt.isoformat()
    except Exception:
        return value


def discover_publication(
    client: httpx.Client,
    source_url: str,
    *,
    require_parseable_sample: bool = True,
) -> dict[str, Any]:
    response = client.get(source_url)
    response.raise_for_status()
    html = response.text
    collector = LinkCollector()
    collector.feed(html)
    page_title = normalize_publication_title(" ".join(collector.title_parts)) if collector.title_parts else None
    best_feed_url = None
    best_feed_title = None
    best_feed_items: list[dict[str, Any]] = []

    for candidate in feed_candidate_urls(source_url, html):
        try:
            feed_response = client.get(candidate)
            if not feed_response.is_success:
                continue
            feed_title, items = parse_feed(feed_response.text)
            if len(items) > len(best_feed_items):
                best_feed_url = candidate
                best_feed_title = feed_title
                best_feed_items = items
        except Exception:
            continue

    archive_posts = discover_archive_posts(client, source_url, max_posts=24)
    if len(archive_posts) > len(best_feed_items):
        sample_posts = archive_posts
    else:
        sample_posts = best_feed_items

    if len(sample_posts) < 3:
        raise RuntimeError("Could not discover a usable publication post list from this site.")

    sample_post = sample_posts[0]
    parsed = fetch_generic_post(client, sample_post["link"])
    if require_parseable_sample and len(parsed.text) < 1200:
        raise RuntimeError("A sample post was reachable, but its main body did not parse cleanly.")

    return {
        "title": normalize_publication_title(best_feed_title or page_title or urlparse(source_url).hostname or "Publication"),
        "feedUrl": best_feed_url,
        "samplePosts": sample_posts,
        "sampleTextCharacters": len(parsed.text),
    }


def discover_archive_posts(client: httpx.Client, source_url: str, *, max_posts: int | None = None) -> list[dict[str, Any]]:
    parsed = urlparse(source_url)
    source_path = parsed.path.rstrip("/")
    base_prefix = f"{parsed.scheme}://{parsed.netloc}"
    queue = [source_url]
    seen_pages: set[str] = set()
    collected: list[dict[str, Any]] = []
    seen_links: set[str] = set()

    while queue:
        page_url = queue.pop(0)
        if page_url in seen_pages:
            continue
        seen_pages.add(page_url)
        response = client.get(page_url)
        response.raise_for_status()
        collector = LinkCollector()
        collector.feed(response.text)

        for href, _attrs in collector.links:
            absolute = urljoin(page_url, href)
            parsed_link = urlparse(absolute)
            if parsed_link.netloc != parsed.netloc:
                continue
            normalized = f"{parsed_link.scheme}://{parsed_link.netloc}{parsed_link.path}"
            path = parsed_link.path.rstrip("/")
            if not path:
                continue
            if path.startswith(f"{source_path}/page/") and normalized not in seen_pages and normalized not in queue:
                queue.append(normalized)
                continue
            if path.endswith("/feed") or path.endswith("/feed/"):
                continue
            if not path.startswith(f"{source_path}/"):
                continue
            suffix = path[len(source_path) + 1 :]
            if not suffix or "/" in suffix:
                continue
            if normalized in seen_links:
                continue
            seen_links.add(normalized)
            collected.append({"title": suffix.replace("-", " ").strip(), "link": normalized, "published_at": None})
            if max_posts is not None and len(collected) >= max_posts:
                return collected

    return collected


def fetch_generic_post(client: httpx.Client, url: str) -> PublicationPost:
    response = client.get(url)
    response.raise_for_status()
    html = response.text
    extractor = CandidateContentExtractor()
    extractor.feed(html)
    text = extractor.best_text()
    title = _extract_page_title(html) or slugify(urlparse(url).path.split("/")[-1]).replace("-", " ").title()
    published_at = _extract_published_at(html)
    return PublicationPost(
        url=url,
        title=title,
        published_at=published_at,
        text=text,
        body_html=html,
    )


def _extract_page_title(html: str) -> str | None:
    for pattern in (
        r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']+)["\']',
        r'<meta[^>]+name=["\']twitter:title["\'][^>]+content=["\']([^"\']+)["\']',
        r"<title>([^<]+)</title>",
    ):
        match = re.search(pattern, html, re.IGNORECASE)
        if match:
            return normalize_publication_title(unescape(match.group(1)).strip())
    return None


def _extract_published_at(html: str) -> str | None:
    for pattern in (
        r'<meta[^>]+property=["\']article:published_time["\'][^>]+content=["\']([^"\']+)["\']',
        r'<time[^>]+datetime=["\']([^"\']+)["\']',
    ):
        match = re.search(pattern, html, re.IGNORECASE)
        if match:
            return match.group(1).strip()
    return None


def infer_publication_subject(source_url: str, title: str | None = None) -> str:
    if title:
        return normalize_publication_title(title)
    host = (urlparse(source_url).hostname or "publication").replace("www.", "")
    return " ".join(part.capitalize() for part in re.split(r"[.-]+", host) if part)


def scrape_generic_publication(
    source_url: str,
    *,
    subject: str | None = None,
    max_articles: int | None = None,
    run_dir: Path | None = None,
) -> dict[str, Any]:
    parsed = urlparse(source_url)
    if not parsed.scheme or not parsed.netloc:
        raise SystemExit(f"Invalid publication URL: {source_url}")

    subject_name = subject or infer_publication_subject(source_url)
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }
    with httpx.Client(headers=headers, follow_redirects=True, timeout=DEFAULT_TIMEOUT) as client:
        discovery = discover_publication(client, source_url)
        subject_name = subject or infer_publication_subject(source_url, discovery["title"])
        archive_posts = discover_archive_posts(client, source_url, max_posts=max_articles)
        if not archive_posts and discovery["feedUrl"]:
            archive_posts = discovery["samplePosts"]
        if not archive_posts:
            raise SystemExit("Could not discover article links for this publication.")
        if max_articles is not None:
            archive_posts = archive_posts[:max_articles]

        sample_size = int(os.getenv("SCENE_WIKI_COST_SAMPLE_POSTS", str(DEFAULT_COST_SAMPLE_POSTS)))
        sampled_posts = archive_posts if len(archive_posts) <= sample_size else archive_posts[:sample_size]
        sampled_extracted: dict[str, PublicationPost] = {}
        sample_lengths: list[int] = []
        sample_chunk_counts: list[int] = []
        for index, post in enumerate(sampled_posts, start=1):
            print(f"Sampling post {index}/{len(sampled_posts)} for cost estimate: {post['link']}", flush=True)
            extracted = fetch_generic_post(client, post["link"])
            sampled_extracted[post["link"]] = extracted
            sample_lengths.append(len(extracted.text))
            sample_chunk_counts.append(_estimated_chunk_count(len(extracted.text)))
            time.sleep(0.25)

        avg_text_chars = (sum(sample_lengths) / len(sample_lengths)) if sample_lengths else 0.0
        avg_chunks_per_post = (sum(sample_chunk_counts) / len(sample_chunk_counts)) if sample_chunk_counts else 0.0
        estimated_total_chunks = int(round(avg_chunks_per_post * len(archive_posts)))
        estimated_input_tokens = _estimate_input_tokens(
            estimated_total_chunks,
            avg_chunk_chars=min(avg_text_chars, 2200),
            subject_name=subject_name,
        )
        max_estimated_input_tokens = int(os.getenv("SCENE_WIKI_MAX_ESTIMATED_INPUT_TOKENS", "0") or "0")
        if max_estimated_input_tokens and estimated_input_tokens > max_estimated_input_tokens:
            raise SystemExit(
                "Preflight budget check stopped this run before analysis started. "
                f"This publication is estimated at {estimated_input_tokens:,} input tokens "
                f"across about {estimated_total_chunks:,} chunks and {len(archive_posts):,} posts, "
                f"which is above the current cap of {max_estimated_input_tokens:,} input tokens. "
                "Raise SCENE_WIKI_MAX_ESTIMATED_INPUT_TOKENS or run a smaller slice of the archive to continue."
            )

        extracted_posts: list[PublicationPost] = []
        for index, post in enumerate(archive_posts, start=1):
            extracted = sampled_extracted.get(post["link"])
            if extracted is None:
                print(f"Fetching publication post {index}/{len(archive_posts)}: {post['link']}", flush=True)
                extracted = fetch_generic_post(client, post["link"])
            else:
                print(f"Reusing sampled post {index}/{len(archive_posts)}: {post['link']}", flush=True)
            if len(extracted.text) < 500:
                raise SystemExit(f"Post body did not parse cleanly for {post['link']}")
            extracted_posts.append(extracted)
            time.sleep(0.25)

    if run_dir is None:
        run_dir, metadata = make_run(
            source="browser",
            subject=subject_name,
            provider="generic-publication-web",
            notes=f"Scraped from {source_url}",
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
                "provider": "generic-publication-web",
                "notes": f"Scraped from {source_url}",
            },
        )

    manifest: list[dict[str, Any]] = []
    documents: list[NormalizedDocument] = []
    sorted_posts = sorted(extracted_posts, key=lambda item: item.published_at or item.url)
    for index, post in enumerate(sorted_posts):
        date_prefix = (post.published_at or "").split("T", 1)[0] or f"post-{index:03d}"
        slug = slugify(urlparse(post.url).path.strip("/").split("/")[-1])
        doc_id = f"{date_prefix}_{slug}_full"
        manifest.append(
            {
                "doc_id": doc_id,
                "slug": slug,
                "title": sanitize_text(post.title),
                "url": post.url,
                "published_at": post.published_at,
                "body_kind": "full",
                "text_length": len(post.text),
            }
        )
        documents.append(
            NormalizedDocument(
                doc_id=doc_id,
                title=sanitize_text(post.title),
                source="browser",
                text=sanitize_text(post.text),
                url=post.url,
                published_at=post.published_at,
            )
        )

    write_json(run_dir / "raw" / "archive-posts.json", archive_posts)
    write_json(run_dir / "raw" / "post-manifest.json", manifest)
    save_normalized_documents(run_dir, documents)

    html_dir = run_dir / "raw" / "html"
    html_dir.mkdir(parents=True, exist_ok=True)
    for index, post in enumerate(sorted_posts):
        date_prefix = (post.published_at or "").split("T", 1)[0] or f"post-{index:03d}"
        slug = slugify(urlparse(post.url).path.strip("/").split("/")[-1])
        doc_id = f"{date_prefix}_{slug}_full"
        write_text(html_dir / f"{doc_id}.html", post.body_html)

    summary = {
        "run_id": metadata["run_id"] if isinstance(metadata, dict) else metadata.run_id,
        "run_dir": str(run_dir),
        "archive_url": source_url,
        "archive_posts_seen": len(archive_posts),
        "archive_posts_selected": len(archive_posts),
        "posts_saved": len(documents),
        "total_text_characters": sum(item["text_length"] for item in manifest),
        "full_posts": len(documents),
        "preview_only_posts": 0,
        "estimated_input_tokens": estimated_input_tokens,
        "estimated_total_chunks": estimated_total_chunks,
        "average_text_characters": round(avg_text_chars),
        "average_chunks_per_post": round(avg_chunks_per_post, 2),
        "sampled_posts": len(sample_lengths),
        "largest_sample_text_characters": max(sample_lengths, default=0),
    }
    write_json(run_dir / "artifacts" / "summary.json", summary)

    return {
        **summary,
        "subject": subject_name,
        "archive_posts": archive_posts,
        "feed_url": discovery.get("feedUrl"),
        "discovered_title": discovery.get("title"),
    }
