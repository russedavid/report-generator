import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from release_snapshot import activate_release, active_release, read_release, store_release


class ReleaseSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def store(self, instructions="preserve the facts"):
        return store_release(self.root / "releases", documents=[], dependencies={},
                             instructions=instructions, request_settings={"model": "fixture"}, policy="top_two")

    def test_identity_covers_prompt_and_restore_is_exact(self):
        first, second = self.store(), self.store("different instructions")
        self.assertNotEqual(first, second)
        activate_release(self.root, first)
        original = active_release(self.root)
        activate_release(self.root, second)
        activate_release(self.root, first)
        self.assertEqual(active_release(self.root), original)
        self.assertEqual(self.store(), first)

    def test_tampering_cannot_change_the_active_release(self):
        first, second = self.store(), self.store("new instructions")
        activate_release(self.root, first)
        target = self.root / "releases" / (second + ".json")
        target.write_text(target.read_text() + " ")
        with self.assertRaisesRegex(ValueError, "integrity"):
            activate_release(self.root, second)
        self.assertEqual(active_release(self.root)[0], first)

    def test_runtime_mismatch_and_path_traversal_fail_before_pointer_write(self):
        first = self.store()
        with patch("release_snapshot.runtime_identity", return_value={"changed": "code"}), self.assertRaisesRegex(ValueError, "runtime"):
            activate_release(self.root, first)
        self.assertFalse((self.root / "active-release.json").exists())
        with self.assertRaises(ValueError):
            read_release(self.root / "releases", "../unexpected")

    def test_snapshot_file_content_is_not_mutated_during_activation(self):
        first = self.store()
        path = self.root / "releases" / (first + ".json")
        before = path.read_bytes()
        activate_release(self.root, first)
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(json.loads(before)["selection_policy"], "top_two")
