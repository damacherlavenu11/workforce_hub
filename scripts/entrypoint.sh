#!/bin/sh
set -eu
# Render mounts its persistent disk at runtime; give only our application user access.
mkdir -p "${WORKFORCE_DATA:-/var/data}"
chown workforce:workforce "${WORKFORCE_DATA:-/var/data}"
chmod 700 "${WORKFORCE_DATA:-/var/data}"
# Definitions refresh in the background. Scanning fails closed until they are available.
freshclam --daemon --foreground --config-file=/etc/clamav/freshclam.conf &
exec gosu workforce gunicorn --config gunicorn.conf.py wsgi:application
