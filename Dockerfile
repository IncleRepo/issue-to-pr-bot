FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /workspace

COPY requirements.txt /tmp/requirements.txt

RUN python -m pip install --upgrade pip \
    && pip install --no-cache-dir -r /tmp/requirements.txt

RUN useradd --create-home --shell /usr/sbin/nologin bot

USER bot

CMD ["python", "-m", "app.main"]
