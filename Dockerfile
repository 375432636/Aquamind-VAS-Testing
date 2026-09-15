# syntax=docker/dockerfile:1
FROM python:3.11-slim-trixie@sha256:9534e5a8e315485d4061ed659af0fd78a284c015f9b73661b41d6bab25604534

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Trixie supplies eSpeak NG 1.52. Ubuntu 24.04's 1.51 has broken cmn
# pronunciation for these Mandarin text fixtures.
RUN apt-get update \
    && apt-get install --no-install-recommends -y ca-certificates espeak-ng ffmpeg libopus0 \
    && dpkg --compare-versions "$(dpkg-query -W -f='${Version}' espeak-ng)" ge 1.52 \
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

COPY pyproject.toml main.py .coveragerc .coveragerc-client ./
COPY voice_scenarios/ ./voice_scenarios/
COPY tests/ ./tests/
COPY scripts/ ./scripts/
COPY config/ ./config/
COPY fixtures/ ./fixtures/
COPY scenarios/ ./scenarios/
RUN python -m pip install --no-cache-dir --no-deps --no-build-isolation -e .

CMD ["python", "main.py", "--help"]
