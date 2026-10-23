"""Measure actual signed Notes releases through their existing GitOps application."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import time

from release_evidence import analysis_result, traffic_result
from release_transport import environment, tunnel
from playbooks.services.files.contract import ROOT

HERE = Path(__file__).resolve().parent
CHECKPOINT = Path('/recovery/notes-rollouts-proof.json')
RESULT = Path('/recovery/notes-rollouts-result.json')


def remote(action, **request):
    from playbooks.access.files.preflight import ssh
    source = (HERE / 'release_cluster.py').read_text()
    try:
        return json.loads(ssh('VM', os.environ['VM_HOST'],
            'python3 -c ' + shlex.quote("import sys;sys.path.insert(0,'/opt/cloudlab');\n" + source),
            input=json.dumps(dict(request, action=action)), timeout=180,
            diagnostics='/recovery/notes-rollouts-remote-error.log'))
    except RuntimeError:
        raise RuntimeError('Notes release operation failed: ' + action + '; private diagnostics retained') from None


def wait_for(predicate, label, timeout=600):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        state = remote('snapshot')
        if predicate(state):
            return state
        time.sleep(5)
    raise RuntimeError('Notes release timed out: ' + label)


def save(path, document):
    path.write_text(json.dumps(document, indent=2))
    path.chmod(0o600)


def traffic(host, stable, canary):
    if not subprocess.check_output(['k6', 'version'], text=True, timeout=10).startswith('k6 v2.3.0 '):
        raise RuntimeError('Notes proof requires pinned k6')
    return subprocess.Popen(['k6', 'run', '--quiet', '--log-output=none', '--no-usage-report', str(HERE / 'traffic.k6.js')],
        env={'PATH': os.environ['PATH'], 'NO_PROXY': '*', 'RELEASE_ORIGIN': 'https://' + host + ':8443',
             'STABLE_REVISION': stable, 'CANARY_REVISION': canary},
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


def measure(case, digest):
    checkpoint = json.loads(CHECKPOINT.read_text())
    before = remote('prepare')
    if before['retained'] != checkpoint['retained']:
        raise RuntimeError('Notes retained configuration identities changed')
    previous = before['state']['stable']
    started = time.monotonic()
    state = wait_for(lambda state: state['digest'] == digest and state['current'] != previous and
                     state['weights'] == [50, 50] and not state['abort'], 'reviewed release and weighted traffic', timeout=1800)
    # Count rollout timing from observed weighted traffic; GitHub/Argo polling delay is separate.
    weighted = time.monotonic()
    injected = False
    observations = []
    decision = None
    with tunnel(before['gateway']):
        process = traffic(before['host'], previous, state['current'])
        try:
            while process.poll() is None:
                state = remote('snapshot')
                elapsed = time.monotonic() - weighted
                if decision is None and (state['abort'] or state['phase'] == 'Healthy' and state['stable'] == state['current']):
                    decision = round(elapsed, 3)
                if case in ('missing', 'low-samples', 'query-failure') and not injected and state['analysis']:
                    injected = remote('analysis-fixture', case=case)['injected']
                if elapsed <= 120:
                    if state['weights'] != [50, 50] or state['abort']:
                        raise RuntimeError('Notes split changed before the accepted measurement duration')
                    observations.append({'seconds': round(elapsed, 1), 'weights': state['weights'], 'sync': state['sync']})
                if 55 <= elapsed <= 75:
                    remote('refresh')
                time.sleep(5)
            measured = traffic_result(process)
        finally:
            if process.poll() is None:
                process.terminate()
                process.communicate(timeout=15)
    state = wait_for(lambda state: state['abort'] or state['phase'] == 'Healthy' and state['stable'] == state['current'], 'analysis decision', timeout=120)
    samples = analysis_result(case, state, measured)
    if state['weights'] != [100, 0] or (case != 'healthy' and not state['abort']) or (case == 'healthy' and state['abort']):
        raise RuntimeError('Notes release did not converge to its required traffic outcome')
    if case != 'healthy' and state['stable'] != previous:
        raise RuntimeError('Aborted Notes release changed its stable revision')
    if case in ('missing', 'low-samples', 'query-failure') and not injected:
        raise RuntimeError('Required Notes analysis fixture was not injected')
    if not any(row['seconds'] >= 65 and row['sync'] == 'Synced' for row in observations):
        raise RuntimeError('Argo reconciliation was not observed at delegated Notes weights')
    stable_requests = 0
    if case != 'healthy':
        with tunnel(before['gateway']):
            # A new k6 connection per request verifies post-abort routing through the same Gateway.
            script = """import http from 'k6/http'; import {check} from 'k6';
export const options={vus:1,iterations:30,noConnectionReuse:true,maxRedirects:0,
 hosts:{[__ENV.HOST+':8443']:'127.0.0.1:8443'},thresholds:{checks:['rate==1']},
 userAgent:'CloudLab-Release-Proof/1.0',systemTags:[]};
export default function(){const r=http.get('https://'+__ENV.HOST+':8443/',{headers:{Host:__ENV.HOST},timeout:'2s'});
 check(r,{'stable Notes response':r=>r.status===200&&r.headers['X-Cloudlab-Revision']===__ENV.STABLE});}
export function handleSummary(){return {stdout:''};}"""
            completed = subprocess.run(['k6', 'run', '--quiet', '--log-output=none', '--no-usage-report', '-'],
                input=script, env={'PATH': os.environ['PATH'], 'NO_PROXY': '*', 'HOST': before['host'], 'STABLE': previous},
                capture_output=True, text=True, timeout=80)
            if completed.returncode:
                raise RuntimeError('New Notes requests did not return to stable')
            stable_requests = 30
    receipt = {'case': case, 'digest': digest, 'source_revision': state['source_revision'],
               'prometheus_samples': samples, 'decision_seconds_after_weighted': decision,
               'wait_and_measure_seconds': round(time.monotonic() - started, 3),
               'traffic': measured, 'analysis': state['metrics'], 'analysis_phase': state['analysis_phase'],
               'weight_observations': observations, 'new_stable_requests': stable_requests}
    result = json.loads(RESULT.read_text()) if RESULT.exists() else {'runs': []}
    result['runs'].append(receipt)
    result['restored'] = False
    save(RESULT, result)
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('converge', 'prepare', 'migrate', 'measure', 'restored', 'inspect'))
    parser.add_argument('--case', choices=('healthy', 'faulty', 'missing', 'low-samples', 'query-failure'))
    parser.add_argument('--digest')
    args = parser.parse_args()
    if args.action in ('measure', 'migrate', 'restored') and not re.fullmatch('sha256:[a-f0-9]{64}', args.digest or ''):
        parser.error('Operation requires the reviewed signed digest')
    if args.action == 'measure' and not args.case:
        parser.error('Measurement requires an explicit case')
    lock = Path('/recovery/notes-rollouts-proof.lock').open('a')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    if json.loads((ROOT / 'charts/service/release-policy.json').read_text()) != json.loads((HERE.parent / 'release-policy.json').read_text()):
        raise RuntimeError('Notes release policy differs from accepted platform thresholds')
    environment()
    if args.action == 'converge':
        from playbooks.argocd.files.render import committed_payload
        source = committed_payload()
        applies = []
        for _ in range(2):
            stats = {}
            for component in ('argocd/010-bootstrap', 'services/010-bootstrap'):
                subprocess.run(['ansible-playbook', '-i', 'inventory.yml', 'playbooks/' + component + '.yml'],
                               cwd=ROOT / 'ansible', check=True, timeout=600)
                stats[component] = json.loads(Path('/tmp/lab-stats.json').read_text())
            applies.append(stats)
        if any(row['changed'] or row.get('failures') or row.get('unreachable')
               for stats in applies[1].values() for row in stats.values()):
            raise RuntimeError('Notes scoped repeat apply did not converge with zero changes')
        result = {'platform_revision': source['revision'], 'scoped_applies': applies}
        save(Path('/recovery/notes-rollouts-setup.json'), result)
    elif args.action == 'prepare':
        if CHECKPOINT.exists():
            raise RuntimeError('Existing Notes proof checkpoint retained; inspect before starting another')
        value = remote('prepare')
        save(CHECKPOINT, {'retained': value['retained'], 'initial': value['state']})
        result = {'prepared': True, 'initial': value['state']}
    elif args.action == 'inspect':
        result = remote('snapshot')
    elif args.action == 'migrate':
        result = remote('migrate', digest=args.digest)
    elif args.action == 'measure':
        result = measure(args.case, args.digest)
    else:
        state = wait_for(lambda state: state['digest'] == args.digest and state['desired_digest'] == args.digest and
                         state['phase'] == 'Healthy' and not state['abort'] and state['stable'] == state['current'] and
                         state['weights'] == [100, 0] and state['sync'] == 'Synced', 'known-good signed Notes release')
        checkpoint = json.loads(CHECKPOINT.read_text())
        if remote('prepare')['retained'] != checkpoint['retained']:
            raise RuntimeError('Notes retained configuration identities changed')
        result = json.loads(RESULT.read_text())
        result.update(restored=True, final=state)
        save(RESULT, result)
    print(json.dumps(result), flush=True)


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print(str(error) if isinstance(error, (ValueError, RuntimeError)) else 'Notes release proof incomplete; private diagnostics withheld', flush=True)
        raise SystemExit(1) from None
