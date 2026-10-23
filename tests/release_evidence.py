"""Credential-free bounded traffic and analysis evidence."""
import json
import math
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]

def traffic_result(process):
    stdout, _stderr = process.communicate(timeout=220)
    try:
        document = json.loads(stdout.strip())
    except ValueError:
        error = RuntimeError('Bounded traffic summary unavailable')
        error.evidence = {'exit_code': process.returncode}
        raise error from None
    total = document['stable_samples'] + document['canary_samples']
    counts = [document[name] for name in ('stable_samples', 'canary_samples', 'unknown_revision', 'dropped_iterations')]
    valid = all(type(value) in (int, float) and math.isfinite(value) and value >= 0 for value in counts)
    # Four standard deviations account for random HTTPRoute sampling.
    split_matches = valid and total >= 680 and abs(document['canary_samples'] / total - 0.5) <= 4 * math.sqrt(0.25 / total)
    if (process.returncode or document['unknown_revision'] or document['dropped_iterations'] or total < 680 or
            not split_matches):
        error = RuntimeError('Bounded traffic thresholds or observed 50/50 split failed')
        error.evidence = dict(document, exit_code=process.returncode)
        raise error
    return document


def analysis_result(mode, state, traffic):
    """An abort must establish its intended cause, not mask a different missing signal."""
    policy = json.loads((ROOT / 'release-policy.json').read_text())
    metrics = {row['name']: row for row in state['metrics']}
    if set(metrics) != {'samples', 'errors', 'maximum', 'p95', 'scrape-health'}:
        raise RuntimeError('Release analysis evidence is incomplete')

    def scalar(name):
        try:
            values = json.loads(metrics[name]['values'][0])
            if len(values) == 1 and type(values[0]) in (int, float) and math.isfinite(values[0]):
                return values[0]
        except (ValueError, TypeError, IndexError):
            pass
        raise RuntimeError('Release analysis lacks a finite scalar: ' + name)

    if metrics['scrape-health']['phase'] != 'Successful' or scalar('scrape-health') != 1:
        raise RuntimeError('Fixture abort masked a scrape failure')
    if mode == 'missing':
        if metrics['samples']['phase'] != 'Failed' or metrics['samples']['values'] != ['[]']:
            raise RuntimeError('Missing-data fixture did not establish absent measurements')
        return None
    samples = scalar('samples')
    if mode == 'low-samples':
        if metrics['samples']['phase'] != 'Failed' or not 0 <= samples < policy['minimum_samples']:
            raise RuntimeError('Low-sample fixture did not establish insufficient measurements')
    elif metrics['samples']['phase'] != 'Successful' or samples < policy['minimum_samples']:
        raise RuntimeError('Release analysis lacks its accepted minimum samples')
    if mode == 'healthy' and (state['analysis_phase'] != 'Successful' or traffic['failed_samples'] or
                              any(row['phase'] != 'Successful' for row in metrics.values())):
        raise RuntimeError('Healthy promotion lacks successful analysis and requests')
    if mode == 'faulty' and (metrics['errors']['phase'] != 'Failed' or
                             scalar('errors') < policy['failure_rate'] or not traffic['failed_samples']):
        raise RuntimeError('Faulty fixture did not fail its error threshold')
    if mode == 'query-failure' and (not any(row['phase'] == 'Error' for row in metrics.values()) or traffic['failed_samples']):
        raise RuntimeError('Query failure fixture did not isolate the query error')
    return samples

