import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from evals.context_watch import watch
from release_snapshot import active_release, activate_release, store_release


class ContextWatchTests(unittest.TestCase):
    def exercise(self, active_matches_failure):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            staging = root / 'staging'
            make = lambda text: store_release(staging / 'releases', documents=[], dependencies={}, instructions=text,
                                             request_settings={'model': 'fixture'}, policy='top_two')
            stable, failed, newer = make('stable'), make('failed'), make('newer')
            activate_release(staging, failed if active_matches_failure else newer)
            (root / 'manifest.json').write_text(json.dumps({'name': 'frontline-context-recovery-v1',
                'completed': ['arbitrary-trace'], 'planned_requests': 1, 'releases': {'top_two': stable}}))
            (root / 'arbitrary-trace.json').write_text(json.dumps({'release_id': failed}))
            rows = [{'trace_id': 'arbitrary-trace', 'objective_pass': False, 'http_status': 200,
                     'structure': 'accepted', 'retrieval': {'missing_ids': [], 'wrong_applicability': ['unexpected-reference']}, 'fields': []}]
            with patch('evals.context_watch.summarize', return_value=rows):
                watch(root, restore_staging=True, timeout=2)
            events = [json.loads(line) for line in (root / 'monitor-events.jsonl').read_text().splitlines()]
            self.assertTrue(any(e['event'] == 'quality_check_failed' for e in events))
            self.assertEqual(active_release(staging)[0], stable if active_matches_failure else newer)
            self.assertEqual(sum(e['event'] == 'staging_restored' for e in events), int(active_matches_failure))

    def test_failed_active_release_is_restored_even_with_http_200(self):
        self.exercise(True)

    def test_late_failure_cannot_roll_back_a_different_active_release(self):
        self.exercise(False)
