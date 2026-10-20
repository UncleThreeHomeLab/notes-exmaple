"""Publication ordering, repository boundaries and credential separation without service access."""
from contextlib import contextmanager
import base64
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from ci import publisher


class PublisherTests(unittest.TestCase):
    def test_signing_identity_cannot_use_a_foreign_repository_or_shared_workflow(self):
        document, environment = self.inputs()
        publisher.configuration(document, environment)
        for identity in ('https://github.com/foreign/app/.github/workflows/ci.yaml@refs/heads/main',
                         'https://github.com/example/app/.github/workflows/ci.yaml@refs/heads/untrusted',
                         'https://github.com/example/platform/.github/workflows/application-publish.yaml@' + 'a' * 40):
            with self.subTest(identity=identity), self.assertRaises(publisher.PublicationFailure):
                publisher.configuration(dict(document, workflow_identity=identity), environment)


    def test_public_signing_requires_exact_issuer_workflow_repository_main_and_source_without_private_key(self):
        document, identity = self.inputs()
        identity.update(ACTIONS_ID_TOKEN_REQUEST_TOKEN='synthetic-oidc-request',
                        ACTIONS_ID_TOKEN_REQUEST_URL='https://oidc.example.invalid/request')
        native = {'PATH': '/usr/bin', 'HOME': '/tmp', 'DOCKER_CONFIG': '/tmp/push'}
        calls = []
        def execute(arguments, environment, **kwargs):
            calls.append((arguments, environment))
            self.assertNotIn('GH_TOKEN', environment)
            self.assertNotIn('OP_PROVISION_SERVICE_ACCOUNT_TOKEN', environment)
            return b''
        with patch.object(publisher, 'execute', side_effect=execute), \
                patch.object(publisher, 'credential', side_effect=AssertionError('Public signing must not read private key credentials')):
            publisher.sign('registry.example.invalid/cloudlab-public/example@sha256:' + 'a' * 64,
                           document, Path('/tmp'), native, identity['GITHUB_SHA'], identity)
        sign, verify = calls
        self.assertEqual(sign[0][:3], ['cosign', 'sign', '--yes'])
        self.assertIn('--new-bundle-format=false', sign[0])
        self.assertIn('--use-signing-config=false', sign[0])
        self.assertIn('--sign-container-identity=https://github.com/example/app', sign[0])
        self.assertNotIn('registry.example.invalid', ' '.join(sign[0][:-1]))
        self.assertEqual(sign[1]['ACTIONS_ID_TOKEN_REQUEST_TOKEN'], 'synthetic-oidc-request')
        self.assertNotIn('ACTIONS_ID_TOKEN_REQUEST_TOKEN', verify[1])
        expected = {'--certificate-identity': document['workflow_identity'],
                    '--certificate-oidc-issuer': 'https://token.actions.githubusercontent.com',
                    '--certificate-github-workflow-repository': 'example/app',
                    '--certificate-github-workflow-ref': 'refs/heads/main',
                    '--certificate-github-workflow-sha': identity['GITHUB_SHA']}
        for argument, value in expected.items():
            self.assertEqual(verify[0][verify[0].index(argument) + 1], value)
        self.assertNotIn('--insecure-ignore-tlog', ' '.join(verify[0]))

    def inputs(self):
        document = {'application': 'example', 'source_repository': 'example/app', 'source_repository_id': '123',
                    'visibility': 'public', 'registry': 'registry.example.invalid', 'project': 'cloudlab-public',
                    'release_repository': 'example/gitops', 'release_repository_id': 456,
                    'workflow_identity': 'https://github.com/example/app/.github/workflows/ci.yaml@refs/heads/main'}
        environment = {'PATH': '/usr/bin', 'HOME': '/tmp', 'RUNNER_ENVIRONMENT': 'self-hosted',
                       'GITHUB_REPOSITORY': 'example/app', 'GITHUB_REPOSITORY_ID': '123',
                       'GITHUB_REF': 'refs/heads/main', 'GITHUB_EVENT_NAME': 'push',
                       'GITHUB_SHA': 'b' * 40, 'GITHUB_RUN_ID': '123', 'GITHUB_RUN_ATTEMPT': '1',
                       'OP_PROVISION_SERVICE_ACCOUNT_TOKEN': 'never-pass-writer', 'GH_TOKEN': 'never-pass-caller'}
        return document, environment

    def test_only_approved_local_main_can_use_the_matching_project_and_gitops_repository(self):
        document, environment = self.inputs()
        self.assertEqual(publisher.configuration(document, environment), document)
        for key, value in (('project', 'cloudlab-private'), ('release_repository', 'foreign/gitops'),
                           ('registry', 'https://untrusted.invalid'), ('workflow_identity', 'https://github.com/example/main')):
            with self.subTest(field=key), self.assertRaises(ValueError):
                publisher.configuration(dict(document, **{key: value}), environment)
        for key, value in (('RUNNER_ENVIRONMENT', 'github-hosted'), ('GITHUB_REF', 'refs/pull/1/merge'),
                           ('GITHUB_EVENT_NAME', 'pull_request_target'), ('GITHUB_REPOSITORY_ID', '999'),
                           ('GITHUB_REPOSITORY', 'foreign/app'), ('GITHUB_SHA', 'main')):
            with self.subTest(field=key), self.assertRaises(ValueError):
                publisher.configuration(document, dict(environment, **{key: value}))

    def test_scan_failure_cannot_load_push_or_release_credentials_or_change_artifacts(self):
        document, environment = self.inputs()
        with patch.object(publisher, 'registry_environment', return_value={'PATH': '/usr/bin'}) as credentials, \
                patch.object(publisher, 'build_and_scan', side_effect=RuntimeError('scan refused')), \
                patch.object(publisher, 'execute') as native, patch.object(publisher, 'sign') as sign, \
                patch.object(publisher, 'release_token') as token, self.assertRaises(RuntimeError):
            publisher.publish(document, Path('/application'), environment)
        self.assertEqual(credentials.call_count, 1)
        self.assertEqual(credentials.call_args.args[3], 'pull')
        self.assertNotIn('OP_PROVISION_SERVICE_ACCOUNT_TOKEN', credentials.call_args.args[4])
        self.assertNotIn('GH_TOKEN', credentials.call_args.args[4])
        native.assert_not_called()
        sign.assert_not_called()
        token.assert_not_called()

    def test_success_pushes_the_scanned_digest_signs_then_proposes_one_file_and_revokes_token(self):
        document, environment = self.inputs()
        digest = 'sha256:' + 'c' * 64
        events = []
        def credentials(directory, registry, project, role, native):
            self.assertEqual(set(native), {'PATH', 'HOME'})
            self.assertEqual(project, 'cloudlab-public')
            events.append(role)
            return dict(native, DOCKER_CONFIG=role)
        def scan(source, directory, native):
            self.assertEqual(native['DOCKER_CONFIG'], 'pull')
            events.append('scan')
            return directory / 'image.tar', digest
        def native(arguments, native, **options):
            self.assertNotIn('GH_TOKEN', native)
            if options.get('missing'):
                return None
            if arguments[1] == 'push':
                self.assertEqual(native['DOCKER_CONFIG'], 'push')
                events.append('push-image')
                return b''
            self.assertEqual(native['DOCKER_CONFIG'], 'pull')
            self.assertTrue(arguments[-1].endswith('@' + digest))
            return digest.encode()
        @contextmanager
        def token(document, native):
            events.append('token')
            try:
                yield 'synthetic-scoped-writer'
            finally:
                events.append('revoke')
        def propose(repository, path, desired):
            self.assertEqual(repository, 'example/gitops')
            self.assertEqual(path, 'apps/releases/example.json')
            self.assertEqual(desired, {'digest': digest, 'source_revision': 'b' * 40, 'workflow_run_id': '123'})
            self.assertEqual(os.environ['GH_TOKEN'], 'synthetic-scoped-writer')
            self.assertNotIn('OP_PROVISION_SERVICE_ACCOUNT_TOKEN', os.environ)
            events.append('proposal')
            return {'release_changed': True, 'pull_request_only': True}
        original = dict(os.environ)
        with patch.dict(os.environ, environment, clear=True), \
                patch.object(publisher, 'registry_environment', side_effect=credentials), \
                patch.object(publisher, 'build_and_scan', side_effect=scan), \
                patch.object(publisher, 'execute', side_effect=native), \
                patch.object(publisher, 'sign', side_effect=lambda *args: events.append('sign')), \
                patch.object(publisher, 'release_token', side_effect=token), \
                patch.object(publisher.release, 'propose', side_effect=propose):
            result = publisher.publish(document, Path('/application'), os.environ)
            self.assertEqual(dict(os.environ), environment)
        self.assertEqual(events, ['pull', 'scan', 'push', 'push-image', 'sign', 'token', 'proposal', 'revoke'])
        self.assertEqual(result['digest'], digest)
        self.assertEqual(dict(os.environ), original)

    def test_registry_authentication_is_scoped_to_one_robot_and_never_printed_or_passed_in_argv(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            secrets = root / 'secrets/pull'
            secrets.mkdir(parents=True)
            for field, value in {'username': 'robot$cloudlab-public+pull', 'password': 'synthetic',
                                 'secret_version': '1'}.items():
                (secrets / field).write_text(value)
            with patch.object(publisher, 'SECRETS', root / 'secrets'):
                environment = publisher.registry_environment(root, 'registry.example.invalid', 'cloudlab-public',
                                                             'pull', {'PATH': '/usr/bin'})
                path = Path(environment['DOCKER_CONFIG']) / 'config.json'
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)
                auth = json.loads(path.read_text())['auths']
                self.assertEqual(set(auth), {'registry.example.invalid'})
                self.assertEqual(base64.b64decode(auth['registry.example.invalid']['auth']),
                                 b'robot$cloudlab-public+pull:synthetic')
                other = root / 'other'
                other.mkdir()
                with self.assertRaises(ValueError):
                    publisher.registry_environment(other, 'registry.example.invalid', 'cloudlab-private', 'pull', {})
