"""Database boundary: local SQLite or hosted PostgreSQL (private schema)."""
import re
import os
import sqlite3

try:
    import psycopg
    from psycopg.rows import dict_row
except ImportError:
    psycopg = None


class IndexedRow(dict):
    def __getitem__(self, key):
        return list(self.values())[key] if isinstance(key, int) else super().__getitem__(key)


class Cursor:
    def __init__(self, cursor, ident=None):
        self.cursor = cursor
        self.lastrowid = ident
        self.rowcount = cursor.rowcount

    def fetchone(self):
        value = self.cursor.fetchone()
        return IndexedRow(value) if value is not None else None

    def fetchall(self):
        return [IndexedRow(value) for value in self.cursor.fetchall()]

    def __iter__(self):
        return iter(self.fetchall())


class Connection:
    def __init__(self, connection, postgres=False):
        self.connection = connection
        self.postgres = postgres

    def __enter__(self):
        return self

    def __exit__(self, *args):
        try:
            return self.connection.__exit__(*args)
        finally:
            self.connection.close()

    def execute(self, sql, params=()):
        if not self.postgres:
            return self.connection.execute(sql, params)
        if sql.strip().upper() == 'BEGIN IMMEDIATE':
            # Serialize critical account/rate-limit/timesheet transitions across processes.
            return Cursor(self.connection.execute('SELECT pg_advisory_xact_lock(946313)'))
        sql = sql.replace('?', '%s')
        returning = bool(re.match(r'INSERT INTO (users|documents)\(', sql, re.I))
        if returning:
            sql += ' RETURNING id'
        cursor = self.connection.execute(sql, params, prepare=False)
        ident = cursor.fetchone()['id'] if returning else None
        return Cursor(cursor, ident)

    def executescript(self, script):
        if self.postgres:
            self.connection.execute(script, prepare=False)
        else:
            self.connection.executescript(script)

    def backup(self, target):
        if self.postgres:
            raise ValueError('Use pg_dump for PostgreSQL backups; SQLite archive tooling is local-only.')
        self.connection.backup(target)


def connect(database_url, path):
    if database_url:
        if psycopg is None:
            raise RuntimeError('Install the PostgreSQL dependency from requirements.txt.')
        options = {'sslmode': 'require'} if os.environ.get('WORKFORCE_ENV') == 'production' else {}
        return Connection(psycopg.connect(database_url, row_factory=dict_row, connect_timeout=10,
                                         options='-c search_path=workforce', prepare_threshold=None, **options), postgres=True)
    connection = sqlite3.connect(path, timeout=15)
    connection.row_factory = sqlite3.Row
    connection.execute('PRAGMA foreign_keys=ON')
    return Connection(connection)


def init_postgres(connection):
    connection.executescript('''
    CREATE SCHEMA IF NOT EXISTS workforce;
    REVOKE ALL ON SCHEMA workforce FROM PUBLIC;
    CREATE TABLE IF NOT EXISTS workforce.users(id SERIAL PRIMARY KEY, name TEXT NOT NULL, email TEXT UNIQUE NOT NULL, role TEXT NOT NULL CHECK(role IN ('employee','manager')), salt TEXT NOT NULL, password TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1, iterations INTEGER NOT NULL DEFAULT 600000);
    CREATE TABLE IF NOT EXISTS workforce.sessions(token TEXT PRIMARY KEY, user_id INTEGER REFERENCES workforce.users(id), csrf TEXT NOT NULL, expires BIGINT NOT NULL);
    CREATE TABLE IF NOT EXISTS workforce.sheets(id SERIAL PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES workforce.users(id), week TEXT NOT NULL, entries TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'draft', note TEXT NOT NULL DEFAULT '', UNIQUE(user_id,week));
    CREATE TABLE IF NOT EXISTS workforce.documents(id SERIAL PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES workforce.users(id), name TEXT NOT NULL, storage TEXT NOT NULL, created TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS workforce.invitations(token_hash TEXT PRIMARY KEY, user_id INTEGER UNIQUE NOT NULL REFERENCES workforce.users(id), expires BIGINT NOT NULL);
    CREATE TABLE IF NOT EXISTS workforce.audit(id SERIAL PRIMARY KEY, actor_id INTEGER REFERENCES workforce.users(id), action TEXT NOT NULL, target TEXT NOT NULL, created BIGINT NOT NULL);
    CREATE TABLE IF NOT EXISTS workforce.rate_limits(key TEXT PRIMARY KEY, count INTEGER NOT NULL, expires BIGINT NOT NULL);
    REVOKE ALL ON ALL TABLES IN SCHEMA workforce FROM PUBLIC;
    ''')
    # Supabase roles don't exist in vanilla PostgreSQL CI; revoke them only when present.
    for role in ('anon', 'authenticated'):
        if connection.execute('SELECT 1 FROM pg_roles WHERE rolname=?', (role,)).fetchone():
            connection.execute('REVOKE ALL ON SCHEMA workforce FROM ' + role)
            connection.execute('REVOKE ALL ON ALL TABLES IN SCHEMA workforce FROM ' + role)
