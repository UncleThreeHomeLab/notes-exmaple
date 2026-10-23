import copy
import json
from unittest import TestCase
from unittest.mock import Mock
from release_evidence import analysis_result, traffic_result

class ReleaseEvidenceTests(TestCase):
    def test_traffic_split_evidence_rejects_wrong_weights_missing_unknown_or_dropped(self):
        good = {'stable_samples': 355, 'canary_samples': 365, 'unknown_revision': 0, 'dropped_iterations': 0}
        for change in ({}, {'stable_samples': 700, 'canary_samples': 20}, {'canary_samples': 0},
                       {'stable_samples': 288, 'canary_samples': 432}, {'canary_samples': float('nan')},
                       {'unknown_revision': 1}, {'dropped_iterations': 1}):
            document = dict(good, **change)
            process = Mock(returncode=0)
            process.communicate.return_value = (json.dumps(document), '')
            if change:
                with self.assertRaises(RuntimeError):
                    traffic_result(process)
            else:
                self.assertEqual(traffic_result(process), good)


    def test_negative_fixtures_must_establish_their_actual_failure_cause(self):
        state = {'analysis_phase': 'Successful', 'metrics': [
            {'name': name, 'phase': 'Successful', 'values': [value]} for name, value in (
                ('samples', '[300]'), ('errors', '[0]'), ('maximum', '[0.1]'), ('p95', '[0.01]'), ('scrape-health', '[1]'))]}
        self.assertEqual(analysis_result('healthy', state, {'failed_samples': 0}), 300)
        with self.assertRaises(RuntimeError):
            analysis_result('faulty', state, {'failed_samples': 10})
        faulty = copy.deepcopy(state)
        faulty['metrics'][1].update(phase='Failed', values=['[1]'])
        self.assertEqual(analysis_result('faulty', faulty, {'failed_samples': 10}), 300)
        missing = copy.deepcopy(state)
        missing['metrics'][0].update(phase='Failed', values=['[]'])
        self.assertIsNone(analysis_result('missing', missing, {'failed_samples': 0}))
        missing['metrics'][-1].update(phase='Failed', values=['[0]'])
        with self.assertRaises(RuntimeError):
            analysis_result('missing', missing, {'failed_samples': 0})

