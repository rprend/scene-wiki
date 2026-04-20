from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Optional
import shutil

import typer

from .generic_publication import scrape_generic_publication
from .mediawiki_export import build_mediawiki_export
from .newsletter_corpus import build_newsletter_corpus
from .scene_search import build_scene_search_assets
from .scene_wiki import build_obsidian_vault, build_scene_wiki, prepare_quartz_content
from .site import build_full_site
from .substack import scrape_substack_archive


app = typer.Typer(help="Generate Scene Wiki outputs from publication archives, including Quartz sites and MediaWiki exports.")


def _default_quartz_concurrency() -> int:
    raw = os.getenv("SCENE_WIKI_QUARTZ_CONCURRENCY", "1").strip()
    try:
        return max(1, int(raw))
    except ValueError:
        return 1


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
    typer.echo(
        f"Estimated extraction: {result['estimated_total_chunks']} chunks / "
        f"{result['estimated_input_tokens']} input tokens"
    )
    typer.echo(result["run_dir"])


@app.command("scrape-publication")
def scrape_publication_command(
    source_url: str,
    subject: Optional[str] = None,
    max_articles: Optional[int] = None,
    run_dir: Optional[Path] = None,
) -> None:
    typer.echo(f"Scraping publication archive: {source_url}")
    result = scrape_generic_publication(
        source_url,
        subject=subject,
        max_articles=max_articles,
        run_dir=run_dir,
    )
    typer.echo(
        f"Saved {result['posts_saved']} posts from {result['archive_posts_selected']} selected publication entries "
        f"({result['total_text_characters']} chars) into {result['run_dir']}"
    )
    typer.echo(
        f"Estimated extraction: {result['estimated_total_chunks']} chunks / "
        f"{result['estimated_input_tokens']} input tokens"
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


@app.command("build-mediawiki-export")
def build_mediawiki_export_command(
    run_dir: Path,
    output_dir: Path = Path("dist/mediawiki"),
    site_title: Optional[str] = None,
    build_search: bool = True,
    wiki_dir: Path = Path("wiki"),
) -> None:
    typer.echo(f"Building MediaWiki export into {output_dir.resolve()}")
    result = build_mediawiki_export(
        run_dir=run_dir.resolve(),
        output_dir=output_dir.resolve(),
        site_title=site_title,
        build_search=build_search,
        wiki_dir=wiki_dir.resolve() if build_search else None,
    )
    typer.echo(
        f"MediaWiki export complete: {result['import_xml']} "
        f"({result['page_count']} pages, {result['entity_count']} entities, {result['issue_count']} issues)"
    )
    typer.echo(result["import_xml"])


@app.command("build-run")
def build_run_command(
    run_dir: Path,
    vault_dir: Path,
    wiki_dir: Path = Path("wiki"),
    output_dir: Path = Path("dist/wiki"),
    quartz_concurrency: int = _default_quartz_concurrency(),
    site_title: Optional[str] = None,
    reset_extraction: bool = False,
    reuse_quartz_output: bool = False,
) -> None:
    if site_title:
        import os

        os.environ["SCENE_WIKI_TITLE"] = site_title
        os.environ["QUARTZ_PAGE_TITLE"] = site_title

    run_dir = run_dir.resolve()
    if reset_extraction:
        chunks_dir = run_dir / "artifacts" / "extraction-chunks"
        if chunks_dir.exists():
            shutil.rmtree(chunks_dir)
        for artifact in (
            run_dir / "artifacts" / "extraction-chunks.json",
            run_dir / "artifacts" / "corpus.json",
        ):
            if artifact.exists():
                artifact.unlink()
        usage_dir = run_dir / "artifacts" / "openai-usage"
        if usage_dir.exists():
            shutil.rmtree(usage_dir)

    typer.echo(f"Building newsletter corpus in {run_dir}")
    build_newsletter_corpus(run_dir=run_dir)
    typer.echo(f"Building full site into {output_dir.resolve()}")
    result = build_full_site(
        run_dir=run_dir,
        wiki_dir=wiki_dir.resolve(),
        output_dir=output_dir.resolve(),
        vault_dir=vault_dir.resolve(),
        quartz_concurrency=quartz_concurrency,
        reuse_quartz_output=reuse_quartz_output,
    )
    typer.echo(f"Site build complete: {result['output_dir']}")
    typer.echo(result["output_dir"])


@app.command("build-substack")
def build_substack_command(
    archive_url: str,
    subject: Optional[str] = None,
    section_slug: Optional[str] = None,
    max_articles: Optional[int] = None,
    model: str = "sonnet",
    workers: int = 10,
    quartz_concurrency: int = _default_quartz_concurrency(),
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
    typer.echo(
        f"Estimated extraction: {scrape_result['estimated_total_chunks']} chunks / "
        f"{scrape_result['estimated_input_tokens']} input tokens"
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


@app.command("build-substack-mediawiki")
def build_substack_mediawiki_command(
    archive_url: str,
    subject: Optional[str] = None,
    section_slug: Optional[str] = None,
    max_articles: Optional[int] = None,
    model: str = "sonnet",
    workers: int = 10,
    run_dir: Optional[Path] = None,
    output_dir: Path = Path("dist/mediawiki"),
    wiki_dir: Path = Path("wiki"),
    site_title: Optional[str] = None,
) -> None:
    typer.echo(f"Scraping Substack archive: {archive_url}")
    scrape_result = scrape_substack_archive(
        archive_url,
        subject=subject,
        section_slug=section_slug,
        max_articles=max_articles,
        run_dir=run_dir,
    )
    actual_run_dir = Path(scrape_result["run_dir"])
    typer.echo(
        f"Saved {scrape_result['posts_saved']} posts from {scrape_result['archive_posts_selected']} selected archive entries "
        f"({scrape_result['total_text_characters']} chars) into {actual_run_dir}"
    )
    typer.echo(
        f"Estimated extraction: {scrape_result['estimated_total_chunks']} chunks / "
        f"{scrape_result['estimated_input_tokens']} input tokens"
    )
    typer.echo(f"Building newsletter corpus in {actual_run_dir}")
    build_newsletter_corpus(run_dir=actual_run_dir, model=model, workers=workers)
    typer.echo(f"Building MediaWiki export into {output_dir.resolve()}")
    result = build_mediawiki_export(
        run_dir=actual_run_dir,
        output_dir=output_dir.resolve(),
        site_title=site_title or subject,
        build_search=True,
        wiki_dir=wiki_dir.resolve(),
    )
    typer.echo(f"MediaWiki export complete: {result['import_xml']}")
    typer.echo(result["import_xml"])


@app.command("build-publication")
def build_publication_command(
    source_url: str,
    subject: Optional[str] = None,
    max_articles: Optional[int] = None,
    model: str = "sonnet",
    workers: int = 10,
    quartz_concurrency: int = _default_quartz_concurrency(),
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

    typer.echo(f"Scraping publication archive: {source_url}")
    scrape_result = scrape_generic_publication(
        source_url,
        subject=subject,
        max_articles=max_articles,
        run_dir=run_dir,
    )
    actual_run_dir = Path(scrape_result["run_dir"])
    actual_vault_dir = vault_dir or Path("vault") / actual_run_dir.name
    typer.echo(
        f"Saved {scrape_result['posts_saved']} posts from {scrape_result['archive_posts_selected']} selected publication entries "
        f"({scrape_result['total_text_characters']} chars) into {actual_run_dir}"
    )
    typer.echo(
        f"Estimated extraction: {scrape_result['estimated_total_chunks']} chunks / "
        f"{scrape_result['estimated_input_tokens']} input tokens"
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


@app.command("build-publication-mediawiki")
def build_publication_mediawiki_command(
    source_url: str,
    subject: Optional[str] = None,
    max_articles: Optional[int] = None,
    model: str = "sonnet",
    workers: int = 10,
    run_dir: Optional[Path] = None,
    output_dir: Path = Path("dist/mediawiki"),
    wiki_dir: Path = Path("wiki"),
    site_title: Optional[str] = None,
) -> None:
    typer.echo(f"Scraping publication archive: {source_url}")
    scrape_result = scrape_generic_publication(
        source_url,
        subject=subject,
        max_articles=max_articles,
        run_dir=run_dir,
    )
    actual_run_dir = Path(scrape_result["run_dir"])
    typer.echo(
        f"Saved {scrape_result['posts_saved']} posts from {scrape_result['archive_posts_selected']} selected publication entries "
        f"({scrape_result['total_text_characters']} chars) into {actual_run_dir}"
    )
    typer.echo(
        f"Estimated extraction: {scrape_result['estimated_total_chunks']} chunks / "
        f"{scrape_result['estimated_input_tokens']} input tokens"
    )
    typer.echo(f"Building newsletter corpus in {actual_run_dir}")
    build_newsletter_corpus(run_dir=actual_run_dir, model=model, workers=workers)
    typer.echo(f"Building MediaWiki export into {output_dir.resolve()}")
    result = build_mediawiki_export(
        run_dir=actual_run_dir,
        output_dir=output_dir.resolve(),
        site_title=site_title or subject,
        build_search=True,
        wiki_dir=wiki_dir.resolve(),
    )
    typer.echo(f"MediaWiki export complete: {result['import_xml']}")
    typer.echo(result["import_xml"])


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
