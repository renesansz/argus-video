from __future__ import annotations

import json
import sqlite3
from pathlib import Path


def default_db_path(output_dir: Path) -> Path:
    return output_dir.resolve() / "argus.db"


def index_output_items(output_dir: Path, db_path: Path | None = None) -> dict:
    output_dir = output_dir.resolve()
    items_dir = output_dir / "items"
    db_path = (db_path or default_db_path(output_dir)).resolve()
    db_path.parent.mkdir(parents=True, exist_ok=True)

    item_records = load_item_records(items_dir)

    connection = sqlite3.connect(db_path)
    try:
        configure_connection(connection)
        create_schema(connection)
        reset_index(connection)

        indexed_videos = 0
        indexed_frames = 0
        for record in item_records:
            indexed_videos += 1
            indexed_frames += index_item_record(connection, record)

        connection.commit()
    finally:
        connection.close()

    return {
        "db_path": str(db_path),
        "indexed_videos": indexed_videos,
        "indexed_frames": indexed_frames,
    }


def search_index(db_path: Path, query: str, *, limit: int = 10) -> list[dict]:
    db_path = db_path.resolve()
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute(
            """
            SELECT
              videos.id,
              videos.filename,
              videos.path,
              videos.classification_status,
              videos.title,
              videos.summary,
              videos.suggested_tags_json,
              videos.duration_seconds,
              videos.width,
              videos.height,
              video_search.content AS indexed_content,
              bm25(video_search) AS rank
            FROM video_search
            JOIN videos ON videos.id = video_search.video_id
            WHERE video_search MATCH ?
            ORDER BY rank
            LIMIT ?
            """,
            (query, limit),
        ).fetchall()
    finally:
        connection.close()

    results = []
    for row in rows:
        results.append(
            {
                "id": row["id"],
                "filename": row["filename"],
                "path": row["path"],
                "classification_status": row["classification_status"],
                "title": row["title"],
                "summary": row["summary"],
                "suggested_tags": json.loads(row["suggested_tags_json"] or "[]"),
                "duration_seconds": row["duration_seconds"],
                "width": row["width"],
                "height": row["height"],
                "match_text": build_match_excerpt(row["indexed_content"] or "", query),
                "rank": row["rank"],
            }
        )
    return results


def query_videos(
    db_path: Path,
    *,
    query: str = "",
    status: str | None = None,
    limit: int = 25,
) -> list[dict]:
    query = query.strip()
    if query:
        results = search_index(db_path, query, limit=max(limit * 3, limit))
        if status:
            results = [
                result
                for result in results
                if result["classification_status"] == status
            ]
        return results[:limit]

    db_path = db_path.resolve()
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    try:
        sql = """
            SELECT
              id,
              filename,
              path,
              classification_status,
              title,
              summary,
              suggested_tags_json,
              duration_seconds,
              width,
              height
            FROM videos
        """
        params: list[object] = []
        if status:
            sql += " WHERE classification_status = ?"
            params.append(status)
        sql += " ORDER BY file_modified_at DESC LIMIT ?"
        params.append(limit)
        rows = connection.execute(sql, params).fetchall()
    finally:
        connection.close()

    return [row_to_result(row, match_text="") for row in rows]


def fetch_status_options(db_path: Path) -> list[str]:
    db_path = db_path.resolve()
    connection = sqlite3.connect(db_path)
    try:
        rows = connection.execute(
            """
            SELECT DISTINCT classification_status
            FROM videos
            WHERE classification_status IS NOT NULL AND classification_status != ''
            ORDER BY classification_status
            """
        ).fetchall()
    finally:
        connection.close()
    return [row[0] for row in rows]


def get_video_path(db_path: Path, video_id: str) -> str | None:
    db_path = db_path.resolve()
    connection = sqlite3.connect(db_path)
    try:
        row = connection.execute(
            "SELECT path FROM videos WHERE id = ?",
            (video_id,),
        ).fetchone()
    finally:
        connection.close()
    return row[0] if row else None


def configure_connection(connection: sqlite3.Connection) -> None:
    connection.execute("PRAGMA journal_mode = WAL")
    connection.execute("PRAGMA foreign_keys = ON")


def create_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS videos (
          id TEXT PRIMARY KEY,
          filename TEXT NOT NULL,
          path TEXT NOT NULL,
          extension TEXT,
          file_created_at TEXT,
          file_modified_at TEXT,
          classification_status TEXT,
          audio_required INTEGER NOT NULL DEFAULT 0,
          duration_seconds REAL,
          codec TEXT,
          width INTEGER,
          height INTEGER,
          frame_rate REAL,
          has_audio INTEGER,
          title TEXT,
          summary TEXT,
          suggested_tags_json TEXT NOT NULL,
          classification_json TEXT,
          raw_json TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS frames (
          video_id TEXT NOT NULL,
          frame_index INTEGER NOT NULL,
          timestamp_seconds REAL,
          path TEXT,
          frame_status TEXT,
          caption_text TEXT,
          tags_json TEXT NOT NULL,
          visible_text_json TEXT NOT NULL,
          PRIMARY KEY (video_id, frame_index),
          FOREIGN KEY (video_id) REFERENCES videos(id) ON DELETE CASCADE
        );

        CREATE VIRTUAL TABLE IF NOT EXISTS video_search USING fts5(
          video_id UNINDEXED,
          content,
          tokenize = 'unicode61'
        );
        """
    )
    ensure_videos_title_column(connection)


def ensure_videos_title_column(connection: sqlite3.Connection) -> None:
    """Add title column when upgrading an existing database."""
    rows = connection.execute("PRAGMA table_info(videos)").fetchall()
    column_names = {row[1] for row in rows}
    if "title" not in column_names:
        connection.execute("ALTER TABLE videos ADD COLUMN title TEXT")


def reset_index(connection: sqlite3.Connection) -> None:
    connection.execute("DELETE FROM frames")
    connection.execute("DELETE FROM videos")
    connection.execute("DELETE FROM video_search")


def index_item_record(connection: sqlite3.Connection, record: dict) -> int:
    media = record.get("media", {})
    video = media.get("video", {})
    suggested_tags = record.get("suggested_tags", [])

    connection.execute(
        """
        INSERT INTO videos (
          id, filename, path, extension, file_created_at, file_modified_at,
          classification_status, audio_required, duration_seconds, codec,
          width, height, frame_rate, has_audio, title, summary, suggested_tags_json,
          classification_json, raw_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            record.get("id"),
            record.get("filename"),
            record.get("path"),
            record.get("extension"),
            record.get("file_created_at"),
            record.get("file_modified_at"),
            record.get("classification_status"),
            int(bool(record.get("audio_required"))),
            media.get("duration_seconds"),
            video.get("codec"),
            video.get("width"),
            video.get("height"),
            video.get("frame_rate"),
            none_to_int(media.get("has_audio")),
            record.get("title"),
            record.get("summary"),
            json.dumps(suggested_tags),
            json.dumps(record.get("classification", {})),
            json.dumps(record),
        ),
    )

    frames = record.get("sample_frames", {}).get("frames", [])
    frame_count = 0
    searchable_chunks = [
        record.get("filename", ""),
        record.get("path", ""),
        record.get("title", "") or "",
        record.get("summary", ""),
        " ".join(suggested_tags),
    ]
    for frame in frames:
        caption = frame.get("caption", {})
        caption_text = caption.get("text")
        frame_tags = caption.get("tags", [])
        visible_text = caption.get("visible_text", [])

        connection.execute(
            """
            INSERT INTO frames (
              video_id, frame_index, timestamp_seconds, path, frame_status,
              caption_text, tags_json, visible_text_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record.get("id"),
                frame.get("index"),
                frame.get("timestamp_seconds"),
                frame.get("path"),
                frame.get("status"),
                caption_text,
                json.dumps(frame_tags),
                json.dumps(visible_text),
            ),
        )
        frame_count += 1
        searchable_chunks.extend(
            [
                caption_text or "",
                " ".join(frame_tags),
                " ".join(visible_text),
            ]
        )

    connection.execute(
        """
        INSERT INTO video_search (video_id, content)
        VALUES (?, ?)
        """,
        (
            record.get("id"),
            "\n".join(chunk for chunk in searchable_chunks if chunk),
        ),
    )

    return frame_count


def load_item_records(items_dir: Path) -> list[dict]:
    if not items_dir.exists():
        return []
    return [
        json.loads(item_path.read_text(encoding="utf-8"))
        for item_path in sorted(items_dir.glob("*.json"))
    ]


def none_to_int(value: bool | None) -> int | None:
    if value is None:
        return None
    return int(bool(value))


def build_match_excerpt(content: str, query: str, *, width: int = 160) -> str:
    if not content:
        return ""

    lowered = content.lower()
    terms = [term.strip('"').lower() for term in query.split() if term.strip()]
    first_index = -1
    matched_term = ""
    for term in terms:
        position = lowered.find(term)
        if position != -1 and (first_index == -1 or position < first_index):
            first_index = position
            matched_term = term

    if first_index == -1:
        excerpt = content[:width]
    else:
        start = max(0, first_index - 40)
        end = min(len(content), start + width)
        excerpt = content[start:end]
        if matched_term:
            excerpt = highlight_term(excerpt, matched_term)

    excerpt = " ".join(excerpt.split())
    if len(excerpt) < len(content):
        return excerpt + " …"
    return excerpt


def highlight_term(text: str, term: str) -> str:
    lowered = text.lower()
    index = lowered.find(term)
    if index == -1:
        return text
    end = index + len(term)
    return text[:index] + "[" + text[index:end] + "]" + text[end:]


def row_to_result(row: sqlite3.Row, *, match_text: str) -> dict:
    return {
        "id": row["id"],
        "filename": row["filename"],
        "path": row["path"],
        "classification_status": row["classification_status"],
        "title": row["title"],
        "summary": row["summary"],
        "suggested_tags": json.loads(row["suggested_tags_json"] or "[]"),
        "duration_seconds": row["duration_seconds"],
        "width": row["width"],
        "height": row["height"],
        "match_text": match_text,
        "rank": row["rank"] if "rank" in row.keys() else None,
    }
