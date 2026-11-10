# syntax=docker/dockerfile:1
FROM node:22-bookworm-slim AS frontend
WORKDIR /app/web
COPY web/ ./
RUN --mount=type=secret,id=proxy_ca,required=false \
    if [ -f /run/secrets/proxy_ca ]; then \
      export NODE_EXTRA_CA_CERTS=/run/secrets/proxy_ca; \
    fi; \
    npm ci --strict-ssl=true \
    && npm run build \
    && rm -rf node_modules /root/.npm

FROM ghcr.io/astral-sh/uv:0.13.5 AS uv
FROM python:3.12-slim-bookworm AS backend
WORKDIR /app
ENV UV_PYTHON_DOWNLOADS=never UV_LINK_MODE=copy
COPY pyproject.toml uv.lock README.md alembic.ini ./
COPY src/ ./src/
COPY migrations/ ./migrations/
COPY scripts/ ./scripts/
RUN --mount=from=uv,source=/uv,target=/usr/local/bin/uv \
    --mount=from=frontend,source=/app/web/dist,target=/tmp/frontend \
    --mount=type=secret,id=proxy_ca,required=false \
    --mount=type=secret,id=ca_bundle,required=false \
    if [ -f /run/secrets/ca_bundle ]; then \
      export SSL_CERT_FILE=/run/secrets/ca_bundle PIP_CERT=/run/secrets/ca_bundle; \
    elif [ -f /run/secrets/proxy_ca ]; then \
      export SSL_CERT_FILE=/run/secrets/proxy_ca PIP_CERT=/run/secrets/proxy_ca; \
    fi; \
    uv sync --frozen --no-dev --no-editable --no-cache \
    && mkdir -p web/dist \
    && cp -R /tmp/frontend/. web/dist/

FROM python:3.12-slim-bookworm AS runtime
WORKDIR /app
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    HEATSHIELD_DATABASE_URL=sqlite:////data/heatshield.db
RUN groupadd --gid 10001 heatshield \
    && useradd --uid 10001 --gid heatshield --create-home heatshield \
    && mkdir /data \
    && chown heatshield:heatshield /data
COPY --chown=10001:10001 --from=backend /app /app
USER heatshield
EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=5s --start-period=30s --retries=6 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/readyz', timeout=3)"]
ENTRYPOINT ["/bin/sh", "/app/scripts/container-entrypoint.sh"]
CMD ["api"]
