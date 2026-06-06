# Shared base image for all 11 Python agent services.
# The compose file overrides `command` per service to run uvicorn with the
# right module path. One Dockerfile, 11 services.

FROM python:3.12-slim

# uv is faster than pip; use it for installs.
RUN pip install --no-cache-dir uv==0.5.4

WORKDIR /app

# Make /app importable so `uvicorn services.flight_agent.main:app` resolves.
ENV PYTHONPATH=/app

# ─── deps layer (cache-friendly) ─────────────────────────────────────
# Extract third-party deps from pyproject.toml and install ONLY those.
# This layer only re-runs if pyproject.toml changes, not on source edits.
COPY pyproject.toml ./
RUN python -c "import tomllib; print('\n'.join(tomllib.load(open('pyproject.toml','rb'))['project']['dependencies']))" > /tmp/requirements.txt \
    && uv pip install --system --no-cache -r /tmp/requirements.txt

# ─── source layer (changes often) ────────────────────────────────────
# Copy source LAST so iterating on code doesn't bust the dep cache.
COPY services/ ./services/
COPY shared/ ./shared/

# Pre-create secrets mount point so volume mounts don't fail if the host
# file is absent (only calendar-agent needs gcal creds).
RUN mkdir -p /app/secrets

# Each service in docker-compose overrides this with its own uvicorn cmd.
CMD ["python", "-c", "print('Override `command:` in docker-compose.yml to run a service.')"]
