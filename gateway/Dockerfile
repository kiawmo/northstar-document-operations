# syntax=docker/dockerfile:1.7
ARG PYTHON_IMAGE=python:3.13-slim-trixie@sha256:cf50a4f6ac4f026458b08d5222023d700ac806410dff5e9ca8728fa44f2b850f
FROM ${PYTHON_IMAGE} AS codex-download
ARG CODEX_VERSION=0.155.1
ARG CODEX_ARCHIVE_SHA256=a0ef8b2debc3bf747e07b1a039354de31300ac0dcc2276498ba281470b5d9115
ADD --checksum=sha256:${CODEX_ARCHIVE_SHA256} https://github.com/openai/codex/releases/download/rust-v${CODEX_VERSION}/codex-x86_64-unknown-linux-musl.tar.gz /tmp/codex.tgz
RUN mkdir /out && tar -xzf /tmp/codex.tgz -C /out && mv /out/codex-x86_64-unknown-linux-musl /out/codex && chmod 0555 /out/codex

FROM ${PYTHON_IMAGE} AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONPATH=/app/src \
    GATEWAY_HOST=0.0.0.0 GATEWAY_PORT=8100 LLM_PROVIDER=codex_cli ALLOW_PAID_LLM=false \
    CODEX_EXECUTABLE=/usr/local/bin/codex CODEX_HOME=/state/codex LLM_TEMP_DIR=/tmp/llm \
    CODEX_AUTH_SOURCE=/run/secrets/codex_auth
WORKDIR /app
COPY requirements.lock /app/requirements.lock
RUN pip install --no-cache-dir --require-hashes -r requirements.lock \
    && useradd --create-home --uid 10001 gateway \
    && mkdir -p /state/codex /tmp/llm \
    && chown -R gateway:gateway /state /tmp/llm
COPY --from=codex-download /out/codex /usr/local/bin/codex
COPY src /app/src
COPY LICENSE /usr/share/doc/northstar-gateway/LICENSE
COPY licenses /usr/share/doc/codex/
USER gateway
EXPOSE 8100
HEALTHCHECK --interval=15s --timeout=5s --start-period=10s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8100/health', timeout=3)"
CMD ["python", "-m", "llm_gateway.container"]

FROM base AS test
USER root
COPY requirements-dev.lock /app/requirements-dev.lock
RUN pip install --no-cache-dir --require-hashes -r requirements-dev.lock
COPY tests /app/tests
USER gateway
CMD ["python", "-m", "pytest", "/app/tests", "-q", "-p", "no:cacheprovider", "--basetemp=/tmp/pytest"]

FROM base AS runtime
USER root
RUN python -m pip uninstall -y pip setuptools wheel
USER gateway
