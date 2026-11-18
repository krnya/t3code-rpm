import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location(
    "check_upstream", Path(__file__).resolve().parents[1] / "scripts/check-upstream.py"
)
monitor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(monitor)

COMMIT = "a" * 40


def upstream(tag="v0.1.10", **kwargs):
    return dict({
        "tag_name": tag, "draft": False, "prerelease": False,
        "published_at": "2026-11-17T01:44:04Z", "id": 42,
    }, **kwargs)


def downstream(revision=1, draft=False, asset=False, **kwargs):
    record = dict({
        "version": "0.1.10", "revision": revision, "tag": "v0.1.10",
        "commit": COMMIT, "upstream_release_id": 42, "run_id": "1", "previous": "",
    }, **kwargs)
    item = {
        "id": 17, "tag_name": f"rpm-v0.1.10-{revision}", "draft": draft,
        "assets": [{"name": monitor.BUNDLE, "state": "uploaded"}] if asset else [],
        "body": f"{monitor.MARKER}{monitor.json.dumps(record)} -->\n\nPublication",
    }
    return item


class MonitorTests(unittest.TestCase):
    def test_rejects_nonstable_releases(self):
        for release in [
            upstream("v0.1.10-nightly.20261111.4529"), upstream("v0.1.10-preview.1"),
            upstream("v00.1.10"), upstream(prerelease=True), upstream(draft=True),
            upstream(published_at=None),
        ]:
            with self.subTest(release=release):
                self.assertIsNone(monitor.stable_version(release))

    def test_scheduled_candidates_are_oldest_first(self):
        with patch.object(monitor, "releases", return_value=iter([
            upstream("v0.1.12"), upstream("v0.1.10-nightly.1"),
            upstream("v0.1.10"), upstream("v0.1.11"), upstream("v0.1.9"),
        ])):
            self.assertEqual([r["tag_name"] for r in monitor.candidates("", False)],
                             ["v0.1.10", "v0.1.11", "v0.1.12"])

    def test_new_releases_start_at_one(self):
        self.assertEqual(monitor.next_revision("0.1.10", {}, True, "20"), (1, None))

    def test_published_version_is_skipped_without_force(self):
        records = monitor.downstream_state([downstream()])
        self.assertIsNone(monitor.next_revision("0.1.10", records, False, "2"))

    def test_forced_revision_increases_but_rerun_is_idempotent(self):
        records = monitor.downstream_state([downstream(2, published_run_id="2")])
        self.assertEqual(monitor.next_revision("0.1.10", records, True, "3"), (3, None))
        self.assertIsNone(monitor.next_revision("0.1.10", records, True, "1"))
        self.assertIsNone(monitor.next_revision("0.1.10", records, True, "2"))

    def test_retry_reuses_draft_and_a_verified_bundle(self):
        with patch.object(monitor, "releases", return_value=iter([downstream(draft=True, asset=True)])):
            with patch.object(monitor, "get", side_effect=[upstream(), {"sha": COMMIT}]):
                plan = monitor.plan_release("owner/t3code-rpm", "")
        self.assertEqual(plan["revision"], 1)
        self.assertEqual(plan["draft_id"], 17)
        self.assertTrue(plan["resume"])

    def test_retry_rebuilds_only_when_no_verified_bundle_exists(self):
        with patch.object(monitor, "releases", return_value=iter([downstream(draft=True)])):
            with patch.object(monitor, "get", side_effect=[upstream(), {"sha": COMMIT}]):
                plan = monitor.plan_release("owner/t3code-rpm", "", force=True)
        self.assertEqual(plan["revision"], 1)
        self.assertFalse(plan["resume"])

    def test_pending_publication_blocks_another_version(self):
        with patch.object(monitor, "releases", return_value=iter([downstream(draft=True)])):
            with self.assertRaisesRegex(ValueError, "Finish the pending"):
                monitor.plan_release("owner/t3code-rpm", "0.1.11", manual=True)

    def test_moved_tag_blocks_rebuild(self):
        with patch.object(monitor, "releases", return_value=iter([downstream()])):
            with patch.object(monitor, "get", side_effect=[upstream(), {"sha": "b" * 40}]):
                with self.assertRaisesRegex(ValueError, "tag .* changed"):
                    monitor.plan_release("owner/t3code-rpm", "0.1.10", manual=True, force=True)


if __name__ == "__main__":
    unittest.main()
