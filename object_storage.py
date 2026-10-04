"""Private Supabase objects accessed only by the authenticated backend."""
import json
import re
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import Request, urlopen


class StorageUnavailable(RuntimeError):
    pass


class SupabaseStorage:
    def __init__(self, url, key, bucket):
        parsed = urlsplit(url)
        if parsed.scheme != 'https' or not parsed.netloc or parsed.path.strip('/') or parsed.username or parsed.query or parsed.fragment:
            raise ValueError('SUPABASE_URL must be an HTTPS project origin.')
        if not key or not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,62}', bucket):
            raise ValueError('Set the Supabase server key and a valid private bucket name.')
        self.base = url.rstrip('/') + '/storage/v1'
        self.key = key
        self.bucket = bucket

    def request(self, method, path, data=None, content_type='application/octet-stream'):
        headers = {'apikey': self.key, 'Authorization': 'Bearer ' + self.key,
                   'Content-Type': content_type, 'x-upsert': 'false'}
        request = Request(self.base + path, data=data, headers=headers, method=method)
        try:
            with urlopen(request, timeout=30) as response:
                return response.read()
        except (HTTPError, URLError, TimeoutError) as error:
            # Never return provider response bodies or credential-bearing request headers.
            raise StorageUnavailable('Document storage is temporarily unavailable.') from error

    def verify_private(self):
        try:
            metadata = json.loads(self.request('GET', '/bucket/' + self.bucket))
        except (ValueError, StorageUnavailable) as error:
            raise RuntimeError('Cannot verify the Supabase private document bucket.') from error
        if metadata.get('public') is not False:
            raise RuntimeError('The document bucket must be PRIVATE.')

    def put(self, name, contents):
        self.request('POST', '/object/' + self.bucket + '/' + quote(name, safe=''), contents)

    def get(self, name):
        return self.request('GET', '/object/authenticated/' + self.bucket + '/' + quote(name, safe=''))

    def delete(self, name):
        self.request('DELETE', '/object/' + self.bucket,
                     json.dumps({'prefixes': [name]}).encode(), 'application/json')
