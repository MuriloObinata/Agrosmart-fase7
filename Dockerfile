FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src \
    AGROSMART_BASE=/app


RUN apt-get update \
    && apt-get install -y --no-install-recommends graphviz \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src ./src

RUN useradd --create-home --uid 1000 agro \
    && mkdir -p data/raw data/trusted data/refined logs relatorios \
    && chown -R agro:agro /app
USER agro

HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=3 \
    CMD python -m agrosmart.healthcheck pipeline

CMD ["python", "-m", "agrosmart.pipeline"]
