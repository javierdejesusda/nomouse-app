# NoMouse replay and live mode. Build: docker build -t nomouse .
# Run: see README.md ("Run with Docker"). Secrets come from the environment
# only (META_API_KEY); nothing secret is baked into the image.
FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.11.32 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY pyproject.toml uv.lock LICENSE ./
RUN uv sync --frozen --no-dev --no-install-project
RUN playwright install --with-deps chromium

COPY src ./src
COPY builder ./builder
COPY eval/audit-corrections.json ./eval/
COPY deploy/start.sh ./deploy/
RUN uv sync --frozen --no-dev

RUN useradd --create-home nomouse && mkdir /data && chown nomouse /data
USER nomouse
ENV NOMOUSE_DATA=/data PORT=8000
VOLUME /data
EXPOSE 8000
CMD ["/app/deploy/start.sh"]
