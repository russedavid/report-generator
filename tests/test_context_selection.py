import tempfile
import unittest
from pathlib import Path

from context_selection import select_context
from retrieval import ReferenceIndex


def document(id, text, **kwargs):
    return dict(id=id, title=id, text=text, models=["fixture"], revision=None,
                status="current", owner_id=None, version="test-v1", license="CC0-1.0",
                url="https://example.invalid/reference", **kwargs)


class ContextSelectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)

    def index(self, docs):
        return ReferenceIndex(Path(self.temp.name) / "test.sqlite3", docs)

    def select(self, index, dependencies, **kwargs):
        return select_context(index, "recovery", model="fixture", actor="bob",
                              policy="primary_with_dependencies", dependencies=dependencies, **kwargs)

    def test_required_passage_does_not_need_to_match_the_search_query(self):
        index = self.index([document("A", "recovery procedure"), document("B", "calibration offset")])
        result, trace = self.select(index, {"A": ["B"]})
        self.assertEqual([d["id"] for d in result], ["A", "B"])
        self.assertEqual(trace["ranked_ids"], ["A"])

    def test_inaccessible_revoked_and_wrong_revision_dependencies_withhold_the_whole_set(self):
        for changes in ({"owner_id": "alice"}, {"status": "revoked"}, {"revision": "B"}, {"models": ["other"]}):
            with self.subTest(changes=changes):
                index = self.index([document("A", "recovery procedure"), dict(document("B", "reference"), **changes)])
                result, trace = self.select(index, {"A": ["B"]})
                self.assertEqual(result, [])
                self.assertEqual(trace["withheld_reason"], "required_passage_unavailable")

    def test_cycles_and_shared_dependencies_terminate_without_duplicates(self):
        index = self.index([document("A", "recovery"), document("B", "reference")])
        result, _ = self.select(index, {"A": ["B", "B"], "B": ["A"]})
        self.assertEqual([d["id"] for d in result], ["A", "B"])

    def test_dependency_closure_is_atomic_at_count_and_character_limits(self):
        index = self.index([document("A", "recovery"), document("B", "reference"), document("C", "offset")])
        result, trace = self.select(index, {"A": ["B"], "B": ["C"]})
        self.assertEqual(result, [])
        self.assertEqual(trace["withheld_reason"], "dependency_count_budget")
        result, trace = self.select(index, {"A": ["B"]}, max_chars=10)
        self.assertEqual(result, [])
        self.assertEqual(trace["withheld_reason"], "dependency_character_budget")

    def test_top_two_default_keeps_its_original_ranking_and_budget_behavior(self):
        index = self.index([document("A", "recovery procedure"), document("B", "recovery reference")])
        result, _ = select_context(index, "recovery", model="fixture")
        self.assertEqual(result, index.search("recovery", model="fixture"))
        result, _ = select_context(index, "recovery", model="fixture", max_chars=0)
        self.assertEqual(result, [])

    def test_invalid_policy_or_dependency_shape_fails_explicitly(self):
        index = self.index([document("A", "recovery")])
        with self.assertRaises(ValueError):
            select_context(index, "recovery", model="fixture", policy="unknown")
        with self.assertRaises(ValueError):
            self.select(index, {"A": "B"})


if __name__ == "__main__":
    unittest.main()
