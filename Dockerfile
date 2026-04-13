# syntax=docker/dockerfile:1.7
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /workspace

COPY requirements.txt /tmp/requirements.txt

RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    --mount=type=cache,target=/root/.cache/pip,sharing=locked \
    --mount=type=cache,target=/root/.npm,sharing=locked \
    apt-get update \
    && apt-get install --no-install-recommends -y ca-certificates git nodejs npm \
    && rm -rf /var/lib/apt/lists/* \
    && python -m pip install --upgrade pip \
    && pip install -r /tmp/requirements.txt \
    && npm install -g @openai/codex@0.118.0

RUN useradd --create-home --shell /usr/sbin/nologin bot \
    && mkdir -p /home/bot/.codex \
    && mkdir -p /run/codex-auth \
    && chown -R bot:bot /home/bot/.codex

USER bot

CMD ["python", "-m", "app.main"]
