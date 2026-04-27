# Argus CLI shortcuts — all targets delegate to ./scripts/argus
# (venv bootstrap and editable install stay in the script)
#
# Common variables (override on the command line):
#   SOURCE=ingest          input folder for scan, run, status
#   OUTPUT_DIR=output     manifest / frames / index output
#   MODEL=…               Ollama vision model (only passed if set)
#   OLLAMA_HOST=…         Ollama API host (only passed if set)
#   QUERY=…              required for `make search`
#   HOST, PORT            optional for `make serve` (default bind stays argus default)
#   ARGS="…"             extra flags for the underlying `argus` subcommand
#
# Examples:
#   make install
#   make help
#   make status
#   make doctor MODEL=gemma3
#   make run SOURCE=/path/to/footage OUTPUT_DIR=~/ArgusOut
#   make search QUERY=boat
#   make cli ARGS="doctor --model gemma3"
#

.DEFAULT_GOAL := help

ARGUS := ./scripts/argus

SOURCE ?= ingest
OUTPUT_DIR ?= output

# Optional; if empty, the CLI default model is used (see argus --help)
MODEL ?=
# Optional; if empty, the CLI default Ollama host is used
OLLAMA_HOST ?=
QUERY ?=
HOST ?=
PORT ?=
# Extra args for the subcommand, e.g. ARGS="--sample-frames" or ARGS="--open-browser"
ARGS ?=

# Expands to --model $(MODEL) only when MODEL is non-empty
arg_model = $(if $(strip $(MODEL)),--model $(MODEL),)
# Expands to --ollama-host $(OLLAMA_HOST) only when set
arg_ollama_host = $(if $(strip $(OLLAMA_HOST)),--ollama-host $(OLLAMA_HOST),)
# For serve: only pass --host / --port when set (otherwise argus uses its own defaults)
arg_host = $(if $(strip $(HOST)),--host $(HOST),)
arg_port = $(if $(strip $(PORT)),--port $(PORT),)

.PHONY: help install cli status doctor scan caption index search serve run

install: ## Create .venv and install Argus in editable mode (no subcommand)
	$(ARGUS) install

help: ## List targets and key variables
	@echo "Argus make shortcuts (invoke $(ARGUS))"
	@echo ""
	@echo "Variables: SOURCE, OUTPUT_DIR, MODEL, OLLAMA_HOST, QUERY, HOST, PORT, ARGS"
	@echo ""
	@grep -hE '^[a-zA-Z0-9_-]+:.*##' "$(firstword $(MAKEFILE_LIST))" | \
		awk 'BEGIN {FS = ":.*## "}; {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

cli: ## Pass through to argus, e.g. make cli ARGS="search boat --limit 5"
	@test -n "$(strip $(ARGS))" || (echo "Set ARGS, e.g. make cli ARGS=\"status\"" >&2; exit 1)
	$(ARGUS) $(ARGS)

status: ## Terminal dashboard: make status [SOURCE=...] [OUTPUT_DIR=...] [ARGS="..."]
	$(ARGUS) status $(SOURCE) --output-dir $(OUTPUT_DIR) $(arg_model) $(arg_ollama_host) $(ARGS)

doctor: ## Dependency check: make doctor [MODEL=...] [OLLAMA_HOST=...] [ARGS="..."]
	$(ARGUS) doctor $(arg_model) $(arg_ollama_host) $(ARGS)

scan: ## Scan: make scan [SOURCE=...] [OUTPUT_DIR=...] [ARGS="--sample-frames" ...]
	$(ARGUS) scan $(SOURCE) --output-dir $(OUTPUT_DIR) $(ARGS)

caption: ## Caption frames: make caption [OUTPUT_DIR=...] [MODEL=...] [ARGS="--force" ...]
	$(ARGUS) caption --output-dir $(OUTPUT_DIR) $(arg_model) $(arg_ollama_host) $(ARGS)

index: ## Rebuild SQLite index: make index [OUTPUT_DIR=...] [ARGS="..."]
	$(ARGUS) index --output-dir $(OUTPUT_DIR) $(ARGS)

search: ## Search: make search QUERY=foo [OUTPUT_DIR=...] [ARGS="..."]
	@test -n "$(strip $(QUERY))" || (echo "Set QUERY, e.g. make search QUERY=boat" >&2; exit 1)
	$(ARGUS) search "$(QUERY)" --output-dir $(OUTPUT_DIR) $(ARGS)

serve: ## Web UI: make serve [OUTPUT_DIR=...] [HOST=...] [PORT=...] [ARGS="--open-browser" ...]
	$(ARGUS) serve --output-dir $(OUTPUT_DIR) $(arg_host) $(arg_port) $(ARGS)

run: ## Full pipeline: make run [SOURCE=...] [OUTPUT_DIR=...] [MODEL=...] [ARGS="..."]
	$(ARGUS) run $(SOURCE) --output-dir $(OUTPUT_DIR) $(arg_model) $(arg_ollama_host) $(ARGS)
