import unittest, tempfile, threading, json, urllib.request, urllib.error
from pathlib import Path
import app

class WorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory(); app.DATA=Path(cls.temp.name); app.init()
        app.create_user('Employee','employee@example.com','employee','test-password-123')
        app.create_user('Other','other@example.com','employee','test-password-123')
        app.create_user('Manager','manager@example.com','manager','test-password-123')
        cls.server=app.ThreadingHTTPServer(('127.0.0.1',0),app.Handler)
        cls.thread=threading.Thread(target=cls.server.serve_forever,daemon=True);cls.thread.start()
        cls.url='http://127.0.0.1:'+str(cls.server.server_port)
    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown();cls.server.server_close();cls.temp.cleanup()
    def request(self,path,data=None,session=None,headers=None,raw=None):
        h=headers or {}
        if session:h.update({'Cookie':session[0],'X-CSRF-Token':session[1]})
        body=raw if raw is not None else (json.dumps(data).encode() if data is not None else None)
        req=urllib.request.Request(self.url+path,data=body,headers=h)
        try:
            r=urllib.request.urlopen(req);content=r.read()
            return r.status,json.loads(content) if r.headers.get('Content-Type')=='application/json' else content,r.headers
        except urllib.error.HTTPError as e:return e.code,json.loads(e.read()),e.headers
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

if __name__=='__main__':unittest.main()
