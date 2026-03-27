from __future__ import annotations

import html
import re
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse


WHITESPACE_RE = re.compile(r"\s+")


class LinkExtractor(HTMLParser):
    def __init__(self, base_url: str) -> None:
        super().__init__()
        self.base_url = base_url
        self.links: list[tuple[str, str]] = []
        self._current_href: str | None = None
        self._current_text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "a":
            return
        attr_map = dict(attrs)
        href = attr_map.get("href")
        if href:
            self._current_href = urljoin(self.base_url, href)
            self._current_text = []

    def handle_data(self, data: str) -> None:
        if self._current_href is not None:
            self._current_text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag != "a" or self._current_href is None:
            return
        text = normalize_text(" ".join(self._current_text))
        self.links.append((self._current_href, text))
        self._current_href = None
        self._current_text = []


def normalize_text(value: str) -> str:
    return WHITESPACE_RE.sub(" ", value).strip()


def html_to_text(document: str) -> str:
    cleaned = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", document)
    cleaned = re.sub(r"(?i)<br\s*/?>", "\n", cleaned)
    cleaned = re.sub(r"(?i)</p>", "\n\n", cleaned)
    cleaned = re.sub(r"(?i)</div>", "\n", cleaned)
    cleaned = re.sub(r"(?s)<[^>]+>", " ", cleaned)
    cleaned = html.unescape(cleaned)
    return normalize_text(cleaned)


def extract_links(document: str, base_url: str) -> list[tuple[str, str]]:
    parser = LinkExtractor(base_url)
    parser.feed(document)
    return parser.links


def same_host(url_a: str, url_b: str) -> bool:
    host_a = (urlparse(url_a).hostname or "").lower().removeprefix("www.")
    host_b = (urlparse(url_b).hostname or "").lower().removeprefix("www.")
    return host_a == host_b
