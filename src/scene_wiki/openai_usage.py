from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


_LOCK = threading.Lock()
_SUMMARY: dict[str, dict[str, Any]] = {}


MODEL_PRICING_USD_PER_MILLION: dict[str, tuple[float, float]] = {
    "gpt-4.1-mini": (0.40, 1.60),
    "gpt-4.1": (2.00, 8.00),
    "gpt-4.1-nano": (0.10, 0.40),
    "gpt-5.4-mini": (0.75, 3.00),
    "gpt-5.4": (2.00, 8.00),
}


def _usage_dir() -> Path | None:
    raw = os.getenv("SCENE_WIKI_AI_USAGE_DIR", "").strip()
    if not raw:
        return None
    path = Path(raw).resolve()
    path.mkdir(parents=True, exist_ok=True)
    return path


def _normalize_model_name(model: str | None) -> str:
    raw = (model or "").strip()
    if not raw:
        return "unknown"
    for known in sorted(MODEL_PRICING_USD_PER_MILLION, key=len, reverse=True):
        if raw == known or raw.startswith(f"{known}-"):
            return known
    return raw


def estimate_cost_usd(model: str | None, input_tokens: int, output_tokens: int) -> float | None:
    normalized = _normalize_model_name(model)
    rates = MODEL_PRICING_USD_PER_MILLION.get(normalized)
    if not rates:
        return None
    input_rate, output_rate = rates
    return round((input_tokens / 1_000_000 * input_rate) + (output_tokens / 1_000_000 * output_rate), 6)


def _summary_path(usage_dir: Path) -> Path:
    return usage_dir / "summary.json"


def _events_path(usage_dir: Path) -> Path:
    return usage_dir / "events.jsonl"


def reset_usage_summary() -> None:
    usage_dir = _usage_dir()
    if not usage_dir:
        return
    with _LOCK:
        _SUMMARY.clear()
        _summary_path(usage_dir).write_text(
            json.dumps(
                {
                    "generatedAt": datetime.now(timezone.utc).isoformat(),
                    "models": {},
                    "totals": {
                        "calls": 0,
                        "inputTokens": 0,
                        "outputTokens": 0,
                        "totalTokens": 0,
                        "estimatedCostUsd": 0.0,
                    },
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        _events_path(usage_dir).write_text("", encoding="utf-8")


def record_openai_usage(
    *,
    requested_model: str,
    resolved_model: str | None,
    response_id: str | None,
    input_tokens: int,
    output_tokens: int,
    total_tokens: int,
    prompt_chars: int,
    chunk_id: str | None = None,
    doc_id: str | None = None,
) -> dict[str, Any]:
    usage_dir = _usage_dir()
    normalized_model = _normalize_model_name(resolved_model or requested_model)
    estimated_cost = estimate_cost_usd(normalized_model, input_tokens, output_tokens)
    event = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "responseId": response_id,
        "requestedModel": requested_model,
        "resolvedModel": resolved_model or requested_model,
        "normalizedModel": normalized_model,
        "chunkId": chunk_id,
        "docId": doc_id,
        "promptChars": prompt_chars,
        "inputTokens": input_tokens,
        "outputTokens": output_tokens,
        "totalTokens": total_tokens,
        "estimatedCostUsd": estimated_cost,
    }
    if not usage_dir:
        return event

    with _LOCK:
        _events_path(usage_dir).open("a", encoding="utf-8").write(json.dumps(event, sort_keys=True) + "\n")

        model_summary = _SUMMARY.setdefault(
            normalized_model,
            {
                "calls": 0,
                "inputTokens": 0,
                "outputTokens": 0,
                "totalTokens": 0,
                "estimatedCostUsd": 0.0,
            },
        )
        model_summary["calls"] += 1
        model_summary["inputTokens"] += input_tokens
        model_summary["outputTokens"] += output_tokens
        model_summary["totalTokens"] += total_tokens
        if estimated_cost is not None:
            model_summary["estimatedCostUsd"] = round(model_summary["estimatedCostUsd"] + estimated_cost, 6)

        totals = {
            "calls": sum(item["calls"] for item in _SUMMARY.values()),
            "inputTokens": sum(item["inputTokens"] for item in _SUMMARY.values()),
            "outputTokens": sum(item["outputTokens"] for item in _SUMMARY.values()),
            "totalTokens": sum(item["totalTokens"] for item in _SUMMARY.values()),
            "estimatedCostUsd": round(sum(item["estimatedCostUsd"] for item in _SUMMARY.values()), 6),
        }
        event["totals"] = totals
        _summary_path(usage_dir).write_text(
            json.dumps(
                {
                    "generatedAt": datetime.now(timezone.utc).isoformat(),
                    "models": _SUMMARY,
                    "totals": totals,
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

    return event
