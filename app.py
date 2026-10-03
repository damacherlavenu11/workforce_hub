"""Workforce Hub: dependency-free local MVP. Run python3 app.py."""
import os, json, sqlite3, hashlib, secrets, hmac, datetime, re
from pathlib import Path
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from http.cookies import SimpleCookie

ROOT = Path(__file__).parent
DATA = Path(os.environ.get('WORKFORCE_DATA', ROOT / 'data'))
MAX_UPLOAD = 10 * 1024 * 1024

def db():
    conn = sqlite3.connect(DATA / 'hub.sqlite3')
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys=ON')
    return conn

def password_hash(password, salt):
    return hashlib.pbkdf2_hmac('sha256', password.encode(), bytes.fromhex(salt), 200000).hex()

def init():
    DATA.mkdir(exist_ok=True, parents=True)
    (DATA / 'uploads').mkdir(exist_ok=True)
    with db() as c:
        c.executescript('''
        CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, name TEXT NOT NULL, email TEXT UNIQUE NOT NULL, role TEXT NOT NULL CHECK(role IN ('employee','manager')), salt TEXT NOT NULL, password TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS sessions(token TEXT PRIMARY KEY, user_id INTEGER REFERENCES users(id), csrf TEXT NOT NULL, expires INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS sheets(id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id), week TEXT NOT NULL, entries TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'draft', note TEXT NOT NULL DEFAULT '', UNIQUE(user_id,week));
        CREATE TABLE IF NOT EXISTS documents(id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id), name TEXT NOT NULL, storage TEXT NOT NULL, created TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS invitations(token_hash TEXT PRIMARY KEY, user_id INTEGER UNIQUE NOT NULL REFERENCES users(id), expires INTEGER NOT NULL);
        ''')
        if 'active' not in [r['name'] for r in c.execute('PRAGMA table_info(users)')]:
            c.execute('ALTER TABLE users ADD COLUMN active INTEGER NOT NULL DEFAULT 1')

def create_user(name, email, role, password):
    name, email, role = name.strip(), email.strip().lower(), role.strip().lower()
    if not name or not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', email):
        raise ValueError('Enter a name and valid email address.')
    if role not in ('employee', 'manager') or len(password) < 12:
        raise ValueError('Choose employee or manager and a password of at least 12 characters.')
    salt = secrets.token_hex(16)
    with db() as c:
        c.execute('INSERT INTO users(name,email,role,salt,password) VALUES(?,?,?,?,?)', (name, email.lower(), role, salt, password_hash(password, salt)))

class Handler(BaseHTTPRequestHandler):
    def reply(self, status, payload, headers=None):
        content = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header('Content-Type','application/json')
        self.send_header('Content-Length',str(len(content)))
        self.send_header('Cache-Control','no-store')
        self.send_header('X-Content-Type-Options','nosniff')
        for k,v in (headers or {}).items(): self.send_header(k,v)
        self.end_headers(); self.wfile.write(content)

    def session(self):
        cookie = SimpleCookie()
        try: cookie.load(self.headers.get('Cookie',''))
        except Exception: return None
        token = cookie.get('session')
        if not token: return None
        with db() as c:
            return c.execute("SELECT users.id,name,email,role,csrf,token FROM sessions JOIN users ON users.id=sessions.user_id WHERE token=? AND expires>strftime('%s','now')", (token.value,)).fetchone()

    def do_GET(self):
        user = self.session()
        if self.path == '/api/me':
            return self.reply(200, dict(user) if user else None) if not user else self.reply(200,{k:user[k] for k in ('id','name','email','role','csrf')})
        if self.path == '/api/employees':
            if not user: return self.reply(401,{'error':'Please sign in.'})
            if user['role']!='manager': return self.reply(403,{'error':'Manager access required.'})
            with db() as c:
                employees=[dict(r) for r in c.execute("SELECT users.id,name,email,active,expires FROM users LEFT JOIN invitations ON invitations.user_id=users.id WHERE role='employee' ORDER BY name")]
            return self.reply(200,employees)
        if self.path == '/api/data':
            if not user: return self.reply(401,{'error':'Please sign in.'})
            with db() as c:
                scope = '' if user['role']=='manager' else ' WHERE user_id=?'
                args = () if not scope else (user['id'],)
                sheets = [dict(r) for r in c.execute('SELECT sheets.*,users.name FROM sheets JOIN users ON users.id=sheets.user_id'+scope+' ORDER BY week DESC',args)]
                docs = [dict(r) for r in c.execute('SELECT documents.id,user_id,name,created FROM documents'+scope+' ORDER BY id DESC',args)]
                people = [dict(r) for r in c.execute('SELECT id,name FROM users')]
            for sheet in sheets: sheet['entries']=json.loads(sheet['entries'])
            return self.reply(200,{'sheets':sheets,'documents':docs,'people':people})
        if self.path.startswith('/api/documents/'):
            if not user: return self.reply(401,{'error':'Please sign in.'})
            ident = self.path.split('/')[-1]
            with db() as c: doc=c.execute('SELECT * FROM documents WHERE id=?',(ident,)).fetchone()
            if not doc or (user['role']!='manager' and doc['user_id']!=user['id']): return self.reply(404,{'error':'Document not found.'})
            body=(DATA/'uploads'/doc['storage']).read_bytes()
            self.send_response(200); self.send_header('Content-Type','application/octet-stream')
            from urllib.parse import quote
            self.send_header('Content-Disposition',"attachment; filename*=UTF-8''"+quote(doc['name']))
            self.send_header('Content-Length',str(len(body))); self.send_header('Cache-Control','no-store'); self.send_header('X-Content-Type-Options','nosniff'); self.end_headers(); self.wfile.write(body); return
        paths={'/':'index.html','/app.js':'app.js','/style.css':'style.css'}
        if self.path not in paths: return self.reply(404,{'error':'Not found.'})
        filename=paths[self.path]; body=(ROOT/'static'/filename).read_bytes()
        self.send_response(200); self.send_header('Content-Type',{'html':'text/html','js':'application/javascript','css':'text/css'}[filename.split('.')[-1]])
        self.send_header('Content-Length',str(len(body))); self.send_header('Cache-Control','no-store'); self.send_header('X-Content-Type-Options','nosniff'); self.send_header('Content-Security-Policy',"default-src 'self'; style-src 'self'; script-src 'self'; frame-ancestors 'none'; base-uri 'none'"); self.end_headers(); self.wfile.write(body)

    def do_POST(self):
        try: self.post()
        except (ValueError,KeyError,TypeError,json.JSONDecodeError): self.reply(400,{'error':'Invalid request. Check your entries.'})
        except sqlite3.IntegrityError: self.reply(409,{'error':'This record already exists.'})

    def post(self):
        length=int(self.headers.get('Content-Length','0'))
        if length<=0 or length>MAX_UPLOAD: return self.reply(413,{'error':'Request must be under 10 MB.'})
        user=self.session()
        if self.path not in ('/api/login','/api/activate'):
            if not user: return self.reply(401,{'error':'Please sign in.'})
            if not hmac.compare_digest(self.headers.get('X-CSRF-Token',''),user['csrf']): return self.reply(403,{'error':'Session check failed. Reload and try again.'})
        raw=self.rfile.read(length)
        if self.path=='/api/upload':
            from urllib.parse import unquote
            name=Path(unquote(self.headers.get('X-Filename','document'))).name
            if Path(name).suffix.lower() not in ('.pdf','.png','.jpg','.jpeg','.docx','.xlsx','.csv','.txt'): return self.reply(400,{'error':'Supported files: PDF, images, Office documents, CSV and text.'})
            storage=secrets.token_hex(24); target=DATA/'uploads'/storage
            target.write_bytes(raw)
            try:
                with db() as c: c.execute('INSERT INTO documents(user_id,name,storage,created) VALUES(?,?,?,?)',(user['id'],name,storage,datetime.date.today().isoformat()))
            except Exception:
                target.unlink(); raise
            return self.reply(201,{'ok':True})
        data=json.loads(raw)
        if self.path=='/api/invite':
            if user['role']!='manager': return self.reply(403,{'error':'Manager access required.'})
            name=str(data.get('name','')).strip(); email=str(data.get('email','')).strip().lower()
            if not name or len(name)>100 or len(email)>254 or not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+',email):
                return self.reply(400,{'error':'Enter a name and valid email address.'})
            token=secrets.token_urlsafe(32); digest=hashlib.sha256(token.encode()).hexdigest()
            with db() as c:
                c.execute('BEGIN IMMEDIATE')
                existing=c.execute('SELECT id,active FROM users WHERE email=?',(email,)).fetchone()
                if existing and existing['active']: return self.reply(409,{'error':'This email already has an active account.'})
                if existing:
                    ident=existing['id']; c.execute('DELETE FROM invitations WHERE user_id=?',(ident,))
                else:
                    salt=secrets.token_hex(16)
                    ident=c.execute("INSERT INTO users(name,email,role,salt,password,active) VALUES(?,?,'employee',?,?,0)",(name,email,salt,password_hash(secrets.token_urlsafe(48),salt))).lastrowid
                c.execute("INSERT INTO invitations VALUES(?,?,strftime('%s','now')+172800)",(digest,ident))
            return self.reply(201,{'token':token,'email':email,'expiresInHours':48})
        if self.path=='/api/activate':
            password=data.get('password',''); token=data.get('token','')
            if not isinstance(password,str) or not 12<=len(password)<=256:
                return self.reply(400,{'error':'Choose a password between 12 and 256 characters.'})
            digest=hashlib.sha256(str(token).encode()).hexdigest()
            salt=secrets.token_hex(16); hashed=password_hash(password,salt)
            with db() as c:
                c.execute('BEGIN IMMEDIATE')
                invitation=c.execute("SELECT invitations.user_id FROM invitations JOIN users ON users.id=invitations.user_id WHERE token_hash=? AND expires>strftime('%s','now') AND active=0",(digest,)).fetchone()
                if not invitation: return self.reply(400,{'error':'Invitation is invalid, expired or already used. Ask your employer for a new invitation.'})
                c.execute('UPDATE users SET salt=?,password=?,active=1 WHERE id=?',(salt,hashed,invitation['user_id']))
                c.execute('DELETE FROM invitations WHERE user_id=?',(invitation['user_id'],))
            return self.reply(200,{'ok':True})
        if self.path=='/api/login':
            with db() as c:
                account=c.execute('SELECT * FROM users WHERE email=?',(data['email'].strip().lower(),)).fetchone()
                salt=account['salt'] if account else '00'*16
                valid=hmac.compare_digest(password_hash(data['password'],salt),account['password'] if account else '00'*32)
                if not account or not account['active'] or not valid: return self.reply(401,{'error':'Unable to sign in. Check your email and password. If you are new, activate your employer invitation first.'})
                token=secrets.token_hex(32); csrf=secrets.token_hex(32)
                c.execute("DELETE FROM sessions WHERE expires<=strftime('%s','now')")
                c.execute("INSERT INTO sessions VALUES(?,?,?,strftime('%s','now')+28800)",(token,account['id'],csrf))
            return self.reply(200,{'ok':True},{'Set-Cookie':f'session={token}; HttpOnly; SameSite=Strict; Path=/; Max-Age=28800'})
        if self.path=='/api/logout':
            with db() as c: c.execute('DELETE FROM sessions WHERE token=?',(user['token'],))
            return self.reply(200,{'ok':True},{'Set-Cookie':'session=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0'})
        if self.path=='/api/sheets':
            if user['role']!='employee': return self.reply(403,{'error':'Only employees can submit timesheets.'})
            week=datetime.date.fromisoformat(data['week'])
            if week.weekday()!=0: return self.reply(400,{'error':'Choose a Monday as the week start.'})
            entries=data['entries']
            if len(entries)!=7 or any(type(x) not in (int,float) or not 0<=x<=24 for x in entries): raise ValueError()
            status='submitted' if data.get('submit') else 'draft'
            with db() as c:
                old=c.execute('SELECT status FROM sheets WHERE user_id=? AND week=?',(user['id'],week.isoformat())).fetchone()
                if old and old['status'] in ('submitted','approved'): return self.reply(409,{'error':'Submitted or approved timesheets cannot be edited.'})
                c.execute('INSERT INTO sheets(user_id,week,entries,status) VALUES(?,?,?,?) ON CONFLICT(user_id,week) DO UPDATE SET entries=excluded.entries,status=excluded.status,note=\'\'',(user['id'],week.isoformat(),json.dumps(entries),status))
            return self.reply(200,{'ok':True})
        if self.path=='/api/review':
            if user['role']!='manager': return self.reply(403,{'error':'Manager access required.'})
            status=data['status']; note=str(data.get('note',''))[:1000]
            if status not in ('approved','returned') or (status=='returned' and not note.strip()): raise ValueError()
            with db() as c:
                result=c.execute("UPDATE sheets SET status=?,note=? WHERE id=? AND status='submitted'",(status,note,data['id']))
                if not result.rowcount: return self.reply(409,{'error':'This timesheet is no longer pending.'})
            return self.reply(200,{'ok':True})
        return self.reply(404,{'error':'Not found.'})

if __name__=='__main__':
    import argparse, getpass
    parser=argparse.ArgumentParser(); parser.add_argument('--create-user',action='store_true'); parser.add_argument('--port',type=int,default=8000); args=parser.parse_args()
    init()
    if args.create_user:
        create_user(input('Name: '),input('Email: '),input('Role (employee/manager): '),getpass.getpass('Password (12+ characters): '))
        print('Account created.')
    else:
        print(f'Workforce Hub running at http://localhost:{args.port}')
        ThreadingHTTPServer(('127.0.0.1',args.port),Handler).serve_forever()
