.PHONY: setup test check run worker web demo benchmark compose

setup:
	uv sync --extra dev --frozen
	cd web && npm ci && npm run build

test:
	uv run --extra dev pytest

check:
	uv run --extra dev ruff check .
	uv run --extra dev ruff format --check .
	cd web && npm run build

run:
	uv run heatshield serve --host 127.0.0.1 --port 8000

worker:
	uv run heatshield worker

web:
	cd web && npm run dev

demo:
	uv run heatshield demo

benchmark:
	uv run heatshield benchmark --suite robustness --count 100 --output benchmark-results.json

compose:
	docker compose up --build
