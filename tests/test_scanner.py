from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from urllib.error import URLError
from unittest.mock import MagicMock, patch

from argus.captioner import (
    caption_output_items,
    match_ollama_model,
    normalize_tags,
    summarize_captions,
)
from argus.config import DEFAULT_OLLAMA_HOST
from argus.database import index_output_items, search_index
from argus.dependencies import dependency_report
from argus.extractor import evenly_spaced_timestamps
from argus.pipeline import run_scan
from argus.progress import load_progress
from argus.scanner import build_video_record, scan_video_files
from argus.status import build_status_report, render_status_text


class ScannerTests(unittest.TestCase):
    def test_scan_video_files_filters_supported_extensions(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "a.mp4").write_bytes(b"")
            (root / "b.mov").write_bytes(b"")
            (root / "c.txt").write_text("ignore", encoding="utf-8")

            files = scan_video_files(root)

            self.assertEqual([path.name for path in files], ["a.mp4", "b.mov"])

    def test_build_video_record_marks_audio_as_not_required(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            file_path = Path(temp_dir) / "sample.mp4"
            file_path.write_bytes(b"not-a-real-video")

            record = build_video_record(file_path)

            self.assertEqual(record["filename"], "sample.mp4")
            self.assertFalse(record["audio_required"])
            self.assertEqual(record["classification_status"], "pending")

    def test_run_scan_writes_manifest_and_items(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            input_dir = root / "ingest"
            output_dir = root / "output"
            input_dir.mkdir()
            (input_dir / "clip.mp4").write_bytes(b"fake")

            manifest = run_scan(input_dir, output_dir)

            self.assertEqual(manifest["file_count"], 1)
            self.assertIn("probe_summary", manifest)
            self.assertTrue((output_dir / "manifest.json").exists())
            self.assertEqual(len(list((output_dir / "items").glob("*.json"))), 1)

    def test_dependency_report_includes_expected_keys(self) -> None:
        report = dependency_report()

        self.assertIn("dependencies", report)
        self.assertIn("ffprobe", report["dependencies"])
        self.assertIn("features", report)
        self.assertIn("media_probe", report["features"])

    def test_evenly_spaced_timestamps_skip_edges(self) -> None:
        timestamps = evenly_spaced_timestamps(10.0, 4)

        self.assertEqual(timestamps, [2.0, 4.0, 6.0, 8.0])

    @patch("argus.pipeline.extract_sample_frames")
    def test_run_scan_can_attach_sample_frames(self, extract_sample_frames_mock) -> None:
        extract_sample_frames_mock.return_value = {
            "status": "ok",
            "frame_count_requested": 2,
            "frames": [
                {
                    "index": 1,
                    "timestamp_seconds": 1.0,
                    "path": "/tmp/frame1.jpg",
                    "status": "ok",
                }
            ],
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            input_dir = root / "ingest"
            output_dir = root / "output"
            input_dir.mkdir()
            (input_dir / "clip.mp4").write_bytes(b"fake")

            manifest = run_scan(
                input_dir,
                output_dir,
                sample_frames=True,
                frame_count=2,
            )

            self.assertIn("frame_summary", manifest)
            self.assertEqual(manifest["frame_summary"]["videos_attempted"], 1)
            self.assertEqual(manifest["frame_summary"]["frames_written"], 1)

    @patch("argus.dependencies.urllib.request.urlopen")
    def test_dependency_report_handles_missing_ollama_api(self, urlopen_mock) -> None:
        urlopen_mock.side_effect = URLError("connection refused")

        report = dependency_report()

        self.assertIn("ollama_api", report["dependencies"])
        self.assertEqual(report["dependencies"]["ollama_api"]["status"], "missing")
        self.assertIn("ollama_model", report["dependencies"])
        self.assertEqual(report["dependencies"]["ollama_model"]["status"], "missing")

    @patch("argus.captioner.ollama_chat")
    def test_summarize_captions_parses_json_response(self, ollama_chat_mock) -> None:
        ollama_chat_mock.return_value = {
            "message": {
                "content": '{"summary":"Wide exterior drone footage.","suggested_tags":["Drone","aerial","drone"]}'
            }
        }

        result = summarize_captions(
            [{"timestamp_seconds": 1.0, "caption": "Drone shot over a road."}],
            model="gemma3",
            ollama_host="http://localhost:11434",
        )

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["summary"], "Wide exterior drone footage.")
        self.assertEqual(result["suggested_tags"], ["drone", "aerial"])

    def test_normalize_tags_lowercases_and_deduplicates(self) -> None:
        result = normalize_tags(["Drone", " aerial ", "drone", ""])

        self.assertEqual(result, ["drone", "aerial"])

    @patch("argus.captioner.captioning_preflight")
    def test_caption_output_items_stops_when_ollama_is_unavailable(
        self, captioning_preflight_mock
    ) -> None:
        captioning_preflight_mock.return_value = {
            "status": "missing",
            "reason": "connection refused",
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            (output_dir / "items").mkdir()
            report = caption_output_items(output_dir)

        self.assertEqual(report["processed_items"], 0)
        self.assertEqual(report["preflight"]["status"], "missing")

    def test_match_ollama_model_matches_latest_alias(self) -> None:
        models = [
            {
                "name": "gemma3:latest",
                "model": "gemma3:latest",
                "details": {"family": "gemma3"},
            }
        ]

        matched = match_ollama_model(models, "gemma3")

        self.assertIsNotNone(matched)
        self.assertEqual(matched["name"], "gemma3:latest")

    @patch("argus.dependencies.ollama_model_check")
    @patch("argus.dependencies.urllib.request.urlopen")
    def test_dependency_report_marks_model_available_when_present(
        self, urlopen_mock, ollama_model_check_mock
    ) -> None:
        response = MagicMock()
        response.read.return_value = b'{"version":"0.12.0"}'
        response.__enter__.return_value = response
        response.__exit__.return_value = False
        urlopen_mock.return_value = response
        ollama_model_check_mock.return_value = {
            "status": "available",
            "model": "gemma3",
            "resolved_name": "gemma3:latest",
            "reason": None,
        }

        report = dependency_report(
            ollama_host=DEFAULT_OLLAMA_HOST,
            vision_model="gemma3",
        )

        self.assertEqual(report["dependencies"]["ollama_api"]["status"], "available")
        self.assertEqual(report["dependencies"]["ollama_model"]["status"], "available")
        self.assertTrue(report["features"]["frame_captioning"])

    @patch("argus.status.dependency_report")
    def test_build_status_report_summarizes_items(self, dependency_report_mock) -> None:
        dependency_report_mock.return_value = {
            "dependencies": {
                "ffmpeg": {"status": "available"},
                "ffprobe": {"status": "available"},
                "ollama": {"status": "available"},
                "ollama_api": {"status": "available"},
                "ollama_model": {
                    "status": "available",
                    "resolved_name": "gemma3:latest",
                    "parameter_size": "4.3B",
                },
            },
            "features": {"frame_captioning": True},
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            ingest = root / "ingest"
            output = root / "output"
            items = output / "items"
            ingest.mkdir()
            items.mkdir(parents=True)

            (ingest / "clip.mp4").write_bytes(b"fake")
            record = {
                "filename": "clip.mp4",
                "file_modified_at": "2026-03-26T15:00:00+00:00",
                "classification_status": "captions_ready",
                "media": {"probe_status": "ok"},
                "sample_frames": {
                    "status": "ok",
                    "frames": [
                        {"status": "ok", "caption": {"text": "A frame caption."}},
                        {"status": "ok"},
                    ],
                },
                "suggested_tags": ["drone", "outdoor"],
            }
            (items / "item.json").write_text(json.dumps(record), encoding="utf-8")

            report = build_status_report(ingest, output)
            text = render_status_text(report)

        self.assertEqual(report["ingest_count"], 1)
        self.assertEqual(report["item_count"], 1)
        self.assertEqual(report["frames_written"], 2)
        self.assertEqual(report["frames_captioned"], 1)
        self.assertIn("captions_ready", report["status_counts"])
        self.assertIn("Argus Status", text)

    def test_index_output_items_and_search_index(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            output = root / "output"
            items = output / "items"
            items.mkdir(parents=True)

            record = {
                "id": "video-1",
                "filename": "warehouse.mp4",
                "path": "/tmp/warehouse.mp4",
                "extension": ".mp4",
                "file_created_at": "2026-03-26T15:00:00+00:00",
                "file_modified_at": "2026-03-26T15:00:00+00:00",
                "classification_status": "captions_ready",
                "audio_required": False,
                "media": {
                    "duration_seconds": 12.5,
                    "has_audio": False,
                    "video": {
                        "codec": "h264",
                        "width": 1920,
                        "height": 1080,
                        "frame_rate": 24.0,
                    },
                },
                "summary": "Two men talk in a warehouse aisle beside stacked cardboard boxes.",
                "suggested_tags": ["warehouse", "boxes", "conversation"],
                "classification": {"model": "gemma3"},
                "sample_frames": {
                    "frames": [
                        {
                            "index": 1,
                            "timestamp_seconds": 2.0,
                            "path": "/tmp/frame.jpg",
                            "status": "ok",
                            "caption": {
                                "text": "Two men stand in front of warehouse shelves.",
                                "tags": ["warehouse", "shelves"],
                                "visible_text": [],
                            },
                        }
                    ]
                },
            }
            (items / "video-1.json").write_text(json.dumps(record), encoding="utf-8")

            report = index_output_items(output)
            results = search_index(Path(report["db_path"]), "warehouse", limit=5)

        self.assertEqual(report["indexed_videos"], 1)
        self.assertEqual(report["indexed_frames"], 1)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["filename"], "warehouse.mp4")

    @patch("argus.cli.index_output_items")
    @patch("argus.cli.caption_output_items")
    @patch("argus.cli.run_scan")
    def test_cli_run_command_executes_pipeline(
        self, run_scan_mock, caption_output_items_mock, index_output_items_mock
    ) -> None:
        from argus.cli import main

        run_scan_mock.return_value = {"file_count": 2}
        caption_output_items_mock.return_value = {"frames_captioned": 8}
        index_output_items_mock.return_value = {
            "indexed_videos": 2,
            "db_path": "/tmp/argus.db",
        }

        exit_code = main(
            [
                "run",
                "/Volumes/Shared/Footage",
                "--output-dir",
                "/tmp/argus-output",
                "--model",
                "gemma3",
            ]
        )

        self.assertEqual(exit_code, 0)
        run_scan_mock.assert_called_once()
        caption_output_items_mock.assert_called_once()
        index_output_items_mock.assert_called_once()

    def test_query_videos_without_search_returns_recent_rows(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            output = root / "output"
            items = output / "items"
            items.mkdir(parents=True)

            for index in range(2):
                record = {
                    "id": f"video-{index}",
                    "filename": f"clip-{index}.mp4",
                    "path": f"/tmp/clip-{index}.mp4",
                    "extension": ".mp4",
                    "file_created_at": "2026-03-26T15:00:00+00:00",
                    "file_modified_at": f"2026-03-26T15:0{index}:00+00:00",
                    "classification_status": "captions_ready",
                    "audio_required": False,
                    "media": {"video": {}},
                    "summary": f"clip {index}",
                    "suggested_tags": [f"tag-{index}"],
                    "sample_frames": {"frames": []},
                }
                (items / f"video-{index}.json").write_text(json.dumps(record), encoding="utf-8")

            report = index_output_items(output)
            from argus.database import query_videos

            results = query_videos(Path(report["db_path"]), limit=10)

        self.assertEqual(len(results), 2)
        self.assertEqual(results[0]["filename"], "clip-1.mp4")

    @patch("argus.captioner.captioning_preflight")
    @patch("argus.captioner.caption_item_record")
    def test_caption_output_items_writes_progress_file(
        self, caption_item_record_mock, captioning_preflight_mock
    ) -> None:
        captioning_preflight_mock.return_value = {"status": "available", "reason": None}
        caption_item_record_mock.side_effect = (
            lambda *args, **kwargs: (False, kwargs["progress_state"])
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            items = output_dir / "items"
            items.mkdir(parents=True)
            (items / "item.json").write_text(
                json.dumps({"filename": "clip.mp4", "sample_frames": {"frames": []}}),
                encoding="utf-8",
            )

            caption_output_items(output_dir)
            progress = load_progress(output_dir)

        self.assertIsNotNone(progress)
        self.assertEqual(progress["phase"], "caption")
        self.assertEqual(progress["status"], "completed")


if __name__ == "__main__":
    unittest.main()
