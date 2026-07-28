FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
COPY migrations ./migrations
COPY alembic.ini ./
RUN pip install --no-cache-dir .

# data/ (secret key + encrypted session) is a mounted volume, not baked in.
VOLUME ["/app/data"]

RUN useradd --create-home appuser && chown -R appuser /app
USER appuser

CMD ["python", "-m", "teasender.app"]
