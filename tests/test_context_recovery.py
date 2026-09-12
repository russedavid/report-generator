import asyncio
import copy
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from evals.context_recovery import build_item, execute, prepare, report_cases, retrieval_result
from release_snapshot import active_release


class ContextRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.output = Path(self.temp.name) / "run"
        comparison = patch("evals.context_recovery.retrieval_comparison", return_value=[])
        comparison.start()
        self.addCleanup(comparison.stop)

    def test_preparation_freezes_comparable_requests_and_keeps_labels_out(self):
        manifest, requests = prepare(self.output)
        self.assertEqual(len(requests), 19)
        by_key = {(r["case_id"], r["round"], r["variant"]): r for r in requests if "round" in r}
        for case in report_cases()[:4]:
            for round in (1, 2):
                arms = [by_key[case["id"], round, policy] for policy in ("top_two", "primary_with_dependencies")]
                left, right = [copy.deepcopy(r["request"]) for r in arms]
                # Context/source-ID schema varies with selected evidence; system instructions and settings do not.
                self.assertEqual(left["messages"][0], right["messages"][0])
                for key in ("model", "temperature", "max_completion_tokens", "reasoning_effort"):
                    self.assertEqual(left[key], right[key])
                self.assertEqual(arms[0]["original_sources"], arms[1]["original_sources"])
        case = report_cases()[0]
        case["reference"] = {"hidden": "EVALUATOR_SECRET_SENTINEL"}
        item = build_item(self.output / "staging", manifest["releases"]["top_two"], case, "probe", "probe")
        self.assertNotIn("EVALUATOR_SECRET_SENTINEL", json.dumps(item["request"]))
        self.assertTrue(any(s.get("reference_version") for s in item["input_sources"]))
        self.assertEqual(active_release(self.output / "staging")[0], manifest["releases"]["top_two"])

    def test_corrupted_metadata_is_checked_against_independent_authority(self):
        manifest, requests = prepare(self.output)
        bad = next(r for r in requests if r.get("phase") == "regression")
        authority = json.loads((self.output / "reference-authority.json").read_text())
        selected = [d for d in authority if d["id"] in bad["retrieval"]["selected_ids"]]
        result = retrieval_result({"id": "probe", "model": "raspberry-pi-4", "expected_ids": []}, authority, selected, {})
        self.assertTrue(result["wrong_applicability"])
        self.assertFalse(result["complete"])

    def test_interrupted_recovery_restores_baseline_configuration(self):
        async def calls(args, output, requests, manifest):
            if requests[0].get("phase") == "regression":
                raise RuntimeError("simulated process boundary error")
            for item in requests:
                (output / (item["trace_id"] + ".json")).write_text("{}")
                manifest["completed"].append(item["trace_id"])
            manifest["status"] = "finished"

        async def no_wait(*_):
            pass

        args = SimpleNamespace(output=self.output, interval=65, prepare=False, credentials=None)
        with patch("evals.context_recovery.call_requests", calls), patch("evals.context_recovery.asyncio.sleep", no_wait):
            with self.assertRaisesRegex(RuntimeError, "simulated"):
                asyncio.run(execute(args))
        manifest = json.loads((self.output / "manifest.json").read_text())
        self.assertEqual(active_release(self.output / "staging")[0], manifest["releases"]["top_two"])
