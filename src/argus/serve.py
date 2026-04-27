from __future__ import annotations

import csv
import io
import json
import re
import subprocess
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from argus.database import fetch_status_options, get_video_path, query_videos

# Blackbox CSV: header text must match metadata_example.csv exactly.
BLACKBOX_CSV_HEADER_ROW: tuple[str, ...] = (
    "#Keep this line: File Name",
    "Description (min 15, max 200 characters, must be least 5 words)",
    "Keywords (min 8, max 49, separated by comma, and no repetition)",
    "Category (use dropdown menu)",
    "Batch name (Batch name is not applicable for curator)",
    "Editorial (use dropdown menu)",
    "Editorial Text",
    "Editorial City",
    "Editorial State",
    "Editorial Country (use dropdown menu)",
    "Editorial Date",
    "Title (Optional)",
    "Shooting Country (Optional)",
    "Shooting Date (Optional)",
)

BLACKBOX_EXPORT_CATEGORY = "Travel"


def batch_name_from_video_path(path: str) -> str:
    """Return slug from the file's immediate parent directory only (not full path)."""
    p = Path(path)
    if not path or p.name == p.anchor:
        return ""
    parent = p.parent
    if not str(parent) or parent == p:
        return ""
    raw = parent.name.strip()
    if not raw:
        return ""
    slug = re.sub(r"[\s_]+", "-", raw.lower().strip())
    slug = re.sub(r"-+", "-", slug)
    return slug.strip("-")


def build_blackbox_csv_text(results: list[dict]) -> str:
    """Build UTF-8 Blackbox CSV text (no BOM) for search result dicts from query_videos."""
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer)
    writer.writerow(BLACKBOX_CSV_HEADER_ROW)
    for item in results:
        tags = item.get("suggested_tags") or []
        keyword_text = ", ".join(str(t) for t in tags)
        pth = item.get("path")
        path_str = pth if isinstance(pth, str) else str(pth or "")
        row = [
            item.get("filename") or "",
            item.get("summary") or "",
            keyword_text,
            BLACKBOX_EXPORT_CATEGORY,
            batch_name_from_video_path(path_str),
            "",
            "",
            "",
            "",
            "",
            "",
            item.get("title") or "",
            "",
            "",
        ]
        writer.writerow(row)
    return buffer.getvalue()


def build_blackbox_csv_bytes(results: list[dict]) -> bytes:
    return build_blackbox_csv_text(results).encode("utf-8")


def parse_search_params(query: str) -> tuple[str, str | None, int]:
    """Shared query string parsing for /api/search and /api/export/blackbox."""
    params = parse_qs(query)
    text = params.get("q", [""])[0]
    status = params.get("status", [""])[0] or None
    limit_text = params.get("limit", ["25"])[0]
    try:
        limit = max(1, min(100, int(limit_text)))
    except ValueError:
        limit = 25
    return text, status, limit

DEMO_RESULTS = [
    {
        "id": "demo-001",
        "filename": "clip-001.mp4",
        "path": "/Volumes/Media/Project-A/clip-001.mp4",
        "classification_status": "captions_ready",
        "title": "Office hallway walk with laptop in hand",
        "summary": "A person walks through a bright office hallway while carrying a laptop.",
        "suggested_tags": ["office", "hallway", "person", "walking", "laptop"],
        "duration_seconds": 14.2,
        "width": 1920,
        "height": 1080,
        "match_text": "A person walks through a bright [office] hallway while carrying a laptop.",
        "rank": None,
    },
    {
        "id": "demo-002",
        "filename": "clip-002.mp4",
        "path": "/Volumes/Media/Project-B/clip-002.mp4",
        "classification_status": "captions_ready",
        "title": "Hands packing product boxes on a worktable",
        "summary": "Close-up footage of hands arranging product boxes on a worktable.",
        "suggested_tags": ["close-up", "hands", "boxes", "table", "product"],
        "duration_seconds": 9.6,
        "width": 3840,
        "height": 2160,
        "match_text": "Close-up footage of [hands] arranging product boxes on a worktable.",
        "rank": None,
    },
    {
        "id": "demo-003",
        "filename": "clip-003.mp4",
        "path": "/Volumes/Media/Project-C/clip-003.mp4",
        "classification_status": "captions_ready",
        "title": "Busy storefront exterior with shoppers coming and going",
        "summary": "Wide exterior shot of a storefront with people entering and leaving.",
        "suggested_tags": ["exterior", "wide shot", "storefront", "people"],
        "duration_seconds": 22.8,
        "width": 1920,
        "height": 1080,
        "match_text": "Wide [exterior] shot of a storefront with people entering and leaving.",
        "rank": None,
    },
]


def serve_ui(
    *,
    db_path: Path,
    host: str = "127.0.0.1",
    port: int = 8765,
    open_browser: bool = False,
) -> int:
    db_path = db_path.resolve()
    handler_class = build_handler(db_path)
    server = ThreadingHTTPServer((host, port), handler_class)
    url = f"http://{host}:{port}"
    print(f"Argus UI running at {url}")
    print(f"Database: {db_path}")
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped server.")
    finally:
        server.server_close()
    return 0


def build_handler(db_path: Path) -> type[BaseHTTPRequestHandler]:
    class ArgusHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            if parsed.path == "/":
                self.respond_html(render_index_html())
                return
            if parsed.path == "/demo":
                self.respond_html(render_index_html(demo_mode=True))
                return
            if parsed.path == "/api/meta":
                self.respond_json(
                    {
                        "statuses": fetch_status_options(db_path),
                    }
                )
                return
            if parsed.path == "/api/search":
                query, status, limit = parse_search_params(parsed.query)
                results = query_videos(
                    db_path,
                    query=query,
                    status=status,
                    limit=limit,
                )
                self.respond_json({"results": results, "count": len(results)})
                return
            if parsed.path == "/api/export/blackbox":
                query, status, limit = parse_search_params(parsed.query)
                results = query_videos(
                    db_path,
                    query=query,
                    status=status,
                    limit=limit,
                )
                data = build_blackbox_csv_bytes(results)
                self.respond_bytes(
                    data,
                    "text/csv; charset=utf-8",
                    'attachment; filename="argus-blackbox-export.csv"',
                )
                return
            self.respond_not_found()

        def do_POST(self) -> None:
            parsed = urlparse(self.path)
            if parsed.path == "/api/reveal":
                content_length = int(self.headers.get("Content-Length", "0"))
                raw_body = self.rfile.read(content_length).decode("utf-8") if content_length else "{}"
                try:
                    payload = json.loads(raw_body)
                except json.JSONDecodeError:
                    self.respond_json({"error": "Invalid JSON body"}, status=HTTPStatus.BAD_REQUEST)
                    return

                video_id = payload.get("id")
                if not isinstance(video_id, str) or not video_id:
                    self.respond_json({"error": "Missing video id"}, status=HTTPStatus.BAD_REQUEST)
                    return

                path = get_video_path(db_path, video_id)
                if path is None:
                    self.respond_json({"error": "Video not found"}, status=HTTPStatus.NOT_FOUND)
                    return

                try:
                    subprocess.run(["open", "-R", path], check=True)
                except (OSError, subprocess.CalledProcessError) as exc:
                    self.respond_json(
                        {"error": f"Failed to reveal file: {exc}"},
                        status=HTTPStatus.INTERNAL_SERVER_ERROR,
                    )
                    return

                self.respond_json({"ok": True, "path": path})
                return

            self.respond_not_found()

        def log_message(self, format: str, *args: object) -> None:
            return

        def respond_html(self, body: str, *, status: HTTPStatus = HTTPStatus.OK) -> None:
            encoded = body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def respond_json(self, payload: dict, *, status: HTTPStatus = HTTPStatus.OK) -> None:
            encoded = (json.dumps(payload) + "\n").encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def respond_bytes(
            self,
            data: bytes,
            content_type: str,
            content_disposition: str,
            *,
            status: HTTPStatus = HTTPStatus.OK,
        ) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Disposition", content_disposition)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def respond_not_found(self) -> None:
            self.respond_json({"error": "Not found"}, status=HTTPStatus.NOT_FOUND)

    return ArgusHandler


def render_index_html(*, demo_mode: bool = False) -> str:
    demo_json = json.dumps(DEMO_RESULTS)
    html = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Argus Local Search</title>
  <style>
    :root {
      --paper: #f5efe3;
      --ink: #1e1d1a;
      --muted: #655f56;
      --accent: #bd4b2e;
      --accent-soft: rgba(189, 75, 46, 0.12);
      --panel: rgba(255, 251, 244, 0.92);
      --line: rgba(30, 29, 26, 0.11);
      --shadow: 0 24px 80px rgba(30, 29, 26, 0.12);
      --radius: 22px;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: "Iowan Old Style", "Palatino Linotype", "Book Antiqua", serif;
      color: var(--ink);
      background:
        radial-gradient(circle at top left, rgba(189, 75, 46, 0.18), transparent 28rem),
        radial-gradient(circle at bottom right, rgba(81, 124, 97, 0.14), transparent 24rem),
        linear-gradient(180deg, #fbf6ee 0%, var(--paper) 100%);
      min-height: 100vh;
    }
    body::before {
      content: "";
      position: fixed;
      inset: 0;
      pointer-events: none;
      opacity: 0.22;
      background-image:
        linear-gradient(rgba(30, 29, 26, 0.06) 1px, transparent 1px),
        linear-gradient(90deg, rgba(30, 29, 26, 0.06) 1px, transparent 1px);
      background-size: 32px 32px;
      mask-image: linear-gradient(180deg, rgba(0,0,0,0.55), transparent 88%);
    }
    main {
      width: min(1120px, calc(100vw - 2rem));
      margin: 0 auto;
      padding: 2rem 0 4rem;
    }
    .hero {
      display: grid;
      gap: 1rem;
      padding: 1.5rem 0 1rem;
    }
    .eyebrow {
      display: inline-flex;
      align-items: center;
      gap: 0.5rem;
      width: fit-content;
      padding: 0.45rem 0.8rem;
      border-radius: 999px;
      border: 1px solid var(--line);
      background: rgba(255,255,255,0.55);
      font-family: "SF Mono", "IBM Plex Mono", ui-monospace, monospace;
      font-size: 0.78rem;
      letter-spacing: 0.08em;
      text-transform: uppercase;
    }
    h1 {
      margin: 0;
      font-size: clamp(2.6rem, 9vw, 6rem);
      line-height: 0.92;
      letter-spacing: -0.04em;
      max-width: 10ch;
    }
    .hero p {
      margin: 0;
      max-width: 56rem;
      color: var(--muted);
      font-size: 1.04rem;
      line-height: 1.6;
    }
    .panel {
      border: 1px solid var(--line);
      background: var(--panel);
      border-radius: var(--radius);
      box-shadow: var(--shadow);
      backdrop-filter: blur(14px);
    }
    .controls {
      display: grid;
      gap: 1rem;
      padding: 1rem;
      grid-template-columns: 1.6fr 0.8fr 0.45fr;
      position: sticky;
      top: 1rem;
      z-index: 5;
      margin-bottom: 1rem;
    }
    .control {
      display: grid;
      gap: 0.4rem;
    }
    label {
      font-family: "SF Mono", "IBM Plex Mono", ui-monospace, monospace;
      font-size: 0.76rem;
      text-transform: uppercase;
      letter-spacing: 0.08em;
      color: var(--muted);
    }
    input, select, button {
      font: inherit;
    }
    input[type="search"], select {
      width: 100%;
      border: 1px solid var(--line);
      border-radius: 16px;
      background: rgba(255,255,255,0.78);
      color: var(--ink);
      padding: 0.95rem 1rem;
      outline: none;
    }
    input[type="search"]:focus, select:focus {
      border-color: rgba(189, 75, 46, 0.5);
      box-shadow: 0 0 0 4px rgba(189, 75, 46, 0.12);
    }
    .meta {
      display: flex;
      justify-content: space-between;
      align-items: center;
      flex-wrap: wrap;
      gap: 0.75rem 1rem;
      padding: 0 0.3rem 1rem;
      color: var(--muted);
      font-family: "SF Mono", "IBM Plex Mono", ui-monospace, monospace;
      font-size: 0.84rem;
    }
    .results {
      display: grid;
      gap: 1rem;
    }
    .card {
      padding: 1.1rem 1.15rem 1rem;
      display: grid;
      gap: 0.9rem;
    }
    .card-top {
      display: flex;
      justify-content: space-between;
      gap: 1rem;
      align-items: start;
    }
    .card h2 {
      margin: 0 0 0.25rem;
      font-size: 1.45rem;
      line-height: 1.02;
    }
    .status-pill {
      padding: 0.45rem 0.7rem;
      border-radius: 999px;
      background: var(--accent-soft);
      color: var(--accent);
      font-family: "SF Mono", "IBM Plex Mono", ui-monospace, monospace;
      font-size: 0.74rem;
      text-transform: uppercase;
      letter-spacing: 0.08em;
      white-space: nowrap;
    }
    .path {
      margin: 0;
      color: var(--muted);
      font-family: "SF Mono", "IBM Plex Mono", ui-monospace, monospace;
      font-size: 0.82rem;
      word-break: break-all;
    }
    .clip-title {
      margin: 0 0 0.35rem;
      font-size: 1.05rem;
      font-weight: 600;
      line-height: 1.35;
      color: var(--ink);
      letter-spacing: -0.01em;
    }
    .summary, .match {
      margin: 0;
      line-height: 1.55;
      color: var(--ink);
    }
    .match {
      color: var(--muted);
      font-style: italic;
    }
    .stats {
      display: flex;
      gap: 0.75rem;
      flex-wrap: wrap;
      font-family: "SF Mono", "IBM Plex Mono", ui-monospace, monospace;
      font-size: 0.8rem;
      color: var(--muted);
    }
    .tags {
      display: flex;
      gap: 0.55rem;
      flex-wrap: wrap;
    }
    .tag {
      padding: 0.5rem 0.72rem;
      border-radius: 999px;
      background: rgba(30, 29, 26, 0.06);
      font-family: "SF Mono", "IBM Plex Mono", ui-monospace, monospace;
      font-size: 0.76rem;
    }
    .actions {
      display: flex;
      gap: 0.75rem;
      flex-wrap: wrap;
    }
    .button {
      border: 1px solid var(--line);
      background: rgba(255,255,255,0.82);
      border-radius: 14px;
      padding: 0.72rem 0.95rem;
      cursor: pointer;
      transition: transform 150ms ease, border-color 150ms ease, background 150ms ease;
      font-family: "SF Mono", "IBM Plex Mono", ui-monospace, monospace;
      font-size: 0.8rem;
    }
    .button:hover {
      transform: translateY(-1px);
      border-color: rgba(189, 75, 46, 0.45);
      background: rgba(255,255,255,0.96);
    }
    .empty {
      padding: 1.6rem;
      text-align: center;
      color: var(--muted);
    }
    .demo-banner {
      margin-bottom: 1rem;
      padding: 0.9rem 1rem;
      border: 1px dashed rgba(189, 75, 46, 0.38);
      border-radius: 18px;
      background: rgba(189, 75, 46, 0.08);
      font-family: "SF Mono", "IBM Plex Mono", ui-monospace, monospace;
      font-size: 0.82rem;
      color: var(--muted);
    }
    .toast {
      position: fixed;
      right: 1rem;
      bottom: 1rem;
      padding: 0.85rem 1rem;
      border-radius: 16px;
      background: rgba(30, 29, 26, 0.92);
      color: #fff;
      font-family: "SF Mono", "IBM Plex Mono", ui-monospace, monospace;
      font-size: 0.8rem;
      opacity: 0;
      transform: translateY(10px);
      pointer-events: none;
      transition: opacity 180ms ease, transform 180ms ease;
    }
    .toast.show {
      opacity: 1;
      transform: translateY(0);
    }
    mark {
      background: rgba(189, 75, 46, 0.16);
      color: inherit;
      padding: 0.05em 0.22em;
      border-radius: 0.25em;
    }
    @media (max-width: 820px) {
      .controls {
        grid-template-columns: 1fr;
      }
      .card-top {
        flex-direction: column;
      }
    }
  </style>
</head>
<body>
  <main>
    <section class="hero">
      <span class="eyebrow">Argus Local Search</span>
      <h1>Find clips by what’s actually in them.</h1>
      <p>
        Search summaries, tags, frame captions, and visible text from your local SQLite index.
        Copy the path or reveal the source file directly in Finder.
      </p>
    </section>

    <section class="controls panel">
      <div class="control">
        <label for="query">Search</label>
        <input id="query" type="search" placeholder="Search by filename, title, tag, summary, caption, or visible text" autocomplete="off">
      </div>
      <div class="control">
        <label for="status">Status</label>
        <select id="status">
          <option value="">All statuses</option>
        </select>
      </div>
      <div class="control">
        <label for="limit">Limit</label>
        <select id="limit">
          <option value="10">10</option>
          <option value="25" selected>25</option>
          <option value="50">50</option>
        </select>
      </div>
    </section>

    <div class="meta">
      <span id="resultCount">Loading…</span>
      <button type="button" class="button" id="exportBlackbox">Export to CSV (Blackbox)</button>
      <span>localhost only</span>
    </div>

    __DEMO_BANNER__

    <section id="results" class="results"></section>
  </main>

  <div id="toast" class="toast"></div>

  <script>
    const queryInput = document.getElementById("query");
    const statusSelect = document.getElementById("status");
    const limitSelect = document.getElementById("limit");
    const resultsEl = document.getElementById("results");
    const resultCountEl = document.getElementById("resultCount");
    const toastEl = document.getElementById("toast");
    const demoMode = __DEMO_MODE__;
    const demoResults = __DEMO_JSON__;

    const BLACKBOX_CSV_HEADER = [
      "#Keep this line: File Name",
      "Description (min 15, max 200 characters, must be least 5 words)",
      "Keywords (min 8, max 49, separated by comma, and no repetition)",
      "Category (use dropdown menu)",
      "Batch name (Batch name is not applicable for curator)",
      "Editorial (use dropdown menu)",
      "Editorial Text",
      "Editorial City",
      "Editorial State",
      "Editorial Country (use dropdown menu)",
      "Editorial Date",
      "Title (Optional)",
      "Shooting Country (Optional)",
      "Shooting Date (Optional)"
    ];

    let debounceTimer = null;
    let lastResults = [];

    async function loadMeta() {
      if (demoMode) {
        const statuses = [...new Set(demoResults.map((item) => item.classification_status))].sort();
        for (const status of statuses) {
          const option = document.createElement("option");
          option.value = status;
          option.textContent = status;
          statusSelect.appendChild(option);
        }
        return;
      }
      const response = await fetch("/api/meta");
      const payload = await response.json();
      for (const status of payload.statuses) {
        const option = document.createElement("option");
        option.value = status;
        option.textContent = status;
        statusSelect.appendChild(option);
      }
    }

    function showToast(message) {
      toastEl.textContent = message;
      toastEl.classList.add("show");
      window.clearTimeout(showToast._timer);
      showToast._timer = window.setTimeout(() => {
        toastEl.classList.remove("show");
      }, 1800);
    }

    function csvEscapeCell(value) {
      const s = value == null ? "" : String(value);
      if (
        s.includes(",")
        || s.indexOf(34) >= 0
        || s.includes(String.fromCharCode(10))
        || s.includes(String.fromCharCode(13))
      ) {
        return (
          String.fromCharCode(34)
          + s.replaceAll(
              String.fromCharCode(34),
              String.fromCharCode(34) + String.fromCharCode(34)
            )
          + String.fromCharCode(34)
        );
      }
      return s;
    }

    function batchNameFromPathClient(path) {
      if (!path) return "";
      const norm = path.split(String.fromCharCode(92)).join("/");
      const parts = norm.split("/").filter(Boolean);
      if (parts.length < 2) return "";
      const parent = parts[parts.length - 2];
      if (!parent) return "";
      return parent
        .trim()
        .toLowerCase()
        .split(/[\\s_]+/)
        .filter(Boolean)
        .join("-");
    }

    function buildBlackboxCsvTextClient(results) {
      const lines = [BLACKBOX_CSV_HEADER.map(csvEscapeCell).join(",")];
      for (const r of results) {
        const kw = (r.suggested_tags || []).map(String).join(", ");
        const row = [
          r.filename || "",
          r.summary || "",
          kw,
          "Travel",
          batchNameFromPathClient(r.path || ""),
          "", "", "", "", "", "",
          r.title || "",
          "", ""
        ];
        lines.push(row.map(csvEscapeCell).join(","));
      }
      return lines.join(String.fromCharCode(10));
    }

    function highlightBrackets(text) {
      return text.replaceAll("[", "<mark>").replaceAll("]", "</mark>");
    }

    function renderResults(results) {
      lastResults = results;
      resultCountEl.textContent = `${results.length} result${results.length === 1 ? "" : "s"}`;
      if (!results.length) {
        resultsEl.innerHTML = `<article class="panel empty">No matches yet. Try a broader search or clear the status filter.</article>`;
        return;
      }

      resultsEl.innerHTML = results.map((result) => {
        const tags = (result.suggested_tags || []).map((tag) => `<span class="tag">${tag}</span>`).join("");
        const clipTitle = result.title ? `<p class="clip-title">${result.title}</p>` : "";
        const summary = result.summary ? `<p class="summary">${result.summary}</p>` : "";
        const match = result.match_text ? `<p class="match">${highlightBrackets(result.match_text)}</p>` : "";
        const duration = typeof result.duration_seconds === "number" ? `${result.duration_seconds.toFixed(2)}s` : "unknown";
        const resolution = result.width && result.height ? `${result.width}×${result.height}` : "unknown";
        return `
          <article class="panel card" data-id="${result.id}">
            <div class="card-top">
              <div>
                <h2>${result.filename}</h2>
                <p class="path">${result.path}</p>
              </div>
              <span class="status-pill">${result.classification_status || "unknown"}</span>
            </div>
            <div class="stats">
              <span>${duration}</span>
              <span>${resolution}</span>
            </div>
            ${clipTitle}
            ${summary}
            ${match}
            <div class="tags">${tags}</div>
            <div class="actions">
              <button class="button" data-action="copy">Copy Path</button>
              <button class="button" data-action="reveal">Reveal in Finder</button>
            </div>
          </article>
        `;
      }).join("");
    }

    async function runSearch() {
      if (demoMode) {
        const query = queryInput.value.trim().toLowerCase();
        const status = statusSelect.value;
        const limit = Number(limitSelect.value || 25);
        const filtered = demoResults.filter((result) => {
          if (status && result.classification_status !== status) return false;
          if (!query) return true;
          const haystack = [
            result.filename,
            result.path,
            result.title,
            result.summary,
            ...(result.suggested_tags || []),
            result.match_text
          ].join(" ").toLowerCase();
          return haystack.includes(query);
        }).slice(0, limit);
        renderResults(filtered);
        return;
      }
      const params = new URLSearchParams({
        q: queryInput.value,
        status: statusSelect.value,
        limit: limitSelect.value
      });
      resultCountEl.textContent = "Searching…";
      const response = await fetch(`/api/search?${params.toString()}`);
      const payload = await response.json();
      renderResults(payload.results || []);
    }

    function scheduleSearch() {
      window.clearTimeout(debounceTimer);
      debounceTimer = window.setTimeout(runSearch, 180);
    }

    resultsEl.addEventListener("click", async (event) => {
      const button = event.target.closest("button[data-action]");
      if (!button) return;
      const card = event.target.closest("[data-id]");
      const id = card?.dataset.id;
      const path = card?.querySelector(".path")?.textContent || "";
      if (!id) return;

      if (button.dataset.action === "copy") {
        await navigator.clipboard.writeText(path);
        showToast("Path copied");
        return;
      }

      if (button.dataset.action === "reveal") {
        if (demoMode) {
          showToast("Reveal is disabled in demo mode");
          return;
        }
        const response = await fetch("/api/reveal", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ id })
        });
        if (response.ok) {
          showToast("Revealed in Finder");
        } else {
          const payload = await response.json();
          showToast(payload.error || "Reveal failed");
        }
      }
    });

    const exportBtn = document.getElementById("exportBlackbox");
    exportBtn.addEventListener("click", async () => {
      if (demoMode) {
        const text = buildBlackboxCsvTextClient(lastResults);
        const blob = new Blob([text], { type: "text/csv;charset=utf-8" });
        const url = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url;
        a.download = "argus-blackbox-export.csv";
        a.click();
        URL.revokeObjectURL(url);
        showToast("Exported CSV (Blackbox)");
        return;
      }
      const params = new URLSearchParams({
        q: queryInput.value,
        status: statusSelect.value,
        limit: limitSelect.value
      });
      const response = await fetch(`/api/export/blackbox?${params.toString()}`);
      if (!response.ok) {
        showToast("Export failed");
        return;
      }
      const blob = await response.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = "argus-blackbox-export.csv";
      a.click();
      URL.revokeObjectURL(url);
      showToast("Exported CSV (Blackbox)");
    });

    queryInput.addEventListener("input", scheduleSearch);
    statusSelect.addEventListener("change", runSearch);
    limitSelect.addEventListener("change", runSearch);

    loadMeta().then(() => {
      if (demoMode) {
        renderResults(demoResults.slice(0, Number(limitSelect.value || 25)));
        return;
      }
      resultCountEl.textContent = "Type a search or choose a filter to begin.";
      resultsEl.innerHTML = `<article class="panel empty">Start with a keyword, tag, filename, or status filter. The UI runs entirely on your local machine.</article>`;
    });
  </script>
</body>
</html>"""
    demo_banner = (
        "<div class='demo-banner'>Demo mode is showing example records only. Reveal-in-Finder is disabled.</div>"
        if demo_mode
        else ""
    )
    return (
        html.replace("__DEMO_MODE__", "true" if demo_mode else "false")
        .replace("__DEMO_JSON__", demo_json)
        .replace("__DEMO_BANNER__", demo_banner)
    )
