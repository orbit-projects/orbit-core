# Contributing to Orbit Core

Start with the architecture overview and relevant ADRs. Core changes must preserve the documented
boundary: Core defines stable contracts; adapter packages define generic capability contracts; provider
plugins implement adapters.

Use Python 3.11 or newer, create a virtual environment, and install `.[dev]`. Before opening a pull
request, run `ruff check src tests scripts`, `ruff format --check src tests scripts`, `mypy src/orbit`,
`pytest`, and `python scripts/check-license-headers.py`.

Every public API needs type annotations and docstrings. Every Python file needs Orbit's Spring-style
Apache-2.0 copyright and license header. Add tests for behavior and update documentation when a public
contract or architecture changes.
