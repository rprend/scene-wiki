from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any

from .scene_search import build_scene_search_assets
from .scene_wiki import build_scene_wiki


def _quartz_build_env() -> dict[str, str]:
    env = os.environ.copy()
    node_options = env.get("NODE_OPTIONS", "").strip()
    if "max-old-space-size" not in node_options:
        extra = env.get("SCENE_WIKI_NODE_MAX_OLD_SPACE_MB", "6144").strip()
        if extra:
            env["NODE_OPTIONS"] = (f"{node_options} --max-old-space-size={extra}").strip()
    return env


def _count_files(path: Path, suffix: str | None = None) -> int:
    if not path.exists():
        return 0
    if suffix is None:
        return sum(1 for child in path.rglob("*") if child.is_file())
    return sum(1 for child in path.rglob(f"*{suffix}") if child.is_file())


def build_quartz_site(
    wiki_dir: Path,
    output_dir: Path,
    *,
    concurrency: int = 3,
) -> None:
    env = _quartz_build_env()
    content_dir = wiki_dir / "content"
    markdown_files = _count_files(content_dir, ".md")
    total_files = _count_files(content_dir)
    print(
        "Quartz build config: "
        f"contentDir={content_dir} markdownFiles={markdown_files} totalFiles={total_files} "
        f"concurrency={concurrency} nodeOptions={env.get('NODE_OPTIONS', '')}",
        flush=True,
    )
    subprocess.run(
        [
            "node",
            "./quartz/bootstrap-cli.mjs",
            "build",
            "-d",
            "content",
            "-o",
            str(output_dir),
            "--concurrency",
            str(concurrency),
        ],
        cwd=wiki_dir,
        check=True,
        env=env,
    )


def bundle_frontend_assets(wiki_dir: Path, output_dir: Path) -> None:
    subprocess.run(
        ["node", str(wiki_dir / "custom" / "build-scene-search-bundles.mjs"), str(output_dir)],
        check=True,
        env=os.environ.copy(),
    )


def build_full_site(
    *,
    run_dir: Path,
    wiki_dir: Path,
    output_dir: Path,
    vault_dir: Path,
    quartz_concurrency: int = 3,
) -> dict[str, Any]:
    print("Preparing wiki content", flush=True)
    build_scene_wiki(run_dir=run_dir, site_dir=wiki_dir, vault_dir=vault_dir)
    content_dir = wiki_dir / "content"
    print(
        "Prepared wiki content tree: "
        f"contentDir={content_dir} markdownFiles={_count_files(content_dir, '.md')} totalFiles={_count_files(content_dir)}",
        flush=True,
    )
    print("Building Quartz site", flush=True)
    build_quartz_site(wiki_dir=wiki_dir, output_dir=output_dir, concurrency=quartz_concurrency)
    print("Building search assets", flush=True)
    build_scene_search_assets(run_dir=run_dir, output_dir=output_dir)
    print("Bundling frontend assets", flush=True)
    bundle_frontend_assets(wiki_dir=wiki_dir, output_dir=output_dir)
    return {
        "run_dir": str(run_dir),
        "wiki_dir": str(wiki_dir),
        "vault_dir": str(vault_dir),
        "output_dir": str(output_dir),
    }
