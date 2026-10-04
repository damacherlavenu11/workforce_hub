FROM python:3.12-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends clamav clamav-freshclam gosu ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 10001 workforce \
    && mkdir -p /var/data /var/lib/clamav \
    && chown workforce:workforce /var/data \
    && chown clamav:clamav /var/lib/clamav \
    && printf 'DatabaseOwner clamav\nDatabaseDirectory /var/lib/clamav\nDatabaseMirror database.clamav.net\nChecks 12\nForeground true\n' > /etc/clamav/freshclam.conf
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app.py wsgi.py gunicorn.conf.py ./
COPY static ./static
COPY scripts ./scripts
ENTRYPOINT ["sh", "/app/scripts/entrypoint.sh"]
