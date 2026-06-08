FROM python:3.12-slim

# Non-root user (uid 1000) per project rules.
RUN useradd -u 1000 -m appuser

WORKDIR /app

# Install deps first for layer caching.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY wc2026bot ./wc2026bot

# Data dir for the SQLite file (mounted as a named volume in compose).
RUN mkdir -p /app/data && chown -R appuser:appuser /app

USER appuser

CMD ["python", "-m", "wc2026bot.main"]
