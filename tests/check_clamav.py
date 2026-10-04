"""Container-only scanner integration check with a local signature (no employee data)."""
import hashlib
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, '/app')
import app

with tempfile.TemporaryDirectory() as temp:
    root = Path(temp)
    sample = b'WORKFORCE-ANTIVIRUS-INTEGRATION-TEST'
    signatures = root / 'test.hdb'
    signatures.write_text(hashlib.md5(sample).hexdigest() + ':' + str(len(sample)) + ':Workforce.Test.Signature\n')
    app.DATA = root / 'data'
    server = app.create_app({'TESTING': True, 'DOCUMENT_SCANNER': 'clamscan --no-summary --database=' + str(signatures), 'ALLOW_UNSCANNED_UPLOADS': False})
    app.create_user('Test', 'test@example.com', 'employee', 'test-password-123')
    client = server.test_client()
    assert client.post('/api/login', json={'email': 'test@example.com', 'password': 'test-password-123'}).status_code == 200
    csrf = client.get('/api/me').json['csrf']
    headers = {'X-CSRF-Token': csrf, 'X-Filename': 'test.txt'}
    assert client.post('/api/upload', data=sample, headers=headers).status_code == 400
    assert list((app.DATA / 'uploads').iterdir()) == []
    assert client.post('/api/upload', data=b'ordinary text', headers=headers).status_code == 201
    print('Real ClamAV rejection and clean upload acceptance verified.')
