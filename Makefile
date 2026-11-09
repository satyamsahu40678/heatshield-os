.PHONY: setup test check run demo benchmark

setup:
	uv sync --extra dev --frozen

test:
	uv run --extra dev pytest

check:
	uv run --extra dev ruff check .
	uv run --extra dev ruff format --check .

run:
	uv run uvicorn heatshield.api:app --host 127.0.0.1 --port 8000

demo:
	uv run heatshield demo

benchmark:
	uv run heatshield benchmark --count 3 --output benchmark-results.json
