export PYTHONPATH := "src"

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
[doc('Run a bot  (--bot required, --user defaults to 1)')]
run bot user="1":
    uv run python src/run.py --bot {{bot}} --user {{user}}

[group('run')]
[doc('Run the kraken bot with user 1')]
kraken:
    just run kraken

[group('run')]
[doc('Run the kraken bot with a specific user id')]
kraken-user user:
    just run kraken {{user}}


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


# ── Dev ────────────────────────────────────────────────────────────────────

[group('dev')]
[doc('Run ruff linter')]
lint:
    uv run ruff check src/

[group('dev')]
[doc('Run ruff formatter')]
fmt:
    uv run ruff format src/
