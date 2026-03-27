from __future__ import annotations

import argparse
import json
from pathlib import Path

from argus.captioner import caption_output_items
from argus.config import DEFAULT_OLLAMA_HOST, DEFAULT_VISION_MODEL
from argus.database import default_db_path, index_output_items, search_index
from argus.dependencies import dependency_report
from argus.pipeline import run_scan
from argus.serve import serve_ui
from argus.status import build_status_report, run_status_tui


def caption_progress_printer(payload: dict) -> None:
    total_frames = payload.get("total_frames") or "?"
    current_number = payload.get("processed_frames", 0) + 1
    timestamp = payload.get("frame_timestamp_seconds")
    timestamp_text = f"{timestamp:.3f}s" if isinstance(timestamp, (int, float)) else "?"
    print(
        f"[caption] {current_number}/{total_frames} "
        f"{payload.get('filename')} frame {payload.get('frame_index')} "
        f"@ {timestamp_text}",
        flush=True,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="argus",
        description="Scan a folder of local videos and write a JSON manifest.",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    scan_parser = subparsers.add_parser(
        "scan",
        help="Scan a folder recursively for supported video files.",
    )
    scan_parser.add_argument(
        "input_dir",
        nargs="?",
        default="ingest",
        help="Directory to scan. Use a normal filesystem path such as ./ingest or /Volumes/Share. Defaults to ./ingest",
    )
    scan_parser.add_argument(
        "--output-dir",
        default="output",
        help="Directory for manifest and item JSON files. Defaults to ./output",
    )
    scan_parser.add_argument(
        "--pretty",
        action="store_true",
        help="Pretty-print the manifest summary to stdout.",
    )
    scan_parser.add_argument(
        "--sample-frames",
        action="store_true",
        help="Extract representative JPEG frames for each video during scan.",
    )
    scan_parser.add_argument(
        "--frame-count",
        type=int,
        default=4,
        help="Number of representative frames to extract per video when --sample-frames is set. Defaults to 4.",
    )

    doctor_parser = subparsers.add_parser(
        "doctor",
        help="Show local dependency status and what features are available.",
    )
    doctor_parser.add_argument(
        "--pretty",
        action="store_true",
        help="Pretty-print the dependency report to stdout.",
    )
    doctor_parser.add_argument(
        "--model",
        default=DEFAULT_VISION_MODEL,
        help=f"Ollama vision model to check. Defaults to {DEFAULT_VISION_MODEL}",
    )
    doctor_parser.add_argument(
        "--ollama-host",
        default=DEFAULT_OLLAMA_HOST,
        help=f"Ollama API host to check. Defaults to {DEFAULT_OLLAMA_HOST}",
    )

    caption_parser = subparsers.add_parser(
        "caption",
        help="Caption extracted frames with a local Ollama vision model and summarize each clip.",
    )
    caption_parser.add_argument(
        "--output-dir",
        default="output",
        help="Directory containing item JSON files and extracted frames. Defaults to ./output",
    )
    caption_parser.add_argument(
        "--model",
        default=DEFAULT_VISION_MODEL,
        help=f"Ollama vision model to use. Defaults to {DEFAULT_VISION_MODEL}",
    )
    caption_parser.add_argument(
        "--ollama-host",
        default=DEFAULT_OLLAMA_HOST,
        help=f"Ollama API host. Defaults to {DEFAULT_OLLAMA_HOST}",
    )
    caption_parser.add_argument(
        "--force",
        action="store_true",
        help="Re-caption frames even if captions already exist.",
    )
    caption_parser.add_argument(
        "--pretty",
        action="store_true",
        help="Pretty-print the captioning report to stdout.",
    )

    status_parser = subparsers.add_parser(
        "status",
        help="Show a terminal dashboard of ingest, sampling, and captioning progress.",
    )
    status_parser.add_argument(
        "input_dir",
        nargs="?",
        default="ingest",
        help="Directory to scan for source videos. Use a mounted filesystem path such as ./ingest or /Volumes/Share. Defaults to ./ingest",
    )
    status_parser.add_argument(
        "--output-dir",
        default="output",
        help="Directory containing item JSON files and frames. Defaults to ./output",
    )
    status_parser.add_argument(
        "--watch",
        action="store_true",
        help="Refresh the terminal dashboard until interrupted.",
    )
    status_parser.add_argument(
        "--interval",
        type=float,
        default=2.0,
        help="Refresh interval in seconds for --watch. Defaults to 2.0",
    )
    status_parser.add_argument(
        "--model",
        default=DEFAULT_VISION_MODEL,
        help=f"Ollama vision model to check. Defaults to {DEFAULT_VISION_MODEL}",
    )
    status_parser.add_argument(
        "--ollama-host",
        default=DEFAULT_OLLAMA_HOST,
        help=f"Ollama API host to check. Defaults to {DEFAULT_OLLAMA_HOST}",
    )
    status_parser.add_argument(
        "--pretty",
        action="store_true",
        help="Print the underlying status report as JSON instead of the dashboard.",
    )

    index_parser = subparsers.add_parser(
        "index",
        help="Build or refresh the local SQLite search index from output item JSON files.",
    )
    index_parser.add_argument(
        "--output-dir",
        default="output",
        help="Directory containing item JSON files. Defaults to ./output",
    )
    index_parser.add_argument(
        "--db-path",
        default=None,
        help="Optional SQLite database path. Defaults to <output-dir>/argus.db",
    )
    index_parser.add_argument(
        "--pretty",
        action="store_true",
        help="Pretty-print the indexing report to stdout.",
    )

    search_parser = subparsers.add_parser(
        "search",
        help="Search the local SQLite index for clips by summary, tags, captions, and visible text.",
    )
    search_parser.add_argument(
        "query",
        help="Search query string.",
    )
    search_parser.add_argument(
        "--output-dir",
        default="output",
        help="Directory containing the default SQLite database. Defaults to ./output",
    )
    search_parser.add_argument(
        "--db-path",
        default=None,
        help="Optional SQLite database path. Defaults to <output-dir>/argus.db",
    )
    search_parser.add_argument(
        "--limit",
        type=int,
        default=10,
        help="Maximum number of results to return. Defaults to 10.",
    )
    search_parser.add_argument(
        "--pretty",
        action="store_true",
        help="Pretty-print search results as JSON.",
    )

    serve_parser = subparsers.add_parser(
        "serve",
        help="Run a local browser-based search UI for the SQLite index.",
    )
    serve_parser.add_argument(
        "--output-dir",
        default="output",
        help="Directory containing the default SQLite database. Defaults to ./output",
    )
    serve_parser.add_argument(
        "--db-path",
        default=None,
        help="Optional SQLite database path. Defaults to <output-dir>/argus.db",
    )
    serve_parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Host interface to bind. Defaults to 127.0.0.1",
    )
    serve_parser.add_argument(
        "--port",
        type=int,
        default=8765,
        help="Port to bind. Defaults to 8765",
    )
    serve_parser.add_argument(
        "--open-browser",
        action="store_true",
        help="Open the UI in your default browser after the server starts.",
    )

    run_parser = subparsers.add_parser(
        "run",
        help="Run scan, caption, and index in one command against any source folder.",
    )
    run_parser.add_argument(
        "input_dir",
        nargs="?",
        default="ingest",
        help="Source directory to scan. Use a mounted filesystem path such as ./ingest or /Volumes/Share. Defaults to ./ingest",
    )
    run_parser.add_argument(
        "--output-dir",
        default="output",
        help="Directory for generated manifests, frames, and database. Defaults to ./output",
    )
    run_parser.add_argument(
        "--frame-count",
        type=int,
        default=4,
        help="Number of representative frames to sample per clip. Defaults to 4.",
    )
    run_parser.add_argument(
        "--model",
        default=DEFAULT_VISION_MODEL,
        help=f"Ollama vision model to use. Defaults to {DEFAULT_VISION_MODEL}",
    )
    run_parser.add_argument(
        "--ollama-host",
        default=DEFAULT_OLLAMA_HOST,
        help=f"Ollama API host. Defaults to {DEFAULT_OLLAMA_HOST}",
    )
    run_parser.add_argument(
        "--db-path",
        default=None,
        help="Optional SQLite database path. Defaults to <output-dir>/argus.db",
    )
    run_parser.add_argument(
        "--force-caption",
        action="store_true",
        help="Re-caption frames even if captions already exist.",
    )
    run_parser.add_argument(
        "--pretty",
        action="store_true",
        help="Pretty-print the combined pipeline report to stdout.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "scan":
        manifest = run_scan(
            Path(args.input_dir),
            Path(args.output_dir),
            sample_frames=args.sample_frames,
            frame_count=args.frame_count,
        )
        if args.pretty:
            print(json.dumps(manifest, indent=2))
        else:
            print(
                f"Scanned {manifest['file_count']} file(s). "
                f"Media probed: {manifest['probe_summary']['ok']}. "
                f"Manifest: {manifest['manifest_path']}"
            )
            if "frame_summary" in manifest:
                print(
                    f"Frame extraction attempted for {manifest['frame_summary']['videos_attempted']} file(s); "
                    f"wrote {manifest['frame_summary']['frames_written']} frame(s)."
                )
            if manifest["probe_summary"]["unavailable"] > 0:
                print(
                    "Media probing is unavailable for one or more files. "
                    "Run `argus doctor` and install ffmpeg to enable ffprobe."
                )
        return 0

    if args.command == "doctor":
        report = dependency_report(
            ollama_host=args.ollama_host,
            vision_model=args.model,
        )
        if args.pretty:
            print(json.dumps(report, indent=2))
        else:
            ffprobe_status = report["dependencies"]["ffprobe"]["status"]
            ollama_api_status = report["dependencies"]["ollama_api"]["status"]
            ollama_model_status = report["dependencies"]["ollama_model"]["status"]
            print(
                f"Python: {report['dependencies']['python']['status']}. "
                f"ffprobe: {ffprobe_status}. "
                f"Ollama API: {ollama_api_status}. "
                f"Model {args.model}: {ollama_model_status}."
            )
            if ffprobe_status != "available":
                print("Install FFmpeg with `brew install ffmpeg`.")
            if report["dependencies"]["ollama"]["status"] != "available":
                print("Install Ollama from https://ollama.com/download")
            elif ollama_api_status != "available":
                print("Start Ollama so the local API is reachable.")
            elif ollama_model_status != "available":
                print(f"Pull the model with `ollama pull {args.model}`.")
        return 0

    if args.command == "caption":
        progress_callback = None
        if not args.pretty:
            progress_callback = caption_progress_printer
        report = caption_output_items(
            Path(args.output_dir),
            model=args.model,
            ollama_host=args.ollama_host,
            force=args.force,
            progress_callback=progress_callback,
        )
        if args.pretty:
            print(json.dumps(report, indent=2))
        else:
            print(
                f"Processed {report['processed_items']} item(s). "
                f"Captioned {report['frames_captioned']} frame(s). "
                f"Updated {report['updated_items']} item(s)."
            )
        return 0

    if args.command == "status":
        if args.pretty:
            report = build_status_report(
                Path(args.input_dir),
                Path(args.output_dir),
                model=args.model,
                ollama_host=args.ollama_host,
            )
            print(json.dumps(report, indent=2))
            return 0
        return run_status_tui(
            Path(args.input_dir),
            Path(args.output_dir),
            model=args.model,
            ollama_host=args.ollama_host,
            watch=args.watch,
            interval_seconds=args.interval,
        )

    if args.command == "index":
        db_path = Path(args.db_path) if args.db_path else default_db_path(Path(args.output_dir))
        report = index_output_items(Path(args.output_dir), db_path=db_path)
        if args.pretty:
            print(json.dumps(report, indent=2))
        else:
            print(
                f"Indexed {report['indexed_videos']} video(s) and "
                f"{report['indexed_frames']} frame(s). "
                f"Database: {report['db_path']}"
            )
        return 0

    if args.command == "search":
        db_path = Path(args.db_path) if args.db_path else default_db_path(Path(args.output_dir))
        results = search_index(db_path, args.query, limit=args.limit)
        if args.pretty:
            print(json.dumps(results, indent=2))
            return 0
        if not results:
            print("No matches.")
            return 0
        for index, result in enumerate(results, start=1):
            tags = ", ".join(result["suggested_tags"][:6])
            summary = result["summary"] or ""
            match_text = result["match_text"] or ""
            print(f"{index}. {result['filename']}")
            print(f"   Path: {result['path']}")
            print(f"   Status: {result['classification_status']}")
            if result["duration_seconds"] is not None:
                print(
                    f"   Video: {result['duration_seconds']:.2f}s "
                    f"{result['width']}x{result['height']}"
                )
            if tags:
                print(f"   Tags: {tags}")
            if summary:
                print(f"   Summary: {summary}")
            if match_text:
                print(f"   Match: {match_text}")
        return 0

    if args.command == "serve":
        db_path = Path(args.db_path) if args.db_path else default_db_path(Path(args.output_dir))
        return serve_ui(
            db_path=db_path,
            host=args.host,
            port=args.port,
            open_browser=args.open_browser,
        )

    if args.command == "run":
        output_dir = Path(args.output_dir)
        db_path = Path(args.db_path) if args.db_path else default_db_path(output_dir)
        scan_report = run_scan(
            Path(args.input_dir),
            output_dir,
            sample_frames=True,
            frame_count=args.frame_count,
        )
        caption_report = caption_output_items(
            output_dir,
            model=args.model,
            ollama_host=args.ollama_host,
            force=args.force_caption,
            progress_callback=None if args.pretty else caption_progress_printer,
        )
        index_report = index_output_items(output_dir, db_path=db_path)
        combined = {
            "input_dir": str(Path(args.input_dir).resolve()),
            "output_dir": str(output_dir.resolve()),
            "scan": scan_report,
            "caption": caption_report,
            "index": index_report,
        }
        if args.pretty:
            print(json.dumps(combined, indent=2))
        else:
            print(
                f"Run complete. Scanned {scan_report['file_count']} file(s), "
                f"captioned {caption_report['frames_captioned']} frame(s), "
                f"indexed {index_report['indexed_videos']} video(s)."
            )
            print(f"Database: {index_report['db_path']}")
        return 0

    parser.error(f"Unknown command: {args.command}")
    return 2
