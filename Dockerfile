# VerumTrade backend (FastAPI + LangGraph agents).
# Usually started through docker-compose.yml together with the frontend image.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONPATH=/app

WORKDIR /app

# Install dependencies first so source edits don't invalidate this layer.
# build-essential covers any dependency without a prebuilt wheel, then is removed.
COPY requirements.txt .
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && pip install -r requirements.txt \
    && apt-get purge -y --auto-remove build-essential \
    && rm -rf /var/lib/apt/lists/*

RUN useradd --create-home --uid 1000 verumtrade \
    && mkdir -p /app/data \
    && chown verumtrade:verumtrade /app /app/data

COPY --chown=verumtrade:verumtrade . .

USER verumtrade

# History DB and run results live on a volume (see docker-compose.yml).
ENV VERUMTRADE_HISTORY_DB=/app/data/trading_history.db \
    VERUMTRADE_RESULTS_DIR=/app/data/results

EXPOSE 8000

HEALTHCHECK --interval=10s --timeout=5s --start-period=40s --retries=6 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4)"

CMD ["python", "-m", "uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
