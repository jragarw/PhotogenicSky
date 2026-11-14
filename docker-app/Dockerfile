FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8080 \
    DATA_DIR=/data
WORKDIR /app
ARG APP_VERSION=1.0.0
LABEL org.opencontainers.image.title="Photogenic Sky Planner" \
      org.opencontainers.image.description="Self-hosted photo, birding and tide planning" \
      org.opencontainers.image.version=$APP_VERSION
COPY server.py VERSION /app/
COPY static /app/static
RUN addgroup --system app && adduser --system --ingroup app app \
    && mkdir -p /data && chown app:app /data \
    && chmod 0555 /app
USER app
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/api/health', timeout=2)"
CMD ["python", "/app/server.py"]
