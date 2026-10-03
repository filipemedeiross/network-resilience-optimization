FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements/web.txt /app/requirements/web.txt
RUN pip install --no-cache-dir -r /app/requirements/web.txt

COPY manage.py /app/manage.py
COPY config /app/config
COPY games /app/games
COPY docker /app/docker
RUN chmod +x /app/docker/web-entrypoint.sh \
    && mkdir -p /app/data /app/staticfiles \
    && useradd --create-home --uid 10001 appuser \
    && chown -R appuser:appuser /app/data /app/staticfiles

USER appuser

EXPOSE 8000

ENTRYPOINT ["/app/docker/web-entrypoint.sh"]
CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "2", "--timeout", "30"]
