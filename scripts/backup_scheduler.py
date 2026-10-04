"""One-instance daily archives, retained seven days, on the persistent disk.
Render's disk snapshots protect archives; export archives separately for disaster recovery.
"""
import logging
import threading
import time
from scripts.backup import backup
import app


def backup_if_due():
    directory = app.DATA / 'backups'
    directory.mkdir(exist_ok=True)
    archives = list(directory.glob('workforce-*.tar.gz'))
    now = time.time()
    if not archives or now - max(p.stat().st_mtime for p in archives) >= 86400:
        backup(directory / ('workforce-' + str(int(now)) + '.tar.gz'))
        for path in archives:
            if now - path.stat().st_mtime > 7 * 86400:
                path.unlink()


def start():
    def loop():
        while True:
            try:
                backup_if_due()
            except Exception:
                logging.exception('Scheduled backup failed; operator action required')
            time.sleep(3600)
    threading.Thread(target=loop, name='daily-backup', daemon=True).start()
