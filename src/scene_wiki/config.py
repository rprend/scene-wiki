from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv


def _discover_project_root() -> Path:
    override = os.getenv("SCENE_WIKI_ROOT")
    if override:
        return Path(override).expanduser().resolve()

    candidates = [Path.cwd(), *Path.cwd().parents, Path(__file__).resolve(), *Path(__file__).resolve().parents]
    for candidate in candidates:
        if candidate.is_file():
            candidate = candidate.parent
        if (candidate / "pyproject.toml").exists() and (candidate / "src" / "scene_wiki").exists():
            return candidate.resolve()
    return Path(__file__).resolve().parents[2]


def _load_env_files(project_root: Path) -> None:
    load_dotenv()
    load_dotenv(project_root / ".dev.vars")
    load_dotenv(project_root / ".env")


@dataclass(frozen=True)
class Settings:
    project_root: Path
    data_root: Path
    openai_api_key: Optional[str]
    gemini_api_key: Optional[str]
    google_api_key: Optional[str]
    exa_api_key: Optional[str]
    x_consumer_key: Optional[str]
    x_consumer_secret: Optional[str]
    x_bearer_token: Optional[str]
    browser_api_key: Optional[str]
    browser_session_mode: Optional[str]
    youtube_cookies_browser: Optional[str]
    youtube_fetch_hop_url: Optional[str]
    youtube_proxy_url: Optional[str]
    youtube_po_token: Optional[str]
    spotify_client_id: Optional[str]
    spotify_client_secret: Optional[str]
    spotify_redirect_uri: Optional[str]


def get_settings() -> Settings:
    project_root = _discover_project_root()
    _load_env_files(project_root)
    data_root = project_root / "data"
    data_root.mkdir(exist_ok=True)
    return Settings(
        project_root=project_root,
        data_root=data_root,
        openai_api_key=os.getenv("OPENAI_API_KEY"),
        gemini_api_key=os.getenv("GEMINI_API_KEY"),
        google_api_key=os.getenv("GOOGLE_API_KEY"),
        exa_api_key=os.getenv("EXA_API_KEY"),
        x_consumer_key=os.getenv("X_CONSUMER_KEY"),
        x_consumer_secret=os.getenv("X_CONSUMER_SECRET"),
        x_bearer_token=os.getenv("X_BEARER_TOKEN"),
        browser_api_key=os.getenv("BROWSER_API_KEY"),
        browser_session_mode=os.getenv("BROWSER_SESSION_MODE"),
        youtube_cookies_browser=os.getenv("YOUTUBE_COOKIES_BROWSER"),
        youtube_fetch_hop_url=os.getenv("YOUTUBE_FETCH_HOP_URL"),
        youtube_proxy_url=os.getenv("YOUTUBE_PROXY_URL"),
        youtube_po_token=os.getenv("YOUTUBE_PO_TOKEN"),
        spotify_client_id=os.getenv("SPOTIFY_CLIENT_ID"),
        spotify_client_secret=os.getenv("SPOTIFY_CLIENT_SECRET"),
        spotify_redirect_uri=os.getenv("SPOTIFY_REDIRECT_URI"),
    )
