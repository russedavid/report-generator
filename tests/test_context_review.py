import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from starlette.testclient import TestClient

from evals.review_app import make_app


class ContextReviewTests(unittest.TestCase):
    def test_nonpilot_dataset_identity_and_trace_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'manifest.json').write_text(json.dumps({'artifacts': {}}))
            (root / 'review-order.json').write_text('["sample"]')
            (root / 'cases.json').write_text('[{"id":"example"}]')
            (root / 'sample.json').write_text(json.dumps({
                'trace_id': 'sample', 'input_sources': [], 'raw_output': '{}',
                'structural_status': 'accepted', 'retrieval': {'selected_ids': ['REF']},
                'release_id': 'example-release', 'transition': {'phase': 'recovered'},
            }))
            with TestClient(make_app(root, root / 'reviews.jsonl', dataset_snapshot=Path('cases.json'))) as client:
                listing = client.get('/items').json()
                self.assertEqual(listing['dataset_sha256'], hashlib.sha256((root / 'cases.json').read_bytes()).hexdigest())
                self.assertEqual(listing['human_reviewed'], 0)
                record = client.get('/trace/sample').json()
                self.assertEqual(record['release_id'], 'example-release')
                self.assertEqual(record['retrieval']['selected_ids'], ['REF'])
                self.assertEqual(client.get('/trace/unlisted').status_code, 404)
            with self.assertRaises(ValueError):
                make_app(root, root / 'reviews.jsonl', dataset_snapshot=Path('../outside.json'))
