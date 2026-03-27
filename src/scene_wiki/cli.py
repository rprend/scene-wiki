from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Optional

import typer

from .newsletter_corpus import build_newsletter_corpus
from .scene_search import build_scene_search_assets
from .scene_wiki import build_obsidian_vault, build_scene_wiki, prepare_quartz_content
from .site import build_full_site
from .substack import scrape_substack_archive


app = typer.Typer(help="Generate an Obsidian-style Quartz wiki from a Substack archive.")


@app.command("scrape-substack")
def scrape_substack_command(
    archive_url: str,
    subject: Optional[str] = None,
    section_slug: Optional[str] = None,
    max_articles: Optional[int] = None,
    run_dir: Optional[Path] = None,
) -> None:
    typer.echo(f"Scraping Substack archive: {archive_url}")
    result = scrape_substack_archive(
        archive_url,
        subject=subject,
        section_slug=section_slug,
        max_articles=max_articles,
        run_dir=run_dir,
    )
    typer.echo(
        f"Saved {result['posts_saved']} posts from {result['archive_posts_selected']} selected archive entries "
        f"({result['total_text_characters']} chars) into {result['run_dir']}"
    )
    typer.echo(result["run_dir"])


@app.command("build-corpus")
def build_corpus_command(
    run_dir: Path,
    model: str = "sonnet",
    workers: int = 10,
    skip_llm: bool = False,
) -> None:
    result = build_newsletter_corpus(run_dir=run_dir, model=model, workers=workers, skip_llm=skip_llm)
    typer.echo(result["output_path"])


@app.command("build-vault")
def build_vault_command(run_dir: Path, vault_dir: Path) -> None:
    result = build_obsidian_vault(run_dir=run_dir, vault_dir=vault_dir)
    typer.echo(result["vault_dir"])


@app.command("prepare-content")
def prepare_content_command(vault_dir: Path, content_dir: Path) -> None:
    result = prepare_quartz_content(vault_dir=vault_dir, content_dir=content_dir)
    typer.echo(result["content_dir"])


@app.command("build-search-assets")
def build_search_assets_command(run_dir: Path, output_dir: Path) -> None:
    result = build_scene_search_assets(run_dir=run_dir, output_dir=output_dir)
    typer.echo(result["output_dir"])


@app.command("build-wiki")
def build_wiki_command(run_dir: Path, site_dir: Path, vault_dir: Optional[Path] = None) -> None:
    result = build_scene_wiki(run_dir=run_dir, site_dir=site_dir, vault_dir=vault_dir)
    typer.echo(result["site_dir"])


@app.command("build-substack")
def build_substack_command(
    archive_url: str,
    subject: Optional[str] = None,
    section_slug: Optional[str] = None,
    max_articles: Optional[int] = None,
    model: str = "sonnet",
    workers: int = 10,
    quartz_concurrency: int = 3,
    run_dir: Optional[Path] = None,
    vault_dir: Optional[Path] = None,
    wiki_dir: Path = Path("wiki"),
    output_dir: Path = Path("dist/wiki"),
    site_title: Optional[str] = None,
) -> None:
    if site_title:
        import os

        os.environ["SCENE_WIKI_TITLE"] = site_title
        os.environ["QUARTZ_PAGE_TITLE"] = site_title

    typer.echo(f"Scraping Substack archive: {archive_url}")
    scrape_result = scrape_substack_archive(
        archive_url,
        subject=subject,
        section_slug=section_slug,
        max_articles=max_articles,
        run_dir=run_dir,
    )
    actual_run_dir = Path(scrape_result["run_dir"])
    actual_vault_dir = vault_dir or Path("vault") / actual_run_dir.name
    typer.echo(
        f"Saved {scrape_result['posts_saved']} posts from {scrape_result['archive_posts_selected']} selected archive entries "
        f"({scrape_result['total_text_characters']} chars) into {actual_run_dir}"
    )
    typer.echo(f"Building newsletter corpus in {actual_run_dir}")
    build_newsletter_corpus(run_dir=actual_run_dir, model=model, workers=workers)
    typer.echo(f"Building full site into {output_dir.resolve()}")
    result = build_full_site(
        run_dir=actual_run_dir,
        wiki_dir=wiki_dir.resolve(),
        output_dir=output_dir.resolve(),
        vault_dir=actual_vault_dir.resolve(),
        quartz_concurrency=quartz_concurrency,
    )
    typer.echo(f"Site build complete: {result['output_dir']}")
    typer.echo(result["output_dir"])


@app.command("deploy")
def deploy_command(
    project_name: str,
    output_dir: Path = Path("dist/wiki"),
) -> None:
    subprocess.run(
        [
            "npx",
            "wrangler",
            "pages",
            "deploy",
            str(output_dir.resolve()),
            "--project-name",
            project_name,
            "--commit-dirty=true",
        ],
        check=True,
    )


if __name__ == "__main__":
    app()
