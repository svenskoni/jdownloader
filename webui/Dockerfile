FROM python:3.12-alpine

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py jdapi.py jd_init.py ./
COPY static ./static

# Gleiche UID/GID wie USER_ID/GROUP_ID des JDownloader-Containers
USER 1000:1000

EXPOSE 8080

# 1 Worker (gemeinsamer Zustand für Hintergrund-Jobs), Threads für parallele Requests
CMD ["gunicorn", "--bind", "0.0.0.0:8080", "--workers", "1", "--threads", "8", "--timeout", "60", "app:app"]
