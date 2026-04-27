# Agent Guidance

Argus is a local-first Python CLI and browser UI for indexing video folders, generating local visual metadata, building a SQLite search index, and browsing results without cloud APIs. Keep changes scoped to that product shape: auditable local files, simple commands, and low setup friction for non-developers.

## Project Map

- `src/argus/cli.py` defines the `argus` console script and subcommands: `scan`, `doctor`, `caption`, `status`, `index`, `search`, `serve`, and `run`.
- `src/argus/pipeline.py`, `scanner.py`, and `extractor.py` scan source folders, collect metadata, and optionally sample frames.
- `src/argus/captioner.py` reads sampled frame records, calls local Ollama, and writes captions, summaries, titles, tags, and progress state back to JSON.
- `src/argus/database.py` builds and queries the SQLite/FTS search index.
- `src/argus/serve.py` provides the local browser UI with `http.server`; this project does not use Flask, FastAPI, or a frontend build step.
- `tests/test_scanner.py` is the current unittest suite covering scanner, pipeline, captioning, database, CLI, and status behavior.

## Current Workflow

Canonical generated artifacts live under the output directory, defaulting to `output/`:

- `output/items/*.json` are the main pipeline records and should remain inspectable.
- `output/manifest.json` summarizes a scan and is refreshed from item records during captioning.
- `output/frames/<video-id>/*.jpg` stores sampled frames when frame extraction is enabled.
- `output/progress.json` reports long-running caption progress.
- `output/argus.db` is a derived SQLite search index rebuilt from item JSON by `argus index`.

Treat JSON sidecars as the portable source of truth for pipeline metadata. Treat SQLite as a searchable index derived from those sidecars unless the code explicitly changes that contract.

## Development Commands

Use Python 3.11 or newer. From the repository root:

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -e .
```

No-install development runs can use:

```bash
PYTHONPATH=src python3 -m argus status
```

Run the current test suite with:

```bash
.venv/bin/python -m unittest discover -s tests
```

There is no project-standard lint, format, or typecheck command configured yet. Do not introduce one unless the task asks for it or the change requires it.

## Code Conventions

- Ensure to follow PEP 8 Style Guide.
- Use `from __future__ import annotations` in Python modules.
- Prefer `pathlib.Path`, resolved filesystem paths, and explicit UTF-8 when reading or writing text files.
- Keep dependencies minimal; `pyproject.toml` currently declares no runtime Python dependencies.
- Preserve local-first behavior. Do not add cloud services, API keys, or network dependencies for core operation.
- Use mounted filesystem paths such as `/Volumes/...` for network media. Do not design workflows around raw `smb://` URLs.
- Remember that Finder reveal in `serve.py` is macOS-specific because it shells out to `open -R`.

## Testing Expectations

For focused Python changes, add or update `unittest` coverage in `tests/test_scanner.py` or a new `tests/test_*.py` file using the same stdlib style. Prefer temporary directories and mocks over real media, real Ollama calls, or persistent output files.

After changing pipeline data shape, verify scan/caption/index/search contracts together when practical, because each stage reads artifacts from the previous stage.
