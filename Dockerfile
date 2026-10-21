ARG PYTHON_IMAGE=python:3.13-slim-trixie@sha256:cf50a4f6ac4f026458b08d5222023d700ac806410dff5e9ca8728fa44f2b850f
FROM ${PYTHON_IMAGE} AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_DISABLE_PIP_VERSION_CHECK=1 \
    TESSERACT_PATH=tesseract DOCUMENT_BIND_ALL=1 TMPDIR=/tmp
RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr tesseract-ocr-eng fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 10001 docops
WORKDIR /app
COPY requirements.lock .
RUN pip install --no-cache-dir --require-hashes --only-binary=:all: -r requirements.lock
COPY --chown=docops:docops docops ./docops
COPY LICENSE THIRD_PARTY_NOTICES.md /usr/share/doc/northstar/
RUN mkdir /app/data /ipc && chown docops:docops /app/data /ipc
ENV PYTHONPATH=/app

FROM base AS test
COPY requirements-dev.lock .
RUN pip install --no-cache-dir --require-hashes --only-binary=:all: -r requirements-dev.lock
COPY --chown=docops:docops tests ./tests
COPY --chown=docops:docops scripts ./scripts
COPY --chown=docops:docops pyproject.toml .
USER 10001:10001
CMD ["python", "-m", "pytest", "-q", "--basetemp=/tmp/docops-tests", "-o", "cache_dir=/tmp/pytest-cache"]

FROM base AS runtime
RUN python -m pip uninstall -y pip setuptools wheel
USER 10001:10001
EXPOSE 8103
HEALTHCHECK --interval=10s --timeout=3s --retries=5 CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8103/ready',timeout=2)"
CMD ["python", "-m", "docops", "serve", "--no-worker"]
