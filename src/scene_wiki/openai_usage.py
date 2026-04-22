from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


_LOCK = threading.Lock()
_SUMMARY_BY_DIR: dict[str, dict[str, dict[str, Any]]] = {}


MODEL_PRICING_USD_PER_MILLION: dict[str, tuple[float, float]] = {
    "gpt-4.1-mini": (0.40, 1.60),
    "gpt-4.1": (2.00, 8.00),
    "gpt-4.1-nano": (0.10, 0.40),
    "gpt-5.4-mini": (0.75, 3.00),
    "gpt-5.4": (2.00, 8.00),
    "text-embedding-3-small": (0.02, 0.0),
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


def _dir_key(usage_dir: Path) -> str:
    return str(usage_dir.resolve())


def _summary_for_dir(usage_dir: Path) -> dict[str, dict[str, Any]]:
    key = _dir_key(usage_dir)
    existing = _SUMMARY_BY_DIR.get(key)
    if existing is not None:
        return existing
    summary_path = _summary_path(usage_dir)
    if summary_path.exists():
        try:
            payload = json.loads(summary_path.read_text(encoding="utf-8"))
            models = payload.get("models", {})
            if isinstance(models, dict):
                normalized = {
                    str(model): {
                        "calls": int(values.get("calls", 0)),
                        "inputTokens": int(values.get("inputTokens", 0)),
                        "outputTokens": int(values.get("outputTokens", 0)),
                        "totalTokens": int(values.get("totalTokens", 0)),
                        "estimatedCostUsd": float(values.get("estimatedCostUsd", 0.0)),
                    }
                    for model, values in models.items()
                    if isinstance(values, dict)
                }
                _SUMMARY_BY_DIR[key] = normalized
                return normalized
        except Exception:
            pass
    _SUMMARY_BY_DIR[key] = {}
    return _SUMMARY_BY_DIR[key]


def _totals_for_summary(summary: dict[str, dict[str, Any]]) -> dict[str, Any]:
    return {
        "calls": sum(item["calls"] for item in summary.values()),
        "inputTokens": sum(item["inputTokens"] for item in summary.values()),
        "outputTokens": sum(item["outputTokens"] for item in summary.values()),
        "totalTokens": sum(item["totalTokens"] for item in summary.values()),
        "estimatedCostUsd": round(sum(item["estimatedCostUsd"] for item in summary.values()), 6),
    }


def _write_summary_file(usage_dir: Path, summary: dict[str, dict[str, Any]]) -> None:
    _summary_path(usage_dir).write_text(
        json.dumps(
            {
                "generatedAt": datetime.now(timezone.utc).isoformat(),
                "models": summary,
                "totals": _totals_for_summary(summary),
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def reset_usage_summary() -> None:
    usage_dir = _usage_dir()
    if not usage_dir:
        return
    with _LOCK:
        _SUMMARY_BY_DIR[_dir_key(usage_dir)] = {}
        _write_summary_file(usage_dir, {})
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
    prompt_text: str,
    response_text: str,
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
        "promptText": prompt_text,
        "responseText": response_text,
        "inputTokens": input_tokens,
        "outputTokens": output_tokens,
        "totalTokens": total_tokens,
        "estimatedCostUsd": estimated_cost,
    }
    if not usage_dir:
        return event

    with _LOCK:
        summary = _summary_for_dir(usage_dir)
        _events_path(usage_dir).open("a", encoding="utf-8").write(json.dumps(event, sort_keys=True) + "\n")

        model_summary = summary.setdefault(
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

        totals = _totals_for_summary(summary)
        event["totals"] = totals
        _write_summary_file(usage_dir, summary)

    return event


def merge_usage_summaries(*summary_paths: Path) -> dict[str, Any]:
    merged_models: dict[str, dict[str, Any]] = {}
    generated_at = datetime.now(timezone.utc).isoformat()
    for summary_path in summary_paths:
        if not summary_path.exists():
            continue
        payload = json.loads(summary_path.read_text(encoding="utf-8"))
        generated_at = max(generated_at, str(payload.get("generatedAt") or generated_at))
        for model, values in payload.get("models", {}).items():
            model_summary = merged_models.setdefault(
                model,
                {
                    "calls": 0,
                    "inputTokens": 0,
                    "outputTokens": 0,
                    "totalTokens": 0,
                    "estimatedCostUsd": 0.0,
                },
            )
            model_summary["calls"] += int(values.get("calls", 0))
            model_summary["inputTokens"] += int(values.get("inputTokens", 0))
            model_summary["outputTokens"] += int(values.get("outputTokens", 0))
            model_summary["totalTokens"] += int(values.get("totalTokens", 0))
            model_summary["estimatedCostUsd"] = round(
                model_summary["estimatedCostUsd"] + float(values.get("estimatedCostUsd", 0.0)),
                6,
            )
    return {
        "generatedAt": generated_at,
        "models": merged_models,
        "totals": _totals_for_summary(merged_models),
    }
