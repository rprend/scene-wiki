from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal
from typing import Optional

from pydantic import BaseModel, Field


SourceName = Literal["exa-people", "exa-mentions", "youtube", "x", "browser", "manual", "arena", "spotify"]


class RunMetadata(BaseModel):
    run_id: str
    source: SourceName
    subject: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    provider: str
    notes: Optional[str] = None


class NormalizedDocument(BaseModel):
    doc_id: str
    title: str
    source: SourceName
    text: str
    url: Optional[str] = None
    published_at: Optional[str] = None


class CorpusEntity(BaseModel):
    name: str
    category: str
    evidence: list[str] = Field(default_factory=list)
    aliases: list[str] = Field(default_factory=list)
    confidence: Optional[float] = None


class Corpus(BaseModel):
    subject: str
    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    entities: list[CorpusEntity] = Field(default_factory=list)


class ProperNounChunk(BaseModel):
    chunk_id: str
    doc_id: str
    title: str
    text: str
    url: Optional[str] = None
    published_at: Optional[str] = None
    entities: list[CorpusEntity] = Field(default_factory=list)


class ProperNounCorpus(BaseModel):
    subject: str
    run_id: str
    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    chunk_size: int
    overlap: int
    chunks: list[ProperNounChunk] = Field(default_factory=list)
    entities: list[CorpusEntity] = Field(default_factory=list)
