FROM python:3.12.11-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
RUN pip install --no-cache-dir uv==0.11.16
COPY pyproject.toml uv.lock README.md ./
COPY packages/agents-should-survive-failure-sdk ./packages/agents-should-survive-failure-sdk
COPY packages/example-operations-agent ./packages/example-operations-agent
COPY src ./src
RUN uv sync --frozen --no-dev
RUN uv build --offline packages/example-operations-agent && \
    uv pip install --python /app/.venv/bin/python packages/example-operations-agent/dist/*.whl
COPY alembic.ini ./
COPY migrations ./migrations
USER 65532:65532
EXPOSE 8000
CMD ["/app/.venv/bin/uvicorn", "agents_should_survive_failure.api:app", "--host", "0.0.0.0", "--port", "8000"]
