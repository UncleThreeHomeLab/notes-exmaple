import json
import os
import threading
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request

import web
import api


class HTTPTests(unittest.TestCase):
    def serve(self, handler):
        server = web.ThreadingHTTPServer(('127.0.0.1', 0), handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 5)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return 'http://127.0.0.1:' + str(server.server_port)

    def test_frontend_settings_do_not_expose_private_address(self):
        with patch.dict(os.environ, OIDC_ISSUER='https://login.example.invalid/realms/applications',
                        OIDC_CLIENT='notes-web', OIDC_AUDIENCE='notes-api',
                        OPERATIONS_HOST='notes.internal.example.invalid'):
            base = self.serve(web.Handler)
            with urllib.request.urlopen(base + '/settings.json', timeout=5) as response:
                raw = response.read()
                self.assertFalse(json.loads(raw)['operations'])
                self.assertNotIn(b'notes.internal.example.invalid', raw)
                self.assertEqual(response.headers['Cache-Control'], 'no-store')
            with urllib.request.urlopen(base + '/', timeout=5) as response:
                self.assertIn(b'<html', response.read().lower())
                self.assertIn("frame-ancestors 'none'", response.headers['Content-Security-Policy'])

    def test_dependency_failure_is_generic(self):
        base = self.serve(web.Handler)
        with patch.object(web, 'dependency_ready', return_value=False):
            with self.assertRaises(urllib.error.HTTPError) as error:
                urllib.request.urlopen(base + '/health', timeout=5)
            self.assertEqual(error.exception.code, 503)
            self.assertEqual(json.loads(error.exception.read()), {'ready': False})

    def test_api_rejects_missing_or_malformed_credentials(self):
        base = self.serve(api.Handler)
        for authorization in ('', 'Bearer malformed'):
            request = urllib.request.Request(base + '/api/notes', headers={'Authorization': authorization})
            with self.assertRaises(urllib.error.HTTPError) as error:
                urllib.request.urlopen(request, timeout=5)
            self.assertEqual(error.exception.code, 401)


if __name__ == '__main__':
    unittest.main()
