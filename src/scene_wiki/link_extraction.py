from __future__ import annotations

import re
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

from pydantic import BaseModel, Field

from .html_tools import extract_links
from .storage import read_json, write_json


LINK_TYPE_DOMAINS: dict[str, str] = {
    "instagram.com": "instagram",
    "substack.com": "substack",
    "partiful.com": "event",
    "lu.ma": "event",
    "dice.fm": "event",
    "eventbrite.com": "event",
    "shotgun.live": "event",
    "ra.co": "event",
    "withfriends.co": "event",
    "youtube.com": "youtube",
    "youtu.be": "youtube",
    "spotify.com": "music",
    "bandcamp.com": "music",
    "soundcloud.com": "music",
    "apple.com": "music",
    "amazon.com": "book",
    "amazon.co.uk": "book",
    "bookshop.org": "book",
    "goodreads.com": "book",
    "letterboxd.com": "film",
    "imdb.com": "film",
    "metrograph.com": "venue",
    "filmforum.org": "venue",
    "google.com": "map",
    "maps.app.goo.gl": "map",
    "twitter.com": "social",
    "x.com": "social",
    "tiktok.com": "social",
    "linkedin.com": "social",
}

INSTAGRAM_HANDLE_RE = re.compile(r"instagram\.com/([a-zA-Z0-9_.]+)/?")
YOUTUBE_CHANNEL_RE = re.compile(r"youtube\.com/(?:@|channel/|c/)([^/?&]+)")


class ExtractedLink(BaseModel):
    url: str
    anchor_text: str
    domain: str
    link_type: str
    instagram_handle: Optional[str] = None
    youtube_handle: Optional[str] = None


class PostLinks(BaseModel):
    doc_id: str
    url: str
    links: list[ExtractedLink] = Field(default_factory=list)


def classify_link_type(domain: str, url: str) -> str:
    normalized = domain.lower().removeprefix("www.")
    for pattern, link_type in LINK_TYPE_DOMAINS.items():
        if normalized == pattern or normalized.endswith("." + pattern):
            return link_type
    # Check if it's a Substack subdomain
    if normalized.endswith(".substack.com"):
        return "substack"
    # Check for known music platforms by path
    if "music.apple.com" in url:
        return "music"
    return "website"


def extract_instagram_handle(url: str) -> str | None:
    match = INSTAGRAM_HANDLE_RE.search(url)
    if match:
        handle = match.group(1).lower()
        # Filter out non-profile pages
        if handle in {"p", "reel", "reels", "stories", "explore", "accounts", "about", "legal", "developer"}:
            return None
        return handle
    return None


def extract_youtube_handle(url: str) -> str | None:
    match = YOUTUBE_CHANNEL_RE.search(url)
    if match:
        return match.group(1)
    return None


def extract_links_from_html(doc_id: str, html_content: str, post_url: str) -> PostLinks:
    raw_links = extract_links(html_content, post_url)
    extracted: list[ExtractedLink] = []
    seen_urls: set[str] = set()

    for url, anchor_text in raw_links:
        # Skip internal anchors and empty URLs
        if not url or url.startswith("#") or url.startswith("javascript:"):
            continue
        # Skip the Substack subscribe/share/comment links
        parsed = urlparse(url)
        if not parsed.hostname:
            continue
        domain = parsed.hostname.lower().removeprefix("www.")

        # Deduplicate by URL
        normalized_url = url.rstrip("/")
        if normalized_url in seen_urls:
            continue
        seen_urls.add(normalized_url)

        # Skip self-referential Substack links (like share buttons, comments)
        if "substack.com" in domain:
            path = parsed.path.lower()
            if any(skip in path for skip in ["/subscribe", "/comments", "/api/", "/account/"]):
                continue
            # Also skip the post's own URL
            if post_url and urlparse(post_url).path.rstrip("/") == path.rstrip("/"):
                continue

        link_type = classify_link_type(domain, url)
        instagram_handle = extract_instagram_handle(url) if link_type == "instagram" else None
        youtube_handle = extract_youtube_handle(url) if link_type == "youtube" else None

        extracted.append(
            ExtractedLink(
                url=url,
                anchor_text=anchor_text.strip(),
                domain=domain,
                link_type=link_type,
                instagram_handle=instagram_handle,
                youtube_handle=youtube_handle,
            )
        )

    return PostLinks(doc_id=doc_id, url=post_url, links=extracted)


def extract_all_links(run_dir: Path) -> list[PostLinks]:
    html_dir = run_dir / "raw" / "html"
    if not html_dir.exists():
        return []

    manifest_path = run_dir / "raw" / "post-manifest.json"
    legacy_manifest_path = run_dir / "raw" / "collected-agenda-manifest.json"
    if not manifest_path.exists() and legacy_manifest_path.exists():
        manifest_path = legacy_manifest_path
    manifest = read_json(manifest_path) if manifest_path.exists() else []
    url_by_doc_id = {item["doc_id"]: item["url"] for item in manifest}

    all_post_links: list[PostLinks] = []
    for html_path in sorted(html_dir.glob("*.html")):
        doc_id = html_path.stem
        html_content = html_path.read_text(encoding="utf-8")
        post_url = url_by_doc_id.get(doc_id, "")
        post_links = extract_links_from_html(doc_id, html_content, post_url)
        all_post_links.append(post_links)

    return all_post_links


def save_link_manifests(run_dir: Path, all_post_links: list[PostLinks]) -> Path:
    links_dir = run_dir / "artifacts" / "links"
    links_dir.mkdir(parents=True, exist_ok=True)

    for post_links in all_post_links:
        write_json(links_dir / f"{post_links.doc_id}.json", post_links.model_dump(mode="json"))

    # Build aggregate summary
    total_links = 0
    by_type: dict[str, int] = {}
    by_domain: dict[str, int] = {}
    instagram_handles: set[str] = set()
    all_links_flat: list[dict] = []

    for post_links in all_post_links:
        for link in post_links.links:
            total_links += 1
            by_type[link.link_type] = by_type.get(link.link_type, 0) + 1
            by_domain[link.domain] = by_domain.get(link.domain, 0) + 1
            if link.instagram_handle:
                instagram_handles.add(link.instagram_handle)
            all_links_flat.append({
                "doc_id": post_links.doc_id,
                "url": link.url,
                "anchor_text": link.anchor_text,
                "domain": link.domain,
                "link_type": link.link_type,
                "instagram_handle": link.instagram_handle,
            })

    summary = {
        "total_links": total_links,
        "posts_with_links": sum(1 for pl in all_post_links if pl.links),
        "unique_instagram_handles": sorted(instagram_handles),
        "instagram_handle_count": len(instagram_handles),
        "by_type": dict(sorted(by_type.items(), key=lambda x: -x[1])),
        "by_domain": dict(sorted(by_domain.items(), key=lambda x: -x[1])[:30]),
    }
    write_json(links_dir / "link-summary.json", summary)
    write_json(links_dir / "all-links.json", all_links_flat)

    return links_dir
