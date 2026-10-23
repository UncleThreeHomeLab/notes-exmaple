"""Exercise the packaged frontend and its real Prometheus exporter."""
import os
from pathlib import Path
import subprocess
import sys
import time
import unittest
import urllib.error
import urllib.request


class ReleaseTelemetryTests(unittest.TestCase):
    def test_revision_header_and_separate_release_metrics(self):
        environment = dict(os.environ, OTEL_ENABLED='1', SOURCE_SHA256='a' * 64,
                           CLOUDLAB_REVISION='revision123',
                           OTEL_EXPORTER_OTLP_TRACES_ENDPOINT='http://cloudlab-alloy.cloudlab-observability.svc:4318/v1/traces')
        process = subprocess.Popen([sys.executable, 'web.py'], cwd=Path(__file__).resolve().parents[1],
                                   env=environment, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            for _ in range(450):
                try:
                    with urllib.request.urlopen('http://127.0.0.1:8080/ready', timeout=1):
                        break
                except OSError:
                    if process.poll() is not None:
                        self.fail('Packaged frontend failed to start')
                    time.sleep(0.1)
            else:
                self.fail('Packaged frontend readiness exceeded 45 seconds')
            request = urllib.request.Request('http://127.0.0.1:8080/',
                                            headers={'User-Agent': 'CloudLab-Release-Proof/1.0'})
            try:
                response = urllib.request.urlopen(request, timeout=2)
            except urllib.error.HTTPError as error:
                response = error
            with response:
                self.assertIn(response.status, (200, 503))
                if response.status == 200:
                    self.assertIn(b'<html', response.read().lower())
                self.assertEqual(response.headers['X-CloudLab-Revision'], 'revision123')
            with urllib.request.urlopen('http://127.0.0.1:8080/', timeout=2) as response:
                response.read()
            with urllib.request.urlopen('http://127.0.0.1:9091/metrics', timeout=2) as response:
                metrics = response.read().decode()
            for traffic in ('release', 'user'):
                self.assertIn('traffic="' + traffic + '"', metrics)
            self.assertIn('status="500",traffic="release"} 0.0', metrics)
            self.assertIn('cloudlab_http_duration_seconds_max{', metrics)
        finally:
            process.terminate()
            process.wait(timeout=10)
