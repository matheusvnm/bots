export PYTHONPATH := "src"

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

[group('run')]
[doc('Run a bot  (bot + action required; user defaults to 1)')]
run bot action user="1":
    uv run python src/run.py --bot {{bot}} --action {{action}} --user {{user}}

[group('kraken')]
[doc('Run the kraken deposit flow for user 1')]
kraken-deposit user="1":
    just run kraken deposit {{user}}

[group('kraken')]
[doc('Run the kraken withdraw flow for user 1')]
kraken-withdraw user="1":
    just run kraken balance {{user}}

[group('coinbase')]
[doc('Run coinbase-api balance')]
cb-api-balance user="1":
    just run coinbase-api balance {{user}}

[group('coinbase')]
[doc('Run coinbase-api deposit')]
cb-api-deposit user="1":
    just run coinbase-api deposit {{user}}

[group('coinbase')]
[doc('Run coinbase-api withdraw')]
cb-api-withdraw user="1":
    just run coinbase-api withdraw {{user}}

[group('coinbase')]
[doc('Run coinbase-api balance')]
cb-balance user="1":
    just run coinbase balance {{user}}

[group('coinbase')]
[doc('Run coinbase-api deposit')]
cb-deposit user="1":
    just run coinbase deposit {{user}}

[group('coinbase')]
[doc('Run coinbase-api withdraw')]
cb-withdraw user="1":
    just run coinbase withdraw {{user}}
