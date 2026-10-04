"""Workforce Hub: single-company Flask application."""
import argparse
import datetime as dt
import getpass
import hashlib
import hmac
import json
import logging
import math
import os
from pathlib import Path
import re
import secrets
import shlex
import sqlite3
import subprocess
import time
import threading
import zipfile
import io
from functools import wraps
from urllib.parse import unquote, urlsplit

from flask import Flask, g, jsonify, request, send_file, send_from_directory
from werkzeug.exceptions import HTTPException
from werkzeug.middleware.proxy_fix import ProxyFix

ROOT = Path(__file__).parent
DATA = Path(os.environ.get('WORKFORCE_DATA', ROOT / 'data'))
MAX_UPLOAD = 10 * 1024 * 1024
SESSION_SECONDS = 8 * 3600
SCAN_LOCK = threading.BoundedSemaphore(1)


def db():
    connection = sqlite3.connect(DATA / 'hub.sqlite3', timeout=15)
    connection.row_factory = sqlite3.Row
    connection.execute('PRAGMA foreign_keys=ON')
    return connection


def password_hash(password, salt):
    return hashlib.pbkdf2_hmac('sha256', password.encode(), bytes.fromhex(salt), 600000).hex()


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def init():
    DATA.mkdir(exist_ok=True, parents=True)
    (DATA / 'uploads').mkdir(exist_ok=True)
    with db() as c:
        c.execute('PRAGMA journal_mode=WAL')
        c.executescript('''
        CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, name TEXT NOT NULL, email TEXT UNIQUE NOT NULL, role TEXT NOT NULL CHECK(role IN ('employee','manager')), salt TEXT NOT NULL, password TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS sessions(token TEXT PRIMARY KEY, user_id INTEGER REFERENCES users(id), csrf TEXT NOT NULL, expires INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS sheets(id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id), week TEXT NOT NULL, entries TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'draft', note TEXT NOT NULL DEFAULT '', UNIQUE(user_id,week));
        CREATE TABLE IF NOT EXISTS documents(id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id), name TEXT NOT NULL, storage TEXT NOT NULL, created TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS invitations(token_hash TEXT PRIMARY KEY, user_id INTEGER UNIQUE NOT NULL REFERENCES users(id), expires INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY, actor_id INTEGER REFERENCES users(id), action TEXT NOT NULL, target TEXT NOT NULL, created INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS rate_limits(key TEXT PRIMARY KEY, count INTEGER NOT NULL, expires INTEGER NOT NULL);
        ''')
        columns = {r['name'] for r in c.execute('PRAGMA table_info(users)')}
        if 'active' not in columns:
            c.execute('ALTER TABLE users ADD COLUMN active INTEGER NOT NULL DEFAULT 1')
        if 'iterations' not in columns:
            c.execute('ALTER TABLE users ADD COLUMN iterations INTEGER NOT NULL DEFAULT 200000')


def validate_identity(name, email):
    if not isinstance(name, str) or not isinstance(email, str):
        raise ValueError('Enter a name and valid email address.')
    name, email = name.strip(), email.strip().lower()
    if not name or len(name) > 100 or len(email) > 254 or not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', email):
        raise ValueError('Enter a name and valid email address.')
    return name, email


def validate_password(password):
    if not isinstance(password, str) or not 12 <= len(password) <= 256:
        raise ValueError('Choose a password between 12 and 256 characters.')


def create_user(name, email, role, password):
    name, email = validate_identity(name, email)
    role = role.strip().lower()
    if role not in ('employee', 'manager'):
        raise ValueError('Choose employee or manager.')
    validate_password(password)
    salt = secrets.token_hex(16)
    with db() as c:
        ident = c.execute('INSERT INTO users(name,email,role,salt,password,iterations) VALUES(?,?,?,?,?,600000)',
                         (name, email, role, salt, password_hash(password, salt))).lastrowid
        audit(c, ident, 'account.created', str(ident))


def audit(c, actor, action, target):
    c.execute('INSERT INTO audit(actor_id,action,target,created) VALUES(?,?,?,?)',
              (actor, action, str(target), int(time.time())))


def create_app(config=None):
    server = Flask(__name__, static_folder=None)
    production = os.environ.get('WORKFORCE_ENV') == 'production'
    origin = os.environ.get('PUBLIC_ORIGIN', '').rstrip('/')
    server.config.update(MAX_CONTENT_LENGTH=MAX_UPLOAD, PRODUCTION=production, PUBLIC_ORIGIN=origin,
                         DOCUMENT_SCANNER=os.environ.get('DOCUMENT_SCANNER', ''),
                         ALLOW_UNSCANNED_UPLOADS=not production,
                         SESSION_COOKIE_SECURE=production)
    if config:
        server.config.update(config)
    if server.config['PRODUCTION']:
        server.config['SESSION_COOKIE_SECURE'] = True
        server.config['ALLOW_UNSCANNED_UPLOADS'] = False
        parsed = urlsplit(server.config['PUBLIC_ORIGIN'])
        if parsed.scheme != 'https' or not parsed.netloc or parsed.path or parsed.query or parsed.fragment or parsed.username:
            raise RuntimeError('Production requires PUBLIC_ORIGIN=https://your-domain without a path.')
        if not os.environ.get('WORKFORCE_DATA') and not server.config.get('TESTING'):
            raise RuntimeError('Production requires WORKFORCE_DATA pointing to persistent storage.')
    # Set only behind a trusted reverse proxy, with direct backend access blocked.
    if os.environ.get('TRUST_PROXY') == '1':
        server.wsgi_app = ProxyFix(server.wsgi_app, x_for=1, x_proto=1)
    init()

    def fail(message, status=400):
        return jsonify(error=message), status

    def payload():
        value = request.get_json(silent=True)
        if not isinstance(value, dict):
            raise ValueError('Send a valid JSON object.')
        return value

    def limited(key, limit, seconds=900):
        now = int(time.time())
        # The database makes limits consistent across Gunicorn workers/restarts.
        with db() as c:
            c.execute('BEGIN IMMEDIATE')
            c.execute('DELETE FROM rate_limits WHERE expires<=?', (now,))
            row = c.execute('SELECT count FROM rate_limits WHERE key=?', (key,)).fetchone()
            if row and row['count'] >= limit:
                return True
            c.execute('INSERT INTO rate_limits VALUES(?,1,?) ON CONFLICT(key) DO UPDATE SET count=count+1', (key, now + seconds))
        return False

    def protect(role=None):
        def decorate(fn):
            @wraps(fn)
            def wrapped(*args, **kwargs):
                if not g.user:
                    return fail('Please sign in.', 401)
                if role and g.user['role'] != role:
                    return fail('Manager access required.' if role == 'manager' else 'Employee access required.', 403)
                if request.method != 'GET' and not hmac.compare_digest(request.headers.get('X-CSRF-Token', ''), g.user['csrf']):
                    return fail('Session check failed. Reload and try again.', 403)
                return fn(*args, **kwargs)
            return wrapped
        return decorate

    @server.before_request
    def security():
        g.user = None
        if server.config['PRODUCTION'] and request.path != '/healthz':
            if not request.is_secure:
                return fail('HTTPS is required.', 400)
            if request.host != urlsplit(server.config['PUBLIC_ORIGIN']).netloc:
                return fail('Invalid host.', 400)
        if request.method not in ('GET', 'HEAD', 'OPTIONS'):
            origin = request.headers.get('Origin')
            expected = server.config['PUBLIC_ORIGIN'] or request.host_url.rstrip('/')
            if origin and origin != expected:
                return fail('Request origin is not allowed.', 403)
        token = request.cookies.get('session', '')
        if token and len(token) <= 128:
            with db() as c:
                g.user = c.execute('SELECT users.id,name,email,role,csrf,token FROM sessions JOIN users ON users.id=sessions.user_id WHERE token=? AND expires>? AND active=1',
                                   (digest(token), int(time.time()))).fetchone()

    @server.after_request
    def headers(response):
        response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'no-referrer'
        response.headers['X-Frame-Options'] = 'DENY'
        response.headers['Permissions-Policy'] = 'camera=(), microphone=(), geolocation=()'
        response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; object-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        if server.config['PRODUCTION']:
            response.headers['Strict-Transport-Security'] = 'max-age=31536000'
        return response

    @server.errorhandler(ValueError)
    def invalid(error):
        return fail(str(error))

    @server.errorhandler(sqlite3.IntegrityError)
    def conflict(error):
        return fail('This record already exists.', 409)

    @server.errorhandler(HTTPException)
    def http_error(error):
        messages = {404: 'Not found.', 413: 'Choose a file under 10 MB.', 400: 'Invalid request.'}
        return fail(messages.get(error.code, error.name), error.code)

    @server.errorhandler(Exception)
    def unexpected(error):
        server.logger.exception('Request failed: %s', request.path)
        return fail('Something went wrong. Please try again.', 500)

    @server.get('/healthz')
    def health():
        with db() as c:
            c.execute('SELECT 1').fetchone()
        return jsonify(status='ok')

    @server.get('/')
    def index():
        return send_from_directory(ROOT / 'static', 'index.html')

    @server.get('/<name>')
    def asset(name):
        if name not in ('app.js', 'style.css', 'favicon.svg'):
            return fail('Not found.', 404)
        return send_from_directory(ROOT / 'static', name)

    @server.get('/api/me')
    def me():
        return jsonify({key: g.user[key] for key in ('id', 'name', 'email', 'role', 'csrf')} if g.user else None)

    @server.post('/api/login')
    def login():
        data = payload()
        email, password = data.get('email'), data.get('password')
        if not isinstance(email, str) or not isinstance(password, str) or len(email) > 254 or len(password) > 256:
            return fail('Enter your email and password.')
        email = email.strip().lower()
        ip_key = digest(request.remote_addr or 'unknown')
        if limited('login-ip:' + ip_key, 40) or limited('login-email:' + digest(email), 10):
            return fail('Too many sign-in attempts. Try again in 15 minutes.', 429)
        with db() as c:
            account = c.execute('SELECT * FROM users WHERE email=?', (email,)).fetchone()
            salt = account['salt'] if account else '00' * 16
            iterations = account['iterations'] if account else 600000
            hashed = hashlib.pbkdf2_hmac('sha256', password.encode(), bytes.fromhex(salt), iterations).hex()
            valid = hmac.compare_digest(hashed, account['password'] if account else '00' * 32)
            if not account or not account['active'] or not valid:
                audit(c, None, 'login.failed', digest(email))
                return fail('Unable to sign in. Check your email and password. If you are new, activate your employer invitation first.', 401)
            if iterations != 600000:
                salt = secrets.token_hex(16)
                c.execute('UPDATE users SET salt=?,password=?,iterations=600000 WHERE id=?', (salt, password_hash(password, salt), account['id']))
            token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
            c.execute('DELETE FROM sessions WHERE expires<=?', (int(time.time()),))
            if request.cookies.get('session'):
                c.execute('DELETE FROM sessions WHERE token=?', (digest(request.cookies['session']),))
            c.execute('INSERT INTO sessions VALUES(?,?,?,?)', (digest(token), account['id'], csrf, int(time.time()) + SESSION_SECONDS))
            audit(c, account['id'], 'login.succeeded', account['id'])
        response = jsonify(ok=True)
        response.set_cookie('session', token, max_age=SESSION_SECONDS, httponly=True, secure=server.config['SESSION_COOKIE_SECURE'], samesite='Strict', path='/')
        return response

    @server.post('/api/logout')
    @protect()
    def logout():
        with db() as c:
            c.execute('DELETE FROM sessions WHERE token=?', (g.user['token'],))
            audit(c, g.user['id'], 'logout', g.user['id'])
        response = jsonify(ok=True)
        response.delete_cookie('session', secure=server.config['SESSION_COOKIE_SECURE'], httponly=True, samesite='Strict')
        return response

    @server.post('/api/password')
    @protect()
    def change_password():
        if limited('password:' + str(g.user['id']), 5):
            return fail('Too many attempts. Try again in 15 minutes.', 429)
        data = payload()
        current, password = data.get('current'), data.get('password')
        validate_password(password)
        if not isinstance(current, str) or len(current) > 256:
            return fail('Enter your current password.')
        with db() as c:
            account = c.execute('SELECT * FROM users WHERE id=?', (g.user['id'],)).fetchone()
            hashed = hashlib.pbkdf2_hmac('sha256', current.encode(), bytes.fromhex(account['salt']), account['iterations']).hex()
            if not hmac.compare_digest(hashed, account['password']):
                return fail('Your current password is incorrect.', 401)
            salt = secrets.token_hex(16)
            c.execute('UPDATE users SET salt=?,password=?,iterations=600000 WHERE id=?', (salt, password_hash(password, salt), g.user['id']))
            c.execute('DELETE FROM sessions WHERE user_id=?', (g.user['id'],))
            audit(c, g.user['id'], 'password.changed', g.user['id'])
        response = jsonify(ok=True)
        response.delete_cookie('session', secure=server.config['SESSION_COOKIE_SECURE'], httponly=True, samesite='Strict')
        return response

    @server.post('/api/employees/disable')
    @protect('manager')
    def disable_employee():
        ident = payload().get('id')
        if type(ident) is not int:
            return fail('Choose an employee.')
        with db() as c:
            c.execute('BEGIN IMMEDIATE')
            changed = c.execute("UPDATE users SET active=0 WHERE id=? AND role='employee'", (ident,))
            if not changed.rowcount:
                return fail('Employee not found.', 404)
            c.execute('DELETE FROM sessions WHERE user_id=?', (ident,))
            c.execute('DELETE FROM invitations WHERE user_id=?', (ident,))
            audit(c, g.user['id'], 'employee.disabled', ident)
        return jsonify(ok=True)

    @server.get('/api/employees')
    @protect('manager')
    def employees():
        with db() as c:
            return jsonify([dict(r) for r in c.execute("SELECT users.id,name,email,active,expires FROM users LEFT JOIN invitations ON invitations.user_id=users.id WHERE role='employee' ORDER BY name")])

    @server.post('/api/invite')
    @protect('manager')
    def invite():
        data = payload()
        name, email = validate_identity(data.get('name'), data.get('email'))
        token = secrets.token_urlsafe(32)
        with db() as c:
            c.execute('BEGIN IMMEDIATE')
            existing = c.execute('SELECT id,active FROM users WHERE email=?', (email,)).fetchone()
            if existing and existing['active']:
                return fail('This email already has an active account.', 409)
            if existing:
                ident = existing['id']
                c.execute('UPDATE users SET name=? WHERE id=?', (name, ident))
                c.execute('DELETE FROM invitations WHERE user_id=?', (ident,))
            else:
                salt = secrets.token_hex(16)
                ident = c.execute("INSERT INTO users(name,email,role,salt,password,active,iterations) VALUES(?,?,'employee',?,?,0,600000)",
                                  (name, email, salt, password_hash(secrets.token_urlsafe(48), salt))).lastrowid
            c.execute('INSERT INTO invitations VALUES(?,?,?)', (digest(token), ident, int(time.time()) + 172800))
            audit(c, g.user['id'], 'invitation.created', ident)
        return jsonify(token=token, email=email, expiresInHours=48), 201

    @server.post('/api/activate')
    def activate():
        if limited('activate:' + digest(request.remote_addr or 'unknown'), 20):
            return fail('Too many attempts. Try again in 15 minutes.', 429)
        data = payload()
        password, token = data.get('password'), data.get('token')
        validate_password(password)
        if not isinstance(token, str) or len(token) > 128:
            return fail('Invalid invitation.')
        salt = secrets.token_hex(16)
        hashed = password_hash(password, salt)
        with db() as c:
            c.execute('BEGIN IMMEDIATE')
            row = c.execute('SELECT invitations.user_id FROM invitations JOIN users ON users.id=invitations.user_id WHERE token_hash=? AND expires>? AND active=0', (digest(token), int(time.time()))).fetchone()
            if not row:
                return fail('Invitation is invalid, expired or already used. Ask your employer for a new invitation.')
            c.execute('UPDATE users SET salt=?,password=?,active=1,iterations=600000 WHERE id=?', (salt, hashed, row['user_id']))
            c.execute('DELETE FROM invitations WHERE user_id=?', (row['user_id'],))
            audit(c, row['user_id'], 'account.activated', row['user_id'])
        return jsonify(ok=True)

    @server.get('/api/data')
    @protect()
    def data():
        manager = g.user['role'] == 'manager'
        scope, args = ('', ()) if manager else (' WHERE user_id=?', (g.user['id'],))
        with db() as c:
            sheets = [dict(r) for r in c.execute('SELECT sheets.*,users.name FROM sheets JOIN users ON users.id=sheets.user_id' + scope + ' ORDER BY week DESC', args)]
            documents = [dict(r) for r in c.execute('SELECT id,user_id,name,created FROM documents' + scope + ' ORDER BY id DESC', args)]
            people = [dict(r) for r in c.execute('SELECT id,name FROM users' + ('' if manager else ' WHERE id=?'), () if manager else (g.user['id'],))]
        for sheet in sheets:
            sheet['entries'] = json.loads(sheet['entries'])
        return jsonify(sheets=sheets, documents=documents, people=people)

    @server.post('/api/sheets')
    @protect('employee')
    def sheets():
        data = payload()
        try:
            week = dt.date.fromisoformat(data.get('week', ''))
        except (ValueError, TypeError):
            return fail('Choose a valid week starting on Monday.')
        if week.weekday() != 0:
            return fail('Choose a Monday as the week start.')
        entries = data.get('entries')
        if not isinstance(entries, list) or len(entries) != 7 or any(type(x) not in (int, float) or not math.isfinite(x) or not 0 <= x <= 24 for x in entries):
            return fail('Enter seven daily hours between 0 and 24.')
        if type(data.get('submit', False)) is not bool:
            return fail('Invalid submission choice.')
        status = 'submitted' if data.get('submit') else 'draft'
        with db() as c:
            c.execute('BEGIN IMMEDIATE')
            old = c.execute('SELECT status FROM sheets WHERE user_id=? AND week=?', (g.user['id'], week.isoformat())).fetchone()
            if old and old['status'] in ('submitted', 'approved'):
                return fail('Submitted or approved timesheets cannot be edited.', 409)
            c.execute("INSERT INTO sheets(user_id,week,entries,status) VALUES(?,?,?,?) ON CONFLICT(user_id,week) DO UPDATE SET entries=excluded.entries,status=excluded.status,note=''",
                      (g.user['id'], week.isoformat(), json.dumps(entries), status))
            audit(c, g.user['id'], 'timesheet.' + status, week.isoformat())
        return jsonify(ok=True)

    @server.post('/api/review')
    @protect('manager')
    def review():
        data = payload()
        status, note, ident = data.get('status'), data.get('note', ''), data.get('id')
        if status not in ('approved', 'returned') or not isinstance(note, str) or len(note) > 1000 or type(ident) is not int or (status == 'returned' and not note.strip()):
            return fail('Choose an approval or provide a correction note.')
        with db() as c:
            result = c.execute("UPDATE sheets SET status=?,note=? WHERE id=? AND status='submitted'", (status, note, ident))
            if not result.rowcount:
                return fail('This timesheet is no longer pending.', 409)
            audit(c, g.user['id'], 'timesheet.' + status, ident)
        return jsonify(ok=True)

    @server.post('/api/upload')
    @protect()
    def upload():
        name = unquote(request.headers.get('X-Filename', 'document'))
        name = name.replace('\\', '/').split('/')[-1]
        extension = Path(name).suffix.lower()
        if not name or len(name) > 200 or any(ord(ch) < 32 for ch in name) or extension not in ('.pdf', '.png', '.jpg', '.jpeg', '.docx', '.xlsx', '.csv', '.txt'):
            return fail('Supported files: PDF, images, Office documents, CSV and text.')
        if limited('upload:' + str(g.user['id']), 30, 3600):
            return fail('Too many uploads. Try again in an hour.', 429)
        raw = request.get_data()
        if not raw:
            return fail('Choose a nonempty file.')
        signatures = {'.pdf': b'%PDF-', '.png': b'\x89PNG\r\n\x1a\n', '.jpg': b'\xff\xd8\xff', '.jpeg': b'\xff\xd8\xff'}
        if extension in signatures and not raw.startswith(signatures[extension]):
            return fail('The file content does not match its extension.')
        if extension in ('.txt', '.csv'):
            try:
                raw.decode('utf-8')
            except UnicodeDecodeError:
                return fail('Text and CSV files must use UTF-8 encoding.')
            if b'\x00' in raw:
                return fail('Invalid text file.')
        if extension in ('.docx', '.xlsx'):
            try:
                with zipfile.ZipFile(io.BytesIO(raw)) as archive:
                    names = archive.namelist()
                    prefix = 'word/' if extension == '.docx' else 'xl/'
                    if '[Content_Types].xml' not in names or not any(n.startswith(prefix) for n in names) or any('vbaproject' in n.lower() for n in names):
                        return fail('Invalid or macro-enabled Office document.')
            except zipfile.BadZipFile:
                return fail('Invalid Office document.')
        scanner = server.config['DOCUMENT_SCANNER']
        if not scanner and not server.config['ALLOW_UNSCANNED_UPLOADS']:
            return fail('Document uploads are unavailable until security scanning is configured.', 503)
        storage = secrets.token_hex(24)
        target = DATA / 'uploads' / storage
        target.write_bytes(raw)
        try:
            if scanner:
                if not SCAN_LOCK.acquire(blocking=False):
                    return fail('Document scanning is busy. Try again shortly.', 503)
                try:
                    try:
                        scan = subprocess.run(shlex.split(scanner) + [str(target)], capture_output=True, timeout=60, check=False)
                    except (OSError, subprocess.TimeoutExpired):
                        return fail('Document scanning is unavailable. Try again later.', 503)
                    if scan.returncode != 0:
                        return fail('The document did not pass security scanning.', 400 if scan.returncode == 1 else 503)
                finally:
                    SCAN_LOCK.release()
            with db() as c:
                ident = c.execute('INSERT INTO documents(user_id,name,storage,created) VALUES(?,?,?,?)', (g.user['id'], name, storage, dt.date.today().isoformat())).lastrowid
                audit(c, g.user['id'], 'document.uploaded', ident)
            storage = None
            return jsonify(ok=True), 201
        finally:
            if storage:
                target.unlink(missing_ok=True)

    @server.get('/api/documents/<int:ident>')
    @protect()
    def download(ident):
        with db() as c:
            document = c.execute('SELECT * FROM documents WHERE id=?', (ident,)).fetchone()
            if not document or (g.user['role'] != 'manager' and document['user_id'] != g.user['id']):
                return fail('Document not found.', 404)
            audit(c, g.user['id'], 'document.downloaded', ident)
        target = DATA / 'uploads' / document['storage']
        if not target.is_file():
            return fail('Document is unavailable. Contact your employer.', 404)
        return send_file(target, mimetype='application/octet-stream', as_attachment=True, download_name=document['name'])

    return server


def reset_password(email, password):
    validate_password(password)
    salt = secrets.token_hex(16)
    with db() as c:
        user = c.execute('SELECT id FROM users WHERE email=? AND active=1', (email.strip().lower(),)).fetchone()
        if not user:
            raise ValueError('Active account not found.')
        c.execute('UPDATE users SET salt=?,password=?,iterations=600000 WHERE id=?', (salt, password_hash(password, salt), user['id']))
        c.execute('DELETE FROM sessions WHERE user_id=?', (user['id'],))
        audit(c, user['id'], 'password.reset_by_operator', user['id'])


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--create-user', action='store_true')
    parser.add_argument('--reset-password', action='store_true')
    parser.add_argument('--port', type=int, default=8000)
    args = parser.parse_args()
    init()
    try:
        if args.create_user:
            create_user(input('Name: '), input('Email: '), input('Role (employee/manager): '), getpass.getpass('Password (12+ characters): '))
            print('Account created.')
        elif args.reset_password:
            reset_password(input('Email: '), getpass.getpass('New password (12+ characters): '))
            print('Password changed; all account sessions revoked.')
        elif os.environ.get('WORKFORCE_ENV') == 'production':
            parser.error('Use gunicorn --config gunicorn.conf.py wsgi:application in production.')
        else:
            create_app().run(host='127.0.0.1', port=args.port, debug=False)
    except (ValueError, sqlite3.IntegrityError) as error:
        parser.exit(1, str(error) + '\n')
