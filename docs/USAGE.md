# Argus Usage Guide

Argus is a local-first tool for indexing video files, extracting representative frames, generating local captions and tags, building a SQLite search index, and browsing the results in a local web UI.

This guide covers the full workflow from installation to browsing search results.

## What Argus Does

Argus processes a folder of supported video files and produces:

- file and media metadata
- representative sampled frames
- local vision-model captions
- clip summaries
- suggested tags
- a searchable SQLite database
- a local browser UI for searching and revealing source files

Argus is designed to work locally. It does not require cloud APIs.

## Requirements

Recommended environment:

- macOS
- Python 3.11 or newer
- FFmpeg installed
- Ollama installed for captioning
- a local vision-capable Ollama model such as `gemma3`

## Installation

From the repository root:

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -e .
```

Install FFmpeg if it is not already available:

```bash
brew install ffmpeg
```

Install Ollama if you want local captioning:

- Download and install from [ollama.com/download](https://ollama.com/download)

Pull the default vision model:

```bash
ollama pull gemma3
```

## Directory Layout

Argus ships with convenient defaults, but they are not required:

- `ingest/`
  Optional default source folder for quick local tests.
- `output/`
  Default location for generated JSON, frames, database, and progress state.

Important:

- you can point Argus at any readable source folder, not just `ingest/`
- source media in repo-local `ingest/` is ignored by git
- generated output in repo-local `output/` is ignored by git
- you can write output to a different local folder with `--output-dir`

## Supported Formats

Current first-pass support is intended for formats readable by `ffmpeg`, especially:

- `mp4`
- `mov`
- `m4v`
- `mpg`
- `mpeg`

Native Blackmagic RAW is not part of the base install yet.

## Preflight Checks

Before running the pipeline, verify local dependencies:

```bash
argus doctor --model gemma3
```

This checks:

- Python runtime
- `ffmpeg`
- `ffprobe`
- Ollama binary
- Ollama API availability
- whether the requested vision model is installed

## Basic Workflow

The typical workflow is:

1. Choose a source folder
2. Scan the folder and optionally sample frames
3. Caption sampled frames locally
4. Build the SQLite index
5. Search from the CLI or local browser UI

Important:

- the source folder does not need to live inside this repository
- any readable path is valid, including mounted external drives and mounted network volumes
- the generated output can be written to a separate local folder if you prefer

Example source folders:

- `/Users/you/Videos/Project-A`
- `/Volumes/ExternalDrive/Footage`
- `/Volumes/SharedMedia/Client-Library`

## 1. Scan Files

You can scan any readable folder path:

```bash
argus scan /Volumes/SharedMedia/Client-Library --output-dir ~/ArgusOutput
```

Basic scan:

```bash
argus scan
```

This writes:

- `output/manifest.json`
- `output/items/*.json`

Scan and sample frames:

```bash
argus scan --sample-frames --frame-count 4
```

Useful flags:

- `--sample-frames`
  Extract representative JPEG frames per clip
- `--frame-count`
  Number of sample frames per video
- `--output-dir`
  Use a non-default output location
- `--pretty`
  Print JSON to stdout

## 2. Monitor Progress

To monitor pipeline state from another terminal:

```bash
argus status --watch
```

This shows:

- ingest totals
- probing status
- frame extraction counts
- caption progress
- model readiness
- recent indexed items
- SQLite index presence

During captioning, Argus also writes a progress file at:

- `output/progress.json`

If you are working outside the repo-local defaults, point `status` at the same source and output directories:

```bash
argus status /Volumes/SharedMedia/Client-Library --output-dir ~/ArgusOutput --watch
```

## 3. Caption Frames Locally

Run local captioning:

```bash
argus caption --model gemma3
```

This reads sampled frames from `output/items/*.json` and writes back:

- per-frame captions
- per-frame tags
- visible on-screen text when detected
- clip summary
- suggested tags

Useful flags:

- `--model`
  Choose a different Ollama model
- `--force`
  Re-run captioning even if captions already exist
- `--pretty`
  Print the summary report as JSON

Example forced re-caption:

```bash
argus caption --model gemma3 --force
```

## 4. Build the SQLite Index

Build or refresh the local database:

```bash
argus index
```

By default, the database is stored at:

- `output/argus.db`

Useful flags:

- `--db-path`
  Write to a custom SQLite file
- `--pretty`
  Print the indexing report as JSON

## 5. Search from the CLI

Search by filename, summary, tags, captions, or visible text:

```bash
argus search "<query>"
```

Examples:

```bash
argus search "outdoor scene"
argus search "close up"
argus search "person at desk"
```

Useful flags:

- `--limit`
  Control how many results are returned
- `--db-path`
  Search a non-default database
- `--pretty`
  Print JSON results

## 6. Use the Browser UI

Start the local web server:

```bash
argus serve --open-browser
```

Defaults:

- host: `127.0.0.1`
- port: `8765`

You can change them:

```bash
argus serve --host 127.0.0.1 --port 9000
```

The browser UI supports:

- text search
- classification-status filtering
- result cards with metadata
- copy path to clipboard
- reveal file in Finder

The UI is local-only by default.

## One-Command Pipeline

If you want a shorter path for non-technical users, you can run the full pipeline in one command:

```bash
argus run /Volumes/SharedMedia/Client-Library --output-dir ~/ArgusOutput
```

This performs:

1. scan
2. frame sampling
3. captioning
4. SQLite indexing

You can then open the browser UI against that output directory:

```bash
argus serve --output-dir ~/ArgusOutput --open-browser
```

## Finder Reveal

From the browser UI, the `Reveal in Finder` action uses the indexed file path and calls macOS Finder reveal locally.

This does not move or rename the source file.

## Output Files

Argus commonly writes:

- `output/manifest.json`
- `output/items/*.json`
- `output/frames/<video-id>/*.jpg`
- `output/argus.db`
- `output/progress.json`

## Recommended Re-run Pattern

If you add more files to your source folder, a common refresh cycle is:

```bash
argus scan /path/to/source/folder --output-dir /path/to/argus-output --sample-frames --frame-count 4
argus caption --output-dir /path/to/argus-output --model gemma3
argus index --output-dir /path/to/argus-output
```

Then browse:

```bash
argus serve --output-dir /path/to/argus-output --open-browser
```

## Troubleshooting

### `argus doctor` says `ffprobe` is missing

Install FFmpeg:

```bash
brew install ffmpeg
```

### `argus caption` says Ollama is unavailable

Make sure:

- Ollama is installed
- the local Ollama app or service is running
- the selected model has been pulled

Then retry:

```bash
argus doctor --model gemma3
```

### Search returns no results

Make sure the index exists and is current:

```bash
argus index
```

Then rerun the search.

### Browser UI opens but shows nothing

Make sure:

- `output/argus.db` exists
- `argus index` has been run
- the UI is pointing at the expected `output` directory

## Development Notes

No-install development run:

```bash
PYTHONPATH=src python3 -m argus status
```

Run tests:

```bash
.venv/bin/python -m unittest discover -s tests
```

## License

Argus is released under the MIT License. See [LICENSE](../LICENSE).
