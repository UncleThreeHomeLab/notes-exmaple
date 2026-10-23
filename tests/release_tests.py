"""Cluster-free checks for Notes' bounded live operations; run with release-proof."""
import copy
import json
from unittest import TestCase
from unittest.mock import patch

import release_cluster as cluster


class ClusterOperationTests(TestCase):
    def test_initial_route_structure_is_applied_by_argo_without_changing_desired_weight_ownership(self):
        app = {'metadata': {'resourceVersion': '1'}, 'spec': {'source': {'targetRevision': 'a' * 40}}}
        state = {'weights': [1], 'stable': 'initial', 'current': 'initial', 'abort': False, 'digest': 'signed'}
        def get(kind, *_args):
            if kind == 'deployment': return {'metadata': {'uid': 'old'}}
            if kind == 'replicaset': return {'status': {'availableReplicas': 2}}
            if kind.startswith('rollout'): return {'spec': {'replicas': 2}}
            raise AssertionError(kind)
        with patch.object(cluster, 'approved', return_value=app), patch.object(cluster, 'snapshot', return_value=state), \
                patch.object(cluster, 'get', side_effect=get), patch.object(cluster, 'kube') as kube:
            self.assertTrue(cluster.run({'action': 'migrate', 'digest': 'signed'})['route_initialization_requested'])
            patch_body = json.loads(kube.call_args.args[-1])
            self.assertEqual(patch_body[1]['path'], '/operation')
            sync = patch_body[1]['value']['sync']
            self.assertFalse(sync['prune'])
            self.assertEqual([row['name'] for row in sync['resources']], ['notes-web', 'notes-web-extra-0'])
            self.assertIn('RespectIgnoreDifferences=false', sync['syncOptions'])
            self.assertNotIn('syncPolicy', patch_body[1]['value'])
            state['stable'] = 'other'
            with self.assertRaises(RuntimeError):
                cluster.run({'action': 'migrate', 'digest': 'signed'})
            self.assertEqual(kube.call_count, 1)

    def test_migration_deletes_only_ready_superseded_stateless_deployment_with_preconditions(self):
        old = {'metadata': {'uid': 'old', 'resourceVersion': '123', 'annotations': {
            'argocd.argoproj.io/tracking-id': 'notes-notes:apps/Deployment:cloudlab-app-notes/notes-web'}},
            'spec': {'template': {'spec': {'volumes': [{'name': 'tmp', 'emptyDir': {}}]}}}}
        state = {'phase': 'Healthy', 'observed': True, 'stable': 'stable', 'current': 'stable',
                 'weights': [100, 0], 'abort': False, 'digest': 'sha256:' + 'a' * 64}
        def get(kind, *_args):
            return old if kind == 'deployment' else {'spec': {'selector': {'rollouts-pod-template-hash': 'stable'}}}
        with patch.object(cluster, 'approved', return_value={}), patch.object(cluster, 'snapshot', return_value=state), \
                patch.object(cluster, 'get', side_effect=get), patch.object(cluster, 'kube') as kube:
            self.assertTrue(cluster.run({'action': 'migrate', 'digest': state['digest']})['superseded_stateless_deployment_retired'])
            self.assertEqual(kube.call_args.kwargs['document']['preconditions'], {'uid': 'old', 'resourceVersion': '123'})
            for change in ({'weights': [50, 50]}, {'abort': True}, {'digest': 'other'}):
                with patch.object(cluster, 'snapshot', return_value=dict(state, **change)), self.assertRaises(RuntimeError):
                    cluster.run({'action': 'migrate', 'digest': state['digest']})
            old['spec']['template']['spec']['volumes'] = [{'persistentVolumeClaim': {'claimName': 'unexpected'}}]
            with self.assertRaises(RuntimeError):
                cluster.run({'action': 'migrate', 'digest': state['digest']})
            self.assertEqual(kube.call_count, 1)

    def test_analysis_fixture_refuses_foreign_and_measured_runs(self):
        run = {'metadata': {'uid': 'run', 'ownerReferences': [{'uid': 'foreign', 'controller': True}]},
               'spec': {'metrics': [{'name': 'errors', 'provider': {'prometheus': {'query': 'good'}}}]}}
        def get(kind, *_args):
            return run if kind.startswith('analysisrun') else {'metadata': {'uid': 'rollout'}}
        with patch.object(cluster, 'approved', return_value={}), patch.object(cluster, 'snapshot', return_value={'analysis': 'run'}), \
                patch.object(cluster, 'get', side_effect=get), patch.object(cluster, 'kube') as kube:
            with self.assertRaises(RuntimeError):
                cluster.run({'action': 'analysis-fixture', 'case': 'query-failure'})
            run['metadata']['ownerReferences'][0]['uid'] = 'rollout'
            self.assertTrue(cluster.run({'action': 'analysis-fixture', 'case': 'query-failure'})['injected'])
            patch_body = json.loads(kube.call_args.args[-1])
            self.assertEqual(patch_body[0], {'op': 'test', 'path': '/metadata/uid', 'value': 'run'})
            self.assertEqual(patch_body[1]['value'], '(')
            run['status'] = {'metricResults': [{'name': 'errors', 'count': 1}]}
            with self.assertRaises(RuntimeError):
                cluster.run({'action': 'analysis-fixture', 'case': 'query-failure'})
            self.assertEqual(kube.call_count, 1)

    def test_unapproved_application_source_refuses_all_operations(self):
        with patch.object(cluster, 'get', return_value={'spec': {}}), self.assertRaises(RuntimeError):
            cluster.approved()
