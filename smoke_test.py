"""Simple smoke test using Flask test_client to avoid network binding issues.

This imports `app` from main.py (which defines the Flask app) and performs GETs
for '/', '/select_format', and '/progress/test123'. It prints status codes and
short snippets of each response for inspection.
"""

from main import app

with app.test_client() as c:
    endpoints = ['/', '/select_format', '/progress/test123']
    for ep in endpoints:
        resp = c.get(ep)
        print('----', ep, 'status=', resp.status_code)
        data = resp.get_data(as_text=True)
        print(data[:800])
        print('\n')
