from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any

from .scene_search import build_scene_search_assets
from .scene_wiki import build_scene_wiki


def build_quartz_site(
    wiki_dir: Path,
    output_dir: Path,
    *,
    concurrency: int = 3,
) -> None:
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
        env=os.environ.copy(),
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
