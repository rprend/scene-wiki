from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable
from typing import Optional
from uuid import uuid4

from .config import get_settings
from .models import NormalizedDocument, RunMetadata


def runs_root() -> Path:
    root = get_settings().data_root / "runs"
    root.mkdir(parents=True, exist_ok=True)
    return root


def make_run(source: str, subject: str, provider: str, notes: Optional[str] = None) -> tuple[Path, RunMetadata]:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_id = f"{stamp}-{uuid4().hex[:8]}"
    run_dir = runs_root() / run_id
    for child in ("raw", "normalized", "artifacts"):
        (run_dir / child).mkdir(parents=True, exist_ok=True)
    metadata = RunMetadata(run_id=run_id, source=source, subject=subject, provider=provider, notes=notes)
    write_json(run_dir / "metadata.json", metadata.model_dump(mode="json"))
    return run_dir, metadata


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def save_normalized_documents(run_dir: Path, documents: Iterable[NormalizedDocument]) -> list[Path]:
    paths: list[Path] = []
    for document in documents:
        path = run_dir / "normalized" / f"{document.doc_id}.json"
        write_json(path, document.model_dump(mode="json"))
        paths.append(path)
    return paths


def load_normalized_documents(run_dir: Path) -> list[NormalizedDocument]:
    docs: list[NormalizedDocument] = []
    for path in sorted((run_dir / "normalized").glob("*.json")):
        docs.append(NormalizedDocument.model_validate(read_json(path)))
    return docs
