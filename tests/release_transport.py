"""Existing lab SSH transport; no credentials reach k6."""
import contextlib
import os
import socket
import subprocess
import time
from dotenv import dotenv_values
from playbooks.services.files.contract import ROOT

def environment():
    values = dotenv_values(ROOT / '.env', interpolate=False)
    for prefix in ('VM', 'VM2'):
        for suffix in ('HOST', 'USER', 'PASSWORD'):
            key = prefix + '_' + suffix
            if not values.get(key):
                raise ValueError('Missing VM input: ' + key)
            os.environ[key] = values[key]
        os.environ[prefix + '_PORT'] = values.get(prefix + '_PORT') or '22'
        os.environ[prefix + '_BECOME_PASSWORD'] = values.get(prefix + '_BECOME_PASSWORD') or values[prefix + '_PASSWORD']
        os.environ[prefix + '_PUBLIC_IP'] = socket.gethostbyname(values[prefix + '_HOST'])


@contextlib.contextmanager
def tunnel(gateway):
    # Use the established SSH host key and password path; TLS still verifies the canonical gateway host.
    command = ['sshpass', '-e', 'ssh', '-N', '-p', os.environ['VM_PORT'],
               '-o', 'StrictHostKeyChecking=yes', '-o', 'UserKnownHostsFile=/state/known_hosts',
               '-o', 'ExitOnForwardFailure=yes', '-o', 'ConnectTimeout=10',
               '-o', 'PreferredAuthentications=password', '-o', 'PubkeyAuthentication=no',
               '-L', '127.0.0.1:8443:' + gateway + ':443', os.environ['VM_USER'] + '@' + os.environ['VM_HOST']]
    process = subprocess.Popen(command, env=dict(os.environ, SSHPASS=os.environ['VM_PASSWORD']),
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(30):
            if process.poll() is not None:
                raise RuntimeError('Fixture SSH transport failed')
            try:
                with socket.create_connection(('127.0.0.1', 8443), timeout=1):
                    break
            except OSError:
                time.sleep(1)
        else:
            raise RuntimeError('Fixture SSH transport timed out')
        yield
    finally:
        process.terminate()
        process.wait(timeout=15)

