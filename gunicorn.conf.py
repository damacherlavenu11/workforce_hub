import os

bind = '0.0.0.0:' + os.environ.get('PORT', '8000')
workers = 1
worker_class = 'gthread'
threads = 4
timeout = 90
graceful_timeout = 30
max_requests = 1000
max_requests_jitter = 100
accesslog = '-'
errorlog = '-'
# Exclude query strings, cookies and request bodies from access logs.
access_log_format = '%(t)s %(m)s %(U)s %(s)s %(L)s'
