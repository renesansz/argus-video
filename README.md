# Argus

Local-first video library indexing for creators and agencies.

Argus is meant to scan a folder of video files, extract useful visual metadata, and produce searchable structured output without sending footage to a cloud API.

## Start Here

Argus is for video teams who want a local workflow, not a cloud asset manager.

If you are comfortable in a terminal, you can run the whole pipeline directly.
If you are not especially technical, the simplest mental model is:

1. point Argus at any source folder
2. run a few commands
3. open the browser UI
4. search by filename, tags, captions, or visible text

The browser UI is intentionally local and simple:

![Argus Browser UI Overview](docs/screenshots/argus-ui-overview.png)

![Argus Browser UI Results](docs/screenshots/argus-ui-results.png)

## First Successful Run

If you want the shortest path from zero to working search, use this:

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -e .
brew install ffmpeg
ollama pull gemma3
argus doctor --model gemma3
argus run /path/to/source/folder --output-dir /path/to/argus-output
argus serve --open-browser
```

Any readable path works, including mounted external drives and mounted network volumes.
If your media lives on a NAS over SMB, mount it in macOS first and use the mounted path under `/Volumes/...`.
Do not pass raw `smb://` URLs to Argus.
For best reliability, keep Argus output local even when the source media is on a network share.

When the browser opens, you can:

- search by content words
- filter by classification status
- copy the source path
- reveal the file in Finder

Example with a mounted NAS share:

```bash
argus run /Volumes/StudioNAS/SourceFootage --output-dir ~/ArgusOutput
argus serve --output-dir ~/ArgusOutput --open-browser
```

If you want a more detailed walkthrough, see [docs/USAGE.md](docs/USAGE.md).

## Current Status

The repository currently includes the first runnable slice:

- a local `ingest` folder for test clips
- a Python CLI that scans supported files
- filesystem metadata extraction
- optional media probing through `ffprobe` when installed
- optional frame sampling through `ffmpeg`
- optional local frame captioning through Ollama
- a `doctor` command for local dependency checks
- JSON manifest output and per-file sidecars

This first cut is intentionally audio-agnostic. Silent clips are a normal input, not an edge case.

## Problem

Video teams accumulate large libraries of B-roll, interviews, and project footage. Reviewing clips by hand is slow, inconsistent, and expensive. The goal is to turn raw video files into machine-readable metadata that can later power:

- local search
- Finder tags on macOS
- clip notes and shot logs
- downstream asset management workflows

## Product Direction

The first version should optimize for:

- local execution
- accuracy over speed
- low setup friction
- open source friendliness
- simple outputs that are easy to inspect and reuse

The first version should not try to solve everything at once.

## MVP

Given a folder of videos, Argus should:

1. scan supported files recursively
2. extract file and media metadata
3. detect representative moments or scenes
4. generate timestamped visual descriptions locally
5. produce suggested tags
6. write structured output to disk
7. optionally build a local SQLite index for search

## Core Constraints

- Must run on a local Mac with 32 GB RAM
- Must work without any cloud inference or API keys
- Can be slow if needed
- Must produce auditable output
- Must be easy for non-developers to run
- Must not rely on audio for baseline classification because many source clips are silent

## Format Support

Initial scope:

- MP4
- MOV
- Any format that can be decoded through `ffmpeg`

Deferred scope:

- native Blackmagic RAW processing

Blackmagic RAW should be treated as a separate ingestion adapter. For the MVP, the practical path is either:

- pre-convert BRAW to an `ffmpeg`-friendly mezzanine format, or
- add optional support later through the Blackmagic RAW SDK

This keeps the first release reliable and much easier to install.

## Proposed Pipeline

### 1. Inventory

Collect:

- filename
- absolute path
- file size
- filesystem created/modified timestamps
- codec/container metadata
- duration
- frame rate
- resolution
- embedded creation time when available
- audio stream presence when detectable

### 2. Sampling

Extract representative frames by either:

- fixed interval sampling, or
- scene detection plus a small number of representative frames per segment

The second option is better for B-roll because it reduces duplicate captions.

### 3. Visual Description

Run a local vision-capable model on sampled frames and return concise descriptions tied to timestamps.

### 4. Aggregation

Combine frame-level descriptions into:

- segment summaries
- clip-level summary
- suggested tags
- confidence or provenance fields where possible

### 5. Storage

Write:

- per-run manifest
- per-video sidecar JSON
- optional SQLite database for search

## Recommended Output Shape

The data model should be segment-first, not only file-first.

Per video:

```json
{
  "id": "sha256-or-stable-id",
  "filename": "drone_beach_001.mp4",
  "path": "/Volumes/Media/Broll/drone_beach_001.mp4",
  "file_created_at": "2026-03-20T11:22:00-04:00",
  "file_modified_at": "2026-03-20T11:22:00-04:00",
  "captured_at": "2026-03-18T18:41:09Z",
  "duration_seconds": 124.3,
  "video": {
    "width": 3840,
    "height": 2160,
    "fps": 23.976,
    "codec": "h264"
  },
  "summary": "Aerial golden-hour beach footage with wide establishing shots and shoreline movement.",
  "suggested_tags": [
    "aerial",
    "drone",
    "beach",
    "sunset",
    "golden hour",
    "establishing shot"
  ],
  "segments": [
    {
      "start_seconds": 0.0,
      "end_seconds": 8.7,
      "frames": [
        {
          "timestamp_seconds": 2.5,
          "description": "Wide aerial view of the shoreline with small waves and long shadows."
        }
      ],
      "summary": "Opening drone pass over the beach.",
      "tags": ["aerial", "shoreline", "wide shot"]
    }
  ]
}
```

Important note:

- `location` should mean filesystem path unless GPS metadata exists.
- inferred real-world location from pixels should not be treated as factual metadata.

## Storage Recommendation

Use both:

- JSON sidecars for portability and transparency
- SQLite for search and filtering

SQLite should be the default searchable index because it stays local, requires no extra service, and is easy to ship in an open source tool.

## Finder Tags

Finder tag export should be optional and conservative.

Why:

- tags are lossy compared with structured metadata
- too many tags become noise
- users may want to review suggested tags before applying them

Best approach:

- keep canonical data in JSON/SQLite
- add a separate command to apply a curated subset of tags to files on macOS

## Non-Goals For V1

- full desktop UI
- native BRAW decode in the base install
- automatic geographic identification
- face recognition / identity resolution
- speech transcription in the first cut
- distributed or cloud processing

## Sensible Technical Direction

Keep the system modular:

- `scanner`: walks folders and collects metadata
- `extractor`: gets frames or scenes
- `captioner`: runs the local vision model
- `classifier`: generates summaries and tags
- `writer`: writes JSON and SQLite
- `tagger`: optional macOS Finder tag export

This allows the inference backend to change without rewriting the whole pipeline.

## Suggested Release Phases

### Phase 1

- MP4/MOV ingestion
- `ffprobe` metadata extraction
- interval or scene-based frame sampling
- local caption generation
- JSON output

### Phase 2

- SQLite index
- CLI search/export commands
- optional Finder tag application

### Phase 3

- BRAW adapter
- audio transcription
- embedding-based semantic search
- lightweight local review UI

## Conclusion

The use case is sound. The main adjustment is scope discipline:

- do not make native BRAW a blocker for the first release
- store segment-level data, not only whole-video summaries
- treat SQLite as the primary search layer
- keep Finder tags as a secondary export step

If the MVP does those things well, the project will already be useful to working video teams.

## Quick Start

Create and activate a virtual environment, then run:

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -e .
argus doctor --model gemma3
argus scan
argus scan --sample-frames --frame-count 4
argus status
argus status --watch
argus caption --model gemma3
argus index
argus search "<query>"
argus serve --open-browser
argus run /Volumes/Shared/Footage --output-dir ~/ArgusOutput
```

Defaults:

- input directory: `./ingest`
- output directory: `./output`

If `ffprobe` is not installed yet, the scanner still runs and writes filesystem metadata. Install FFmpeg later to enable richer media probing:

```bash
brew install ffmpeg
```

Once you drop test MP4s into `ingest`, run the scan command again and inspect:

- `output/manifest.json`
- `output/items/*.json`
- `output/frames/<video-id>/*.jpg`

If `argus doctor` reports missing `ffprobe`, scan results will still include file inventory, but media-specific fields like duration, dimensions, frame rate, and audio presence will remain unavailable until FFmpeg is installed.

If you add `--sample-frames`, Argus will extract evenly spaced JPEGs from each clip and attach their timestamps and paths to each item record. This is the first step toward local visual captioning and tag generation.

To generate captions and initial clip metadata locally, install Ollama and pull a vision-capable model, then run:

```bash
argus doctor --model gemma3
ollama pull gemma3
argus caption --model gemma3
```

That command reads `output/items/*.json`, captions extracted frames, and writes back:

- per-frame captions
- clip summary
- suggested tags

To monitor progress while another terminal is running scan or caption work, use:

```bash
argus status --watch
```

The dashboard shows ingest progress, probe/sample/caption counts, dependency readiness, and recent item status.

To make the library searchable, build the SQLite index after captioning:

```bash
argus index
argus search "<query>"
argus search --limit 25 "<query>"
```

The SQLite database is stored at `output/argus.db` by default.

To browse the library in a local browser UI:

```bash
argus serve --open-browser
```

The server binds to `127.0.0.1:8765` by default and lets you:

- search by content, summary, tags, and visible text
- filter by classification status
- copy file paths
- reveal files directly in Finder

For a complete usage guide, see [docs/USAGE.md](docs/USAGE.md).

## License

MIT. See [LICENSE](LICENSE).

For a no-install dev run, this also works:

```bash
PYTHONPATH=src python3 -m argus scan --pretty
```
