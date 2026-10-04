import unittest, tempfile, json, hashlib
from unittest.mock import patch
from types import SimpleNamespace
from pathlib import Path
import app

class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); app.DATA=Path(self.temp.name)
        if app.DATABASE_URL:
            app.init()
            with app.db() as c:c.execute('TRUNCATE users, sessions, sheets, documents, invitations, audit, rate_limits RESTART IDENTITY CASCADE')
        self.server=app.create_app({'TESTING':True})
        self.client=self.server.test_client(use_cookies=False)
        app.create_user('Employee','employee@example.com','employee','test-password-123')
        app.create_user('Other','other@example.com','employee','test-password-123')
        app.create_user('Manager','manager@example.com','manager','test-password-123')

    def tearDown(self):
        self.temp.cleanup()

    def request(self,path,data=None,session=None,headers=None,raw=None):
        h=dict(headers or {})
        if session:h.update({'Cookie':session[0],'X-CSRF-Token':session[1]})
        body=raw if raw is not None else (json.dumps(data).encode() if data is not None else None)
        if data is not None: h.setdefault('Content-Type','application/json')
        response=self.client.open(path,method='POST' if body is not None else 'GET',data=body,headers=h)
        result=(response.status_code,response.get_json() if response.is_json else response.data,response.headers)
        response.close()
        return result

    def login(self,email):
        status,body,headers=self.request('/api/login',{'email':email,'password':'test-password-123'})
        self.assertEqual(status,200);cookie=headers['Set-Cookie'].split(';')[0]
        status,me,_=self.request('/api/me',headers={'Cookie':cookie})
        self.assertNotIn('token',me)
        return cookie,me['csrf']
    def test_approval_and_document_isolation(self):
        employee=self.login('employee@example.com');manager=self.login('manager@example.com');other=self.login('other@example.com')
        sheet={'week':'2026-09-28','entries':[8,8,8,8,8,0,0],'submit':True}
        self.assertEqual(self.request('/api/sheets',sheet,employee)[0],200)
        self.assertEqual(self.request('/api/sheets',sheet,employee)[0],409)
        _,records,_=self.request('/api/data',session=manager);sid=records['sheets'][0]['id']
        self.assertEqual(self.request('/api/review',{'id':sid,'status':'approved'},employee)[0],403)
        self.assertEqual(self.request('/api/review',{'id':sid,'status':'returned','note':'Correct Monday'},manager)[0],200)
        self.assertEqual(self.request('/api/sheets',sheet,employee)[0],200)
        self.assertEqual(self.request('/api/review',{'id':sid,'status':'approved'},manager)[0],200)
        self.assertEqual(self.request('/api/review',{'id':sid,'status':'approved'},manager)[0],409)
        self.assertEqual(self.request('/api/upload',session=employee,headers={'X-Filename':'contract.txt'},raw=b'private document')[0],201)
        _,records,_=self.request('/api/data',session=employee);did=records['documents'][0]['id']
        self.assertEqual(self.request('/api/documents/'+str(did),session=other)[0],404)
        self.assertEqual(self.request('/api/documents/'+str(did),session=manager)[1],b'private document')
        _,records,_=self.request('/api/data',session=other);self.assertEqual(records['sheets'],[]);self.assertEqual(records['documents'],[])
    def test_employee_invitation_lifecycle(self):
        manager=self.login('manager@example.com');employee=self.login('employee@example.com')
        data={'name':'New Hire','email':'new@example.com','role':'manager'}
        self.assertEqual(self.request('/api/invite',data)[0],401)
        self.assertEqual(self.request('/api/invite',data,employee)[0],403)
        self.assertEqual(self.request('/api/employees',session=employee)[0],403)
        status,first,_=self.request('/api/invite',data,manager);self.assertEqual(status,201)
        with app.db() as c:
            person=c.execute('SELECT * FROM users WHERE email=?',(data['email'],)).fetchone()
            self.assertEqual(person['role'],'employee');self.assertEqual(person['active'],0)
            self.assertNotEqual(c.execute('SELECT token_hash FROM invitations WHERE user_id=?',(person['id'],)).fetchone()[0],first['token'])
        _,second,_=self.request('/api/invite',data,manager)
        activate={'token':first['token'],'password':'test-password-123'}
        self.assertEqual(self.request('/api/activate',activate)[0],400)
        self.assertEqual(self.request('/api/login',{'email':data['email'],'password':'test-password-123'})[0],401)
        activate['token']=second['token'];activate['password']='short'
        self.assertEqual(self.request('/api/activate',activate)[0],400)
        activate['password']='test-password-123'
        with app.db() as c:c.execute('UPDATE invitations SET expires=0 WHERE user_id=?',(person['id'],))
        self.assertEqual(self.request('/api/activate',activate)[0],400)
        _,third,_=self.request('/api/invite',data,manager);activate['token']=third['token']
        self.assertEqual(self.request('/api/activate',activate)[0],200)
        self.assertEqual(self.request('/api/activate',activate)[0],400)
        self.login(data['email'])
        self.assertEqual(self.request('/api/invite',data,manager)[0],409)
        _,people,_=self.request('/api/employees',session=manager)
        self.assertTrue(next(p for p in people if p['email']==data['email'])['active'])

    def test_login_email_normalization(self):
        app.create_user(' Trimmed ',' Trimmed@Example.com ',' employee ','test-password-123')
        self.login('  TRIMMED@example.COM  ')
        self.assertEqual(self.request('/api/login',{'email':'trimmed@example.com','password':'test-password-123 '})[0],401)

    def test_security_and_validation(self):
        self.assertEqual(self.request('/api/data')[0],401)
        self.assertEqual(self.request('/api/login',{'email':'employee@example.com','password':'wrong'})[0],401)
        session=self.login('employee@example.com')
        self.assertEqual(self.request('/api/logout',{},headers={'Cookie':session[0]})[0],403)
        self.assertEqual(self.request('/api/sheets',{'week':'2026-10-06','entries':[8]*7},session)[0],400)
        self.assertEqual(self.request('/api/sheets',{'week':'2026-10-05','entries':[25]*7},session)[0],400)
        self.assertEqual(self.request('/api/upload',session=session,headers={'X-Filename':'bad.html'},raw=b'html')[0],400)
        self.assertEqual(self.request('/api/logout',{},session)[0],200)
        self.assertEqual(self.request('/api/data',session=session)[0],401)

    def test_login_throttle_and_origin(self):
        for _ in range(10):
            self.assertEqual(self.request('/api/login',{'email':'missing@example.com','password':'wrong'})[0],401)
        self.assertEqual(self.request('/api/login',{'email':'missing@example.com','password':'wrong'})[0],429)
        self.assertEqual(self.request('/api/login',{'email':'employee@example.com','password':'test-password-123'},headers={'Origin':'https://attacker.example'})[0],403)

    def test_production_cookies_and_security_headers(self):
        production=app.create_app({'TESTING':True,'PRODUCTION':True,'PUBLIC_ORIGIN':'https://workforce.example','SESSION_COOKIE_SECURE':True,'ALLOW_UNSCANNED_UPLOADS':False})
        client=production.test_client()
        response=client.post('/api/login',base_url='https://workforce.example',json={'email':'employee@example.com','password':'test-password-123'})
        self.assertEqual(response.status_code,200)
        cookie=response.headers['Set-Cookie']
        for flag in ('Secure','HttpOnly','SameSite=Strict'):self.assertIn(flag,cookie)
        self.assertIn('Strict-Transport-Security',response.headers)
        self.assertEqual(client.get('/api/me',base_url='http://workforce.example').status_code,400)
        self.assertEqual(client.get('/api/me',base_url='https://evil.example').status_code,400)
        self.assertEqual(client.get('/healthz',base_url='http://localhost').status_code,200)

    def test_password_reset_revokes_sessions_and_audits(self):
        session=self.login('employee@example.com')
        with app.db() as c:
            token=c.execute('SELECT token FROM sessions').fetchone()[0]
            self.assertNotEqual(token,session[0].split('=',1)[1])
        app.reset_password('employee@example.com','new-password-123')
        self.assertEqual(self.request('/api/data',session=session)[0],401)
        self.assertEqual(self.request('/api/login',{'email':'employee@example.com','password':'new-password-123'})[0],200)
        with app.db() as c:self.assertGreater(c.execute('SELECT count(*) FROM audit').fetchone()[0],0)

    def test_document_scanning_fails_closed(self):
        session=self.login('employee@example.com')
        self.server.config['ALLOW_UNSCANNED_UPLOADS']=False
        self.assertEqual(self.request('/api/upload',session=session,headers={'X-Filename':'a.txt'},raw=b'hello')[0],503)
        self.server.config['DOCUMENT_SCANNER']='clamscan'
        with patch('app.subprocess.run',return_value=SimpleNamespace(returncode=1)):
            self.assertEqual(self.request('/api/upload',session=session,headers={'X-Filename':'a.txt'},raw=b'hello')[0],400)
        with patch('app.subprocess.run',side_effect=FileNotFoundError()):
            self.assertEqual(self.request('/api/upload',session=session,headers={'X-Filename':'a.txt'},raw=b'hello')[0],503)
        self.assertEqual(list((app.DATA/'uploads').iterdir()),[])
        with patch('app.subprocess.run',return_value=SimpleNamespace(returncode=0)):
            self.assertEqual(self.request('/api/upload',session=session,headers={'X-Filename':'a.txt'},raw=b'hello')[0],201)

    def test_malformed_data_and_file_signatures(self):
        session=self.login('employee@example.com')
        for raw in (b'[]',b'null',b'{invalid'):
            self.assertEqual(self.request('/api/sheets',session=session,headers={'Content-Type':'application/json'},raw=raw)[0],400)
        for entries in (None,{},[float('nan')]*7,[True]*7):
            self.assertEqual(self.request('/api/sheets',{'week':'2026-10-05','entries':entries},session)[0],400)
        self.assertEqual(self.request('/api/upload',session=session,headers={'X-Filename':'fake.pdf'},raw=b'not a pdf')[0],400)
        self.assertEqual(self.request('/api/upload',session=session,headers={'X-Filename':'fake.docx'},raw=b'not a zip')[0],400)
        self.assertEqual(self.request('/api/upload',session=session,headers={'X-Filename':'big.txt'},raw=b'x'*(app.MAX_UPLOAD+1))[0],413)
        _,records,_=self.request('/api/data',session=session)
        self.assertEqual(len(records['people']),1)

    def test_employee_offboarding(self):
        employee=self.login('employee@example.com');manager=self.login('manager@example.com')
        with app.db() as c:ident=c.execute("SELECT id FROM users WHERE email='employee@example.com'").fetchone()[0]
        self.assertEqual(self.request('/api/employees/disable',{'id':ident},employee)[0],403)
        self.assertEqual(self.request('/api/employees/disable',{'id':ident},manager)[0],200)
        self.assertEqual(self.request('/api/data',session=employee)[0],401)
        self.assertEqual(self.request('/api/login',{'email':'employee@example.com','password':'test-password-123'})[0],401)

    def test_self_service_password_change(self):
        employee=self.login('employee@example.com')
        self.assertEqual(self.request('/api/password',{'current':'wrong','password':'new-password-123'},employee)[0],401)
        self.assertEqual(self.request('/api/password',{'current':'test-password-123','password':'new-password-123'},employee)[0],200)
        self.assertEqual(self.request('/api/data',session=employee)[0],401)
        self.assertEqual(self.request('/api/login',{'email':'employee@example.com','password':'new-password-123'})[0],200)

    def test_backup_contains_consistent_data_and_uploads(self):
        if app.DATABASE_URL:self.skipTest('SQLite archives are not PostgreSQL backups.')
        import tarfile,sqlite3
        from scripts.backup import backup
        employee=self.login('employee@example.com')
        self.request('/api/upload',session=employee,headers={'X-Filename':'contract.txt'},raw=b'contract')
        destination=Path(self.temp.name)/'archive.tar.gz'
        backup(destination)
        original=destination.read_bytes()
        with self.assertRaises(FileExistsError):backup(destination)
        self.assertEqual(destination.read_bytes(),original)
        with tarfile.open(destination) as archive:
            with tempfile.TemporaryDirectory() as restore:
                archive.extractall(restore)
                with sqlite3.connect(Path(restore)/'hub.sqlite3') as c:
                    storage=c.execute('SELECT storage FROM documents').fetchone()[0]
                self.assertEqual((Path(restore)/'uploads'/storage).read_bytes(),b'contract')

    def test_legacy_password_upgrade(self):
        salt='11'*16
        old=hashlib.pbkdf2_hmac('sha256',b'test-password-123',bytes.fromhex(salt),200000).hex()
        with app.db() as c:c.execute("UPDATE users SET salt=?,password=?,iterations=200000 WHERE email='employee@example.com'",(salt,old))
        self.login('employee@example.com')
        with app.db() as c:self.assertEqual(c.execute("SELECT iterations FROM users WHERE email='employee@example.com'").fetchone()[0],600000)

if __name__=='__main__':unittest.main()
