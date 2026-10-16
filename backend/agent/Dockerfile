# AgentCore Runtime container (linux/arm64, exposes /invocations + /ping on :8080).
# Build context = repository root (build.sh uses -f backend/agent/Dockerfile .) so the shared query layer can be copied.
FROM --platform=linux/arm64 public.ecr.aws/docker/library/python:3.12-slim

WORKDIR /app

# System dependencies (none needed for strands/pyarrow; keep it lean)
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

COPY backend/agent/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# Application code + shared query layer (flattened into /app to match the flat imports)
COPY backend/agent/app.py backend/agent/agent.py backend/agent/tools.py /app/
COPY backend/shared/athena_client.py backend/shared/derivations.py /app/

# Port expected by AgentCore Runtime
EXPOSE 8080

# Run as a non-root user (the app reads /app only, and at runtime writes only /tmp and $HOME)
RUN useradd --create-home --uid 10001 app && chown -R app /app
USER app
# /ping is the health-check endpoint expected by AgentCore Runtime (built into BedrockAgentCoreApp)
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/ping', timeout=3).status==200 else 1)"
CMD ["python", "app.py"]
