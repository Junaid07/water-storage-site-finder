# Water Storage Site Finder: container for Hugging Face Spaces, Render or any Docker host.
# Copyright (C) 2026 Junaid Ahmad, PhD. Licensed under the GNU AGPL-3.0-or-later.
FROM python:3.11-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PORT=7860 \
    DAMFINDER_CACHE=/tmp/wssf-cache DAMFINDER_PUBLIC=1 DAMFINDER_WORKERS=2
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
# Hugging Face Spaces runs containers as user id 1000
RUN useradd -m -u 1000 user && chown -R user /app
USER user
EXPOSE 7860
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT} --proxy-headers --forwarded-allow-ips='*'"]
