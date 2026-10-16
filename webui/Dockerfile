# Build-Kontext ist das Repo (siehe docker-compose.yml), damit die Erweiterung mit ins Image kommt
FROM python:3.12-alpine

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY webui/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY webui/app.py webui/cnl.py webui/jdapi.py webui/jd_init.py webui/mover.py ./
COPY webui/static ./static
# Click'n'Load-Erweiterung: die WebUI bietet sie als ZIP zum Download an (/extension.zip)
COPY extension ./extension

# Gleiche UID/GID wie USER_ID/GROUP_ID des JDownloader-Containers
USER 1000:1000

# 8080 = Oberfläche (mit Login), 9666 = Click'n'Load-Empfänger (ohne Login, siehe README)
EXPOSE 8080 9666

# 1 Worker (gemeinsamer Zustand für Hintergrund-Jobs), Threads für parallele Requests.
# --no-control-socket: UID 1000 hat kein Home-Verzeichnis, gunicorn könnte $HOME/.gunicorn nicht anlegen.
CMD ["gunicorn", "--bind", "0.0.0.0:8080", "--bind", "0.0.0.0:9666", "--workers", "1", "--threads", "8", "--timeout", "60", "--no-control-socket", "app:app"]
