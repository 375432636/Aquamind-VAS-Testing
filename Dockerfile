# syntax=docker/dockerfile:1
FROM python:3.11-slim-trixie@sha256:9534e5a8e315485d4061ed659af0fd78a284c015f9b73661b41d6bab25604534

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    ORT_DISABLE_TELEMETRY=1 \
    PIPER_DATA_DIR=/opt/piper

RUN apt-get update \
    && apt-get install --no-install-recommends -y ca-certificates ffmpeg libopus0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Only dependency metadata invalidates this layer. Changes to conversations,
# reports or Python source reuse the installed system and Python dependencies.
COPY pyproject.toml /tmp/voice-test-pyproject.toml
RUN python - <<'PY'
import subprocess
import sys
import tomllib

with open('/tmp/voice-test-pyproject.toml', 'rb') as source:
    project = tomllib.load(source)
requirements = [
    *project['build-system']['requires'],
    'wheel',
    *project['project']['dependencies'],
    *project['project']['optional-dependencies']['test'],
]
subprocess.run([sys.executable, '-m', 'pip', 'install', '--no-cache-dir', *requirements], check=True)
PY

# Keep the public voice model in the dependency cache, before changing source
# files. Actions run with the caller's uid/gid, so the cache must be readable.
RUN python -m piper.download_voices zh_CN-huayan-medium --data-dir /opt/piper \
    && chmod -R a+rX /opt/piper

COPY pyproject.toml main.py .coveragerc .coveragerc-client ./
COPY aquamind_voice_report/ ./aquamind_voice_report/
COPY voice_scenarios/ ./voice_scenarios/
COPY tests/ ./tests/
COPY scripts/ ./scripts/
COPY config/ ./config/
COPY fixtures/ ./fixtures/
COPY scenarios/ ./scenarios/
RUN python -m pip install --no-cache-dir --no-deps --no-build-isolation -e .

CMD ["python", "main.py", "--help"]
