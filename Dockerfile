FROM python:3.12-slim

WORKDIR /app

# Build tools needed for native extensions (cryptography, pydantic-core,
# google-cloud-firestore grpc deps).
RUN apt-get update && \
    apt-get install -y --no-install-recommends gcc python3-dev && \
    rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY src/ src/

# Install with team-mode optional deps (firestore, kms, monitoring, jinja2,
# starlette). The base install is enough for stdio/local mode.
RUN pip install --no-cache-dir ".[team]"

RUN apt-get purge -y --auto-remove gcc python3-dev

RUN groupadd -r mcp && useradd -r -g mcp -d /app -s /sbin/nologin mcp && \
    chown -R mcp:mcp /app

USER mcp

ENV PLAUD_TRANSPORT=http
ENV PLAUD_MCP_PORT=8000
ENV PLAUD_MCP_HOST=0.0.0.0
# Team mode is the Cloud Run default; the rs_infra module sets the same
# value via Cloud Run env. Local Docker users can override with -e
# PLAUD_DEPLOYMENT_MODE='' to fall back to single-tenant API-key mode.
ENV PLAUD_DEPLOYMENT_MODE=team

EXPOSE 8000

CMD ["python", "-m", "plaud_notes_mcp.server"]
