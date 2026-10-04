import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError

import app
from object_storage import SupabaseStorage, StorageUnavailable


class StorageTests(unittest.TestCase):
    def test_private_bucket_and_api_contract(self):
        storage=SupabaseStorage('https://example.supabase.co','server-test-key','workforce-documents')
        response=MagicMock()
        response.__enter__.return_value=response
        response.read.return_value=json.dumps({'public':False}).encode()
        with patch('object_storage.urlopen',return_value=response) as send:
            storage.verify_private()
            self.assertIn('/bucket/workforce-documents',send.call_args[0][0].full_url)
            storage.put('random-key',b'private')
            request=send.call_args[0][0]
            self.assertEqual(request.method,'POST')
            self.assertEqual(request.get_header('Authorization'),'Bearer server-test-key')
            self.assertEqual(request.data,b'private')
            storage.get('random-key')
            self.assertIn('/object/authenticated/',send.call_args[0][0].full_url)
        response.read.return_value=json.dumps({'public':True}).encode()
        with patch('object_storage.urlopen',return_value=response):
            with self.assertRaises(RuntimeError):storage.verify_private()
        with patch('object_storage.urlopen',side_effect=HTTPError('https://example',500,'error',{},None)):
            with self.assertRaises(StorageUnavailable):storage.get('random-key')

    def test_remote_storage_upload_download_and_isolation(self):
        with tempfile.TemporaryDirectory() as temp:
            old=app.DATA;app.DATA=Path(temp)
            try:
                if app.DATABASE_URL:
                    app.init()
                    with app.db() as c:c.execute('TRUNCATE users, sessions, sheets, documents, invitations, audit, rate_limits RESTART IDENTITY CASCADE')
                storage=MagicMock()
                storage.get.return_value=b'private document'
                server=app.create_app({'TESTING':True,'OBJECT_STORAGE':storage})
                app.create_user('Owner','storage-owner@example.com','employee','test-password-123')
                app.create_user('Other','storage-other@example.com','employee','test-password-123')
                client=server.test_client()
                client.post('/api/login',json={'email':'storage-owner@example.com','password':'test-password-123'})
                csrf=client.get('/api/me').json['csrf']
                self.assertEqual(client.post('/api/upload',data=b'private document',headers={'X-Filename':'private.txt','X-CSRF-Token':csrf}).status_code,201)
                storage.put.assert_called_once()
                self.assertEqual(list((app.DATA/'uploads').iterdir()),[])
                ident=client.get('/api/data').json['documents'][0]['id']
                response=client.get('/api/documents/'+str(ident))
                self.assertEqual(response.data,b'private document');response.close()
                client.post('/api/login',json={'email':'storage-other@example.com','password':'test-password-123'})
                self.assertEqual(client.get('/api/documents/'+str(ident)).status_code,404)
            finally:app.DATA=old

    def test_free_production_refuses_ephemeral_storage(self):
        with patch.object(app,'DATABASE_URL',''):
            with self.assertRaisesRegex(RuntimeError,'remote PostgreSQL'):
                app.create_app({'TESTING':True,'PRODUCTION':True,'PUBLIC_ORIGIN':'https://workforce.example','HOSTING_TIER':'free'})
