"""Coherent database + immutable uploads archive; no credentials in filenames/logs."""
import argparse
import datetime
import os
from pathlib import Path
import shutil
import sqlite3
import sys
import tarfile
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app


def backup(destination):
    destination = Path(destination).resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as temp:
        snapshot = Path(temp)
        with app.db() as lock:
            lock.execute('BEGIN IMMEDIATE')
            with app.db() as source, sqlite3.connect(snapshot / 'hub.sqlite3') as target:
                source.backup(target)
            files = [r['storage'] for r in lock.execute('SELECT storage FROM documents')]
        # Committed upload contents never change. Later uploads aren't in the DB snapshot.
        (snapshot / 'uploads').mkdir()
        for storage in files:
            shutil.copy2(app.DATA / 'uploads' / storage, snapshot / 'uploads' / storage)
        with tempfile.NamedTemporaryFile(dir=destination.parent, prefix='.workforce-', suffix='.partial', delete=False) as pending:
            pending_path = Path(pending.name)
        try:
            with tarfile.open(pending_path, 'w:gz') as archive:
                archive.add(snapshot / 'hub.sqlite3', arcname='hub.sqlite3')
                archive.add(snapshot / 'uploads', arcname='uploads')
            # Publishing is atomic and refuses to replace an existing archive.
            os.link(pending_path, destination)
        finally:
            pending_path.unlink(missing_ok=True)
    return destination


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', default='backups/workforce-' + datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '.tar.gz')
    args = parser.parse_args()
    print(backup(args.output))
