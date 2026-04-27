from __future__ import annotations

import csv
import io
import json
import tempfile
import unittest
from pathlib import Path

from argus.database import index_output_items
from argus.serve import (
    BLACKBOX_CSV_HEADER_ROW,
    BLACKBOX_EXPORT_CATEGORY,
    batch_name_from_video_path,
    build_blackbox_csv_text,
    parse_search_params,
)


class BlackboxExportTests(unittest.TestCase):
    def test_header_matches_metadata_example(self) -> None:
        with open(
            Path(__file__).resolve().parent.parent / "metadata_example.csv",
            encoding="utf-8",
        ) as f:
            first_line = f.readline().rstrip("\r\n")
        reader = csv.reader(io.StringIO(first_line))
        expected = next(reader)
        self.assertEqual(BLACKBOX_CSV_HEADER_ROW, tuple(expected))

    def test_batch_name_from_immediate_parent_only(self) -> None:
        self.assertEqual(
            batch_name_from_video_path(
                "/tmp/Gensan Glan Escapade 2026/clip.mov"
            ),
            "gensan-glan-escapade-2026",
        )
        self.assertEqual(
            batch_name_from_video_path(
                "/Volumes/Media/Gensan Glan Escapade 2026/aerial-sea-0004.mov"
            ),
            "gensan-glan-escapade-2026",
        )
        self.assertEqual(batch_name_from_video_path("clip.mov"), "")

    def test_parse_search_params(self) -> None:
        self.assertEqual(
            parse_search_params("q=hello&status=captions_ready&limit=10"),
            ("hello", "captions_ready", 10),
        )
        self.assertEqual(
            parse_search_params(""),
            ("", None, 25),
        )
        self.assertEqual(
            parse_search_params("limit=500"),
            ("", None, 100),
        )
        self.assertEqual(
            parse_search_params("limit=notint"),
            ("", None, 25),
        )

    def test_build_blackbox_csv_text_mapping(self) -> None:
        text = build_blackbox_csv_text(
            [
                {
                    "id": "a",
                    "filename": "clip.mov",
                    "path": "/tmp/batch one/clip.mov",
                    "summary": "A test summary for the description field.",
                    "suggested_tags": ["a", "b", "c"],
                    "title": "My title",
                }
            ]
        )
        lines = [ln for ln in text.splitlines() if ln.strip()]
        self.assertIn("#Keep this line: File Name", lines[0])
        reader = csv.reader(io.StringIO(text))
        header = next(reader)
        self.assertEqual(tuple(header), BLACKBOX_CSV_HEADER_ROW)
        row = next(reader)
        self.assertEqual(row[0], "clip.mov")
        self.assertEqual(row[1], "A test summary for the description field.")
        self.assertEqual(row[2], "a, b, c")
        self.assertEqual(row[3], BLACKBOX_EXPORT_CATEGORY)
        self.assertEqual(row[4], "batch-one")
        for col in (5, 6, 7, 8, 9, 10, 12, 13):
            self.assertEqual(row[col], "", msg=f"column {col} should be blank")
        self.assertEqual(row[11], "My title")

    def test_index_and_csv_export_integration(self) -> None:
        from argus.database import query_videos

        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "output"
            items = output / "items"
            items.mkdir(parents=True)
            record = {
                "id": "video-1",
                "filename": "x.mp4",
                "path": f"{temp_dir}/Sub Folder Name Here/x.mp4",
                "extension": ".mp4",
                "file_created_at": "2026-03-26T15:00:00+00:00",
                "file_modified_at": "2026-03-26T16:00:00+00:00",
                "classification_status": "captions_ready",
                "audio_required": False,
                "media": {"video": {}},
                "title": "T",
                "summary": "S" * 20,
                "suggested_tags": ["a", "b"],
                "sample_frames": {"frames": []},
            }
            (items / "video-1.json").write_text(
                json.dumps(record), encoding="utf-8"
            )
            report = index_output_items(output)
            results = query_videos(Path(report["db_path"]), query="", limit=5)
            text = build_blackbox_csv_text(results)
            row = list(csv.reader(io.StringIO(text)))[1]
        self.assertEqual(row[0], "x.mp4")
        self.assertEqual(row[3], "Travel")
        self.assertEqual(row[4], "sub-folder-name-here")


if __name__ == "__main__":
    unittest.main()
