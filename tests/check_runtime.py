"""Exercise the actual production Gunicorn entrypoint inside the built container."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

sys.path.insert(0, '/app')
import app

with tempfile.TemporaryDirectory() as temp:
    app.DATA = Path(temp) / 'data'
    app.init()
    app.create_user('Smoke manager', 'smoke@example.com', 'manager', 'smoke-password-123')
    env = dict(os.environ, WORKFORCE_ENV='production', WORKFORCE_DATA=str(app.DATA), PUBLIC_ORIGIN='https://workforce.example', TRUST_PROXY='1', PORT='8080')
    process = subprocess.Popen(['gunicorn', '--config', 'gunicorn.conf.py', 'wsgi:application'], env=env)
    def request(path, data=None, cookie=None):
        headers={'Host':'workforce.example','X-Forwarded-Proto':'https'}
        if cookie: headers['Cookie']=cookie
        body=None
        if data is not None:
            body=json.dumps(data).encode(); headers['Content-Type']='application/json'
        return urllib.request.urlopen(urllib.request.Request('http://127.0.0.1:8080'+path,data=body,headers=headers),timeout=5)
    try:
        for _ in range(100):
            try:
                with request('/healthz') as response: assert json.load(response)['status']=='ok'
                break
            except (urllib.error.URLError,ConnectionError):
                if process.poll() is not None: raise RuntimeError('Gunicorn exited before becoming healthy')
                time.sleep(.1)
        else: raise RuntimeError('Gunicorn did not become healthy')
        with request('/') as response:
            assert b'Workforce Hub' in response.read()
        with request('/api/login',{'email':'smoke@example.com','password':'smoke-password-123'}) as response:
            cookie=response.headers['Set-Cookie']
            assert all(flag in cookie for flag in ('Secure','HttpOnly','SameSite=Strict'))
        with request('/api/me',cookie=cookie.split(';')[0]) as response:
            assert json.load(response)['role']=='manager'
        print('Production Gunicorn boot, frontend serving, authentication and secure cookies verified.')
    finally:
        process.terminate()
        process.wait(timeout=15)
