"""Bounded Notes inspection, native analysis fixtures and stateless migration."""
import json

from playbooks.istio.files.kube import condition, get, kube

NS = 'cloudlab-app-notes'
APP = 'notes-notes'
NAME = 'notes-web'


def approved():
    app = get('application.argoproj.io', APP, 'argocd')
    source = app.get('spec', {}).get('source', {}) if app else {}
    if (not app or app.get('spec', {}).get('project') != 'notes' or app.get('spec', {}).get('destination') != {
            'server': 'https://kubernetes.default.svc', 'namespace': NS} or
            source.get('repoURL') != 'https://github.com/UncleThreeHomeLab/yasai-cloudlab.git' or
            source.get('path') != 'apps/notes' or not source.get('helm', {}).get('valuesObject', {}).get('packaged') or
            not any(ref.get('kind') == 'ApplicationSet' and ref.get('name') == 'notes'
                    for ref in app['metadata'].get('ownerReferences', []))):
        raise RuntimeError('Notes is outside its accepted signed GitOps application')
    policy = get('policy.kyverno.io', 'application-artifacts', NS)
    delivery = get('externalsecret.external-secrets.io', 'harbor-application-pull', NS)
    if not condition(policy, 'Ready') or not condition(delivery, 'Ready') or policy['spec'].get('validationFailureAction') != 'Enforce':
        raise RuntimeError('Notes requires ready Enforce signature policy and ESO registry delivery')
    return app


def snapshot():
    app = approved()
    rollout = get('rollout.argoproj.io', NAME, NS) or {}
    status = rollout.get('status', {})
    route = get('httproute', NAME, NS) or {}
    analysis = status.get('canary', {}).get('currentStepAnalysisRunStatus', {}).get('name')
    if not analysis and rollout:
        candidates = [run for run in (get('analysisruns.argoproj.io', namespace=NS) or {'items': []})['items']
                      if any(ref.get('uid') == rollout['metadata']['uid'] and ref.get('controller')
                             for ref in run['metadata'].get('ownerReferences', []))
                      and any(arg.get('name') == 'revision' and arg.get('value') == status.get('currentPodHash')
                              for arg in run['spec'].get('args', []))]
        if candidates:
            analysis = max(candidates, key=lambda run: run['metadata']['creationTimestamp'])['metadata']['name']
    run = get('analysisrun.argoproj.io', analysis, NS) if analysis else None
    metrics = []
    for metric in (run or {}).get('status', {}).get('metricResults', []):
        metrics.append({key: metric.get(key) for key in ('name', 'phase', 'count', 'successful', 'failed', 'error')})
        metrics[-1]['values'] = [row.get('value') for row in metric.get('measurements', [])]
    image = rollout.get('spec', {}).get('template', {}).get('spec', {}).get('containers', [{}])[0].get('image', '')
    return {'phase': status.get('phase'), 'stable': status.get('stableRS'), 'current': status.get('currentPodHash'),
            'observed': str(status.get('observedGeneration')) == str(rollout.get('metadata', {}).get('generation', -1)),
            'abort': status.get('abort', False), 'analysis': analysis,
            'analysis_phase': (run or {}).get('status', {}).get('phase'), 'metrics': metrics,
            'weights': [backend.get('weight') for rule in route.get('spec', {}).get('rules', []) for backend in rule.get('backendRefs', [])],
            'sync': app.get('status', {}).get('sync', {}).get('status'),
            'operation': app.get('status', {}).get('operationState', {}).get('phase'),
            'digest': image.split('@')[-1], 'desired_digest': app['metadata']['annotations'].get('cloudlab.io/artifact-digest'),
            'source_revision': app['metadata']['annotations'].get('cloudlab.io/source-revision')}


def run(request):
    action = request['action']
    if action == 'snapshot':
        return snapshot()
    app = approved()
    if action == 'prepare':
        routes = get('httproute', namespace=NS)['items']
        private = [row for row in routes if row['metadata']['name'] == NAME + '-extra-0' and
                   row['spec']['parentRefs'][0].get('namespace') == 'cloudlab-gateway-private']
        if len(private) != 1:
            raise RuntimeError('Notes private Gateway API route is unavailable')
        retained = {}
        for kind, name in [('namespace', NS), ('secret', 'notes-database'), ('secret', 'notes-objects'),
                           ('configmap', 'database-ca')]:
            obj = get(kind, name, None if kind == 'namespace' else NS)
            if not obj:
                raise RuntimeError('Notes retained data configuration is unavailable')
            retained[kind + '/' + name] = obj['metadata']['uid']
        return {'host': private[0]['spec']['hostnames'][0], 'retained': retained,
                'gateway': get('service', 'cloudlab-istio', 'cloudlab-gateway-private')['spec']['clusterIP'],
                'state': snapshot()}
    if action == 'refresh':
        kube('annotate', 'application.argoproj.io', APP, '-n', 'argocd', 'argocd.argoproj.io/refresh=hard', '--overwrite')
        return {'refreshed': True}
    if action == 'migrate':
        state = snapshot()
        old = get('deployment', NAME, NS)
        if not old:
            return {'superseded_deployment_absent': True}
        if (state['phase'] != 'Healthy' or not state['observed'] or state['stable'] != state['current'] or
                state['weights'] != [100, 0] or state['abort'] or state['digest'] != request['digest'] or
                not old['metadata'].get('annotations', {}).get('argocd.argoproj.io/tracking-id', '').startswith(APP + ':') or
                any('persistentVolumeClaim' in volume for volume in old['spec']['template']['spec'].get('volumes', []))):
            raise RuntimeError('Notes stateless migration is not ready; old Deployment preserved')
        service = get('service', NAME, NS)
        if service['spec']['selector'].get('rollouts-pod-template-hash') != state['stable']:
            raise RuntimeError('Notes stable Service has not switched to the healthy Rollout')
        options = {'apiVersion': 'v1', 'kind': 'DeleteOptions', 'preconditions': {
            'uid': old['metadata']['uid'], 'resourceVersion': old['metadata']['resourceVersion']}, 'propagationPolicy': 'Foreground'}
        kube('delete', '--raw', '/apis/apps/v1/namespaces/' + NS + '/deployments/' + NAME, '-f', '-', document=options)
        return {'superseded_stateless_deployment_retired': True}
    if action == 'analysis-fixture':
        mode = request['case']
        if mode not in ('missing', 'low-samples', 'query-failure'):
            raise ValueError('Unsupported Notes analysis fixture')
        state = snapshot()
        if not state['analysis']:
            return {'injected': False}
        obj = get('analysisrun.argoproj.io', state['analysis'], NS)
        rollout = get('rollout.argoproj.io', NAME, NS)
        if not any(ref.get('uid') == rollout['metadata']['uid'] and ref.get('controller')
                   for ref in obj['metadata'].get('ownerReferences', [])):
            raise RuntimeError('Notes analysis ownership changed')
        index = next(i for i, metric in enumerate(obj['spec']['metrics']) if metric['name'] == ('errors' if mode == 'query-failure' else 'samples'))
        metric = obj['spec']['metrics'][index]
        if any(row['name'] == metric['name'] and row.get('count', 0) for row in obj.get('status', {}).get('metricResults', [])):
            raise RuntimeError('Analysis fixture arrived after measurement; injection refused')
        query = '(' if mode == 'query-failure' else ('vector(0)' if mode == 'low-samples' else 'cloudlab_missing_release_fixture')
        kube('patch', 'analysisrun.argoproj.io', state['analysis'], '-n', NS, '--type=json', '-p', json.dumps([
            {'op': 'test', 'path': '/metadata/uid', 'value': obj['metadata']['uid']},
            {'op': 'replace', 'path': '/spec/metrics/' + str(index) + '/provider/prometheus/query', 'value': query}]))
        return {'injected': True}
    raise ValueError('Unsupported Notes release operation')


if __name__ == '__main__':
    import sys
    try:
        print(json.dumps(run(json.load(sys.stdin))))
    except (ValueError, RuntimeError) as error:
        raise SystemExit(str(error)) from None
    except Exception:
        raise SystemExit('Notes release operation failed; private diagnostics withheld') from None
