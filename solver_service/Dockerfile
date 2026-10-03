FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apt-get update \
    && apt-get install --yes --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements/solver.txt /tmp/solver-requirements.txt
RUN pip install --requirement /tmp/solver-requirements.txt

COPY solver_service /app/solver_service

RUN useradd --system --uid 10001 --no-create-home solver
USER solver

EXPOSE 8001

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8001/health/ready', timeout=3)"]

CMD ["uvicorn", "solver_service.main:app", "--host", "0.0.0.0", "--port", "8001", "--workers", "1"]
