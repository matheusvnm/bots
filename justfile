export PYTHONPATH := "src"

# ── Dev ────────────────────────────────────────────────────────────────────

[group('dev')]
[doc('List all receipts')]
list:
    just --list

[group('dev')]
[doc('Run ruff linter')]
lint:
    uv run ruff check src/

[group('dev')]
[doc('Run ruff formatter')]
fmt:
    uv run ruff format src/


# ── Setup ──────────────────────────────────────────────────────────────────

[group('setup')]
[doc('Install Python dependencies and Chromium browser')]
setup: deps browser

[group('setup')]
[doc('Install Python dependencies')]
deps:
    uv sync

[group('setup')]
[doc('Install Chromium via patchright')]
browser:
    uv run patchright install chrome


# ── Run ────────────────────────────────────────────────────────────────────

[group('run')]
[doc('Run a bot  (bot + action required; user defaults to 1; pass refresh="--refresh" to force re-scrape)')]
run bot action user="1" refresh="":
    uv run python src/run.py --bot {{bot}} --action {{action}} --user {{user}} {{refresh}}

[group('run')]
[doc('Run the kraken deposit flow for user 1')]
kraken-deposit user="1" refresh="":
    just run kraken deposit {{user}} {{refresh}}

[group('run')]
[doc('Run the kraken withdraw flow for user 1')]
kraken-withdraw user="1" refresh="":
    just run kraken withdraw {{user}} {{refresh}}


[group('run')]
[doc('Run coinbase-api balance')]
cb-balance user="1":
    just run coinbase-api balance {{user}}

[group('run')]
[doc('Run coinbase-api deposit')]
cb-deposit user="1":
    just run coinbase-api deposit {{user}}

[group('run')]
[doc('Run coinbase-api withdraw')]
cb-withdraw user="1":
    just run coinbase-api withdraw {{user}}


# ── Logs ───────────────────────────────────────────────────────────────────

[group('logs')]
[doc('Tail the main bot log')]
logs:
    tail -f logs/bot.log

[group('logs')]
[doc('Tail the network debug log')]
net-logs:
    tail -f logs/network_debug.log

[group('logs')]
[doc('Clear all log files')]
clear-logs:
    rm -f logs/bot.log logs/network_debug.log


