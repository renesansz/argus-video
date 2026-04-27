from __future__ import annotations

import base64
import json
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from argus.config import DEFAULT_OLLAMA_HOST, DEFAULT_VISION_MODEL
from argus.progress import finish_progress, initialize_progress, update_progress

FRAME_CAPTION_PROMPT = (
    "You are classifying a frame from silent B-roll footage for a searchable local media library. "
    "Return strict JSON with keys short_caption, tags, and visible_text. "
    "short_caption must be one concise sentence with only visible facts. "
    "tags must be 35 to 49 short lowercase visual tags and it should accurately contains or represents the shot. "
    "visible_text must be a list of short strings that are actually readable in frame; otherwise return an empty list. "
    "Do not use markdown, headings, preambles, apologies, or questions."
)

VIDEO_SUMMARY_PROMPT = (
    "You are classifying silent B-roll footage for a local media library. "
    "Based on timestamped frame captions, produce: (1) a short title, (2) a fuller summary, and (3) search tags. "
    "The title must be an attention-grabbing one-line statement that includes the main topic keywords from the clip. "
    "The summary should give more context and purpose of the clip than the title. "
    "Prefer concrete visual terms over abstract adjectives; be original, unique, and succinct. "
    "Do not include conversational filler. "
    "Only include brand names or readable on-screen text if they are visually clear and central to the clip."
)


def caption_output_items(
    output_dir: Path,
    *,
    model: str = DEFAULT_VISION_MODEL,
    ollama_host: str = DEFAULT_OLLAMA_HOST,
    force: bool = False,
    progress_callback: Callable[[dict], None] | None = None,
) -> dict:
    output_dir = output_dir.resolve()
    items_dir = output_dir / "items"
    item_paths = sorted(items_dir.glob("*.json"))

    report = {
        "processed_items": 0,
        "updated_items": 0,
        "frames_attempted": 0,
        "frames_captioned": 0,
        "frames_skipped": 0,
        "frames_failed": 0,
        "model": model,
        "ollama_host": ollama_host,
    }

    preflight = captioning_preflight(model=model, ollama_host=ollama_host)
    report["preflight"] = preflight
    if preflight["status"] != "available":
        report["reason"] = preflight["reason"]
        return report

    progress_state = initialize_progress(
        output_dir,
        phase="caption",
        total_items=len(item_paths),
        total_frames=count_total_frames(item_paths),
        model=model,
    )

    for item_path in item_paths:
        record = json.loads(item_path.read_text(encoding="utf-8"))
        report["processed_items"] += 1

        updated, progress_state = caption_item_record(
            record,
            model=model,
            ollama_host=ollama_host,
            force=force,
            report=report,
            output_dir=output_dir,
            progress_state=progress_state,
            progress_callback=progress_callback,
        )
        if updated:
            report["updated_items"] += 1
            item_path.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
        progress_state = update_progress(
            output_dir,
            progress_state,
            completed_items=report["processed_items"],
            current_item=record.get("filename"),
        )

    manifest_path = output_dir / "manifest.json"
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["files"] = load_item_records(items_dir)
        manifest["caption_summary"] = report
        manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    finish_progress(output_dir, progress_state, status="completed")
    return report


def caption_item_record(
    record: dict,
    *,
    model: str,
    ollama_host: str,
    force: bool,
    report: dict,
    output_dir: Path,
    progress_state: dict,
    progress_callback: Callable[[dict], None] | None,
) -> tuple[bool, dict]:
    sample_frames = record.get("sample_frames", {})
    frames = sample_frames.get("frames", [])
    if not frames:
        return False, progress_state

    updated = False
    captions_for_summary: list[dict] = []

    for frame in frames:
        progress_state = update_progress(
            output_dir,
            progress_state,
            current_item=record.get("filename"),
            current_frame_index=frame.get("index"),
            current_frame_timestamp_seconds=frame.get("timestamp_seconds"),
            completed_items=report["processed_items"] - 1,
            processed_frames=report["frames_attempted"],
            completed_frames=report["frames_captioned"] + report["frames_failed"],
            failed_frames=report["frames_failed"],
        )
        if progress_callback:
            progress_callback(
                {
                    "filename": record.get("filename"),
                    "frame_index": frame.get("index"),
                    "frame_timestamp_seconds": frame.get("timestamp_seconds"),
                    "processed_frames": report["frames_attempted"],
                    "total_frames": progress_state.get("total_frames", 0),
                }
            )

        report["frames_attempted"] += 1
        if frame.get("status") != "ok":
            report["frames_skipped"] += 1
            continue

        if frame.get("caption") and not force:
            report["frames_skipped"] += 1
            captions_for_summary.append(
                {
                    "timestamp_seconds": frame["timestamp_seconds"],
                    "caption": frame["caption"]["text"],
                }
            )
            continue

        caption_result = caption_frame(
            Path(frame["path"]),
            model=model,
            ollama_host=ollama_host,
        )
        if caption_result["status"] == "ok":
            frame["caption"] = {
                "text": caption_result["text"],
                "tags": caption_result["tags"],
                "visible_text": caption_result["visible_text"],
                "generated_at": utc_now_iso(),
                "model": model,
            }
            frame.pop("caption_error", None)
            report["frames_captioned"] += 1
            captions_for_summary.append(
                {
                    "timestamp_seconds": frame["timestamp_seconds"],
                    "caption": caption_result["text"],
                }
            )
            updated = True
        else:
            frame["caption_error"] = {
                "reason": caption_result["reason"],
                "generated_at": utc_now_iso(),
                "model": model,
            }
            report["frames_failed"] += 1
            updated = True
        progress_state = update_progress(
            output_dir,
            progress_state,
            processed_frames=report["frames_attempted"],
            completed_frames=report["frames_captioned"] + report["frames_failed"],
            failed_frames=report["frames_failed"],
        )

    if captions_for_summary:
        summary_result = summarize_captions(
            captions_for_summary,
            model=model,
            ollama_host=ollama_host,
        )
        if summary_result["status"] == "ok":
            record["classification_status"] = "captions_ready"
            record["title"] = summary_result["title"]
            record["summary"] = summary_result["summary"]
            record["suggested_tags"] = summary_result["suggested_tags"]
            record["classification"] = {
                "generated_at": utc_now_iso(),
                "model": model,
                "source": "sample_frames",
            }
        else:
            record["classification_status"] = "caption_partial"
            record["classification_error"] = {
                "reason": summary_result["reason"],
                "generated_at": utc_now_iso(),
                "model": model,
            }
        updated = True

    return updated, progress_state


def caption_frame(image_path: Path, *, model: str, ollama_host: str) -> dict:
    encoded = base64.b64encode(image_path.read_bytes()).decode("utf-8")
    payload = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": FRAME_CAPTION_PROMPT,
                "images": [encoded],
            }
        ],
        "format": "json",
        "stream": False,
    }

    try:
        response = ollama_chat(payload, ollama_host=ollama_host)
    except OllamaError as exc:
        return {"status": "error", "reason": str(exc)}

    content = response.get("message", {}).get("content", "").strip()
    if not content:
        return {"status": "error", "reason": "empty caption response"}
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as exc:
        return {"status": "error", "reason": f"invalid frame caption json: {exc}"}

    short_caption = parsed.get("short_caption")
    tags = parsed.get("tags")
    visible_text = parsed.get("visible_text")
    if not isinstance(short_caption, str) or not isinstance(tags, list):
        return {"status": "error", "reason": "frame caption missing required fields"}

    return {
        "status": "ok",
        "text": normalize_sentence(short_caption),
        "tags": normalize_tags(tags),
        "visible_text": normalize_visible_text(visible_text),
    }


def summarize_captions(
    captions_for_summary: list[dict],
    *,
    model: str,
    ollama_host: str,
) -> dict:
    lines = [
        f"- {entry['timestamp_seconds']:.3f}s: {entry['caption']}"
        for entry in captions_for_summary
    ]
    payload = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": VIDEO_SUMMARY_PROMPT,
            },
            {
                "role": "user",
                "content": (
                    "Frame captions:\n"
                    + "\n".join(lines)
                    + "\n\nReturn strict JSON with keys title, summary, and suggested_tags. "
                    + "title: one short line, attention-grabbing, must include main topic keywords; "
                    + "maximum 100 characters, no line breaks, no quotes wrapping the whole title. "
                    + "summary: Minimum 15 characters - Maximum 200 characters, Minimum 5 words; "
                    + "more detailed than the title. "
                    + "suggested_tags should be 35 to 49 short lowercase tags."
                ),
            },
        ],
        "format": "json",
        "stream": False,
    }

    try:
        response = ollama_chat(payload, ollama_host=ollama_host)
    except OllamaError as exc:
        return {"status": "error", "reason": str(exc)}

    content = response.get("message", {}).get("content", "").strip()
    if not content:
        return {"status": "error", "reason": "empty summary response"}

    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as exc:
        return {"status": "error", "reason": f"invalid summary json: {exc}"}

    title = parsed.get("title")
    summary = parsed.get("summary")
    suggested_tags = parsed.get("suggested_tags")
    if (
        not isinstance(title, str)
        or not isinstance(summary, str)
        or not isinstance(suggested_tags, list)
    ):
        return {
            "status": "error",
            "reason": "summary response missing required fields",
        }

    normalized_title = normalize_clip_title(title)
    if not normalized_title:
        return {
            "status": "error",
            "reason": "summary response missing required fields",
        }

    cleaned_tags = [
        tag.strip()
        for tag in suggested_tags
        if isinstance(tag, str) and tag.strip()
    ]
    return {
        "status": "ok",
        "title": normalized_title,
        "summary": normalize_sentence(summary),
        "suggested_tags": normalize_tags(cleaned_tags),
    }


def ollama_chat(payload: dict, *, ollama_host: str) -> dict:
    endpoint = ollama_host.rstrip("/") + "/api/chat"
    request = Request(
        endpoint,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=300) as response:
            body = response.read().decode("utf-8")
    except HTTPError as exc:
        details = exc.read().decode("utf-8", errors="replace")
        raise OllamaError(f"Ollama HTTP {exc.code}: {details}") from exc
    except URLError as exc:
        raise OllamaError(f"Could not reach Ollama at {endpoint}: {exc.reason}") from exc

    try:
        return json.loads(body)
    except json.JSONDecodeError as exc:
        raise OllamaError(f"Invalid Ollama JSON response: {exc}") from exc


def load_item_records(items_dir: Path) -> list[dict]:
    return [
        json.loads(item_path.read_text(encoding="utf-8"))
        for item_path in sorted(items_dir.glob("*.json"))
    ]


def count_total_frames(item_paths: list[Path]) -> int:
    total = 0
    for item_path in item_paths:
        record = json.loads(item_path.read_text(encoding="utf-8"))
        total += len(record.get("sample_frames", {}).get("frames", []))
    return total


def ollama_healthcheck(ollama_host: str) -> dict:
    endpoint = ollama_host.rstrip("/") + "/api/version"
    request = Request(endpoint, method="GET")
    try:
        with urlopen(request, timeout=3) as response:
            body = response.read().decode("utf-8")
    except HTTPError as exc:
        details = exc.read().decode("utf-8", errors="replace")
        return {
            "status": "missing",
            "reason": f"Ollama HTTP {exc.code}: {details}",
        }
    except URLError as exc:
        return {
            "status": "missing",
            "reason": f"Could not reach Ollama at {endpoint}: {exc.reason}",
        }

    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        payload = {}

    return {
        "status": "available",
        "version": payload.get("version"),
        "reason": None,
    }


def ollama_model_check(model: str, ollama_host: str) -> dict:
    endpoint = ollama_host.rstrip("/") + "/api/tags"
    request = Request(endpoint, method="GET")
    try:
        with urlopen(request, timeout=5) as response:
            body = response.read().decode("utf-8")
    except HTTPError as exc:
        details = exc.read().decode("utf-8", errors="replace")
        return {
            "status": "missing",
            "model": model,
            "reason": f"Ollama HTTP {exc.code}: {details}",
        }
    except URLError as exc:
        return {
            "status": "missing",
            "model": model,
            "reason": f"Could not reach Ollama at {endpoint}: {exc.reason}",
        }

    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        return {
            "status": "missing",
            "model": model,
            "reason": f"Invalid Ollama model list JSON: {exc}",
        }

    models = payload.get("models", [])
    matched = match_ollama_model(models, model)
    if matched is None:
        return {
            "status": "missing",
            "model": model,
            "reason": f"Model `{model}` is not installed in Ollama",
        }

    details = matched.get("details", {})
    return {
        "status": "available",
        "model": model,
        "resolved_name": matched.get("name") or matched.get("model"),
        "family": details.get("family"),
        "parameter_size": details.get("parameter_size"),
        "quantization_level": details.get("quantization_level"),
        "reason": None,
    }


def captioning_preflight(model: str, ollama_host: str) -> dict:
    api_status = ollama_healthcheck(ollama_host)
    model_status = (
        ollama_model_check(model, ollama_host)
        if api_status["status"] == "available"
        else {
            "status": "missing",
            "model": model,
            "reason": "Ollama API unavailable",
        }
    )

    if api_status["status"] != "available":
        return {
            "status": "missing",
            "reason": api_status["reason"],
            "ollama_api": api_status,
            "ollama_model": model_status,
        }

    if model_status["status"] != "available":
        return {
            "status": "missing",
            "reason": model_status["reason"],
            "ollama_api": api_status,
            "ollama_model": model_status,
        }

    return {
        "status": "available",
        "reason": None,
        "ollama_api": api_status,
        "ollama_model": model_status,
    }


def match_ollama_model(models: list[dict], requested_model: str) -> dict | None:
    requested_base = base_model_name(requested_model)
    for model in models:
        names = {
            model.get("name"),
            model.get("model"),
            base_model_name(model.get("name")),
            base_model_name(model.get("model")),
        }
        if requested_model in names or requested_base in names:
            return model
    return None


def base_model_name(value: str | None) -> str | None:
    if not value:
        return None
    return value.split(":", maxsplit=1)[0]


def normalize_sentence(value: str) -> str:
    collapsed = " ".join(value.split()).strip()
    return collapsed


def normalize_clip_title(value: str, *, max_len: int = 100) -> str:
    """Collapse whitespace and cap length for storage and display."""
    text = normalize_sentence(value)
    if not text or len(text) <= max_len:
        return text
    chunk = text[: max_len + 1]
    cut = chunk.rfind(" ", 0, max_len + 1)
    if cut >= max(1, max_len // 2):
        base = chunk[:cut].rstrip(" ,.;:!?")
    else:
        base = text[:max_len].rstrip(" ,.;:!?")
    return base[:max_len]


def normalize_tags(values: list[str]) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for value in values:
        if not isinstance(value, str):
            continue
        cleaned = " ".join(value.strip().lower().split())
        cleaned = cleaned.strip(" ,.;:!?")
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        normalized.append(cleaned)
    return normalized


def normalize_visible_text(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    normalized: list[str] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, str):
            continue
        cleaned = " ".join(item.split()).strip()
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        normalized.append(cleaned)
    return normalized


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class OllamaError(RuntimeError):
    pass
