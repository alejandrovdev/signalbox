"""Tests of the atoms that are not git: patterns, JSON-lines logs, the lock, and how the host
reads `ps` and `lsof` (through stub programs)."""

import unittest

from signalbox.atoms import lock, logs, patterns
from signalbox.host import Host
from tests import repos


class Patterns(unittest.TestCase):
    def test_a_pattern_without_a_slash_matches_a_name_anywhere(self):
        self.assertTrue(patterns.matches("node_modules/", "node_modules"))
        self.assertTrue(patterns.matches("packages/web/node_modules", "node_modules"))
        self.assertTrue(patterns.matches("packages/web/tsconfig.tsbuildinfo", "*.tsbuildinfo"))
        self.assertFalse(patterns.matches("node_modules_old", "node_modules"))

    def test_a_pattern_with_a_slash_matches_from_the_top_and_star_stays_in_a_folder(self):
        self.assertTrue(patterns.matches("docs/features/a.md", "docs/features/*.md"))
        self.assertFalse(patterns.matches("docs/features/old/a.md", "docs/features/*.md"))
        self.assertTrue(patterns.matches("docs/features/old/a.md", "docs/**/*.md"))
        self.assertFalse(patterns.matches("other/docs/features/a.md", "docs/features/*.md"))
        self.assertTrue(patterns.matches(".husky/_/", ".husky/_"))
        self.assertTrue(patterns.any_match("a/dist", ("x", "dist")))


class Logs(repos.Scratch):
    def test_reads_objects_and_counts_the_lines_it_skips(self):
        path = self.tmp / "log.jsonl"
        self.assertEqual(logs.read(path), ([], 0), "a missing log has no record")
        path.write_text('{"a": 1}\nnot json\n[1]\n\n{"b": 2}\n')
        self.assertEqual(logs.read(path), ([{"a": 1}, {"b": 2}], 2))

    def test_append_makes_the_folder_and_never_raises(self):
        path = self.tmp / "new" / "log.jsonl"
        self.assertTrue(logs.append(path, {"a": 1}))
        self.assertTrue(logs.append(path, {"b": "é"}))
        self.assertEqual(path.read_text(encoding="utf8"), '{"a":1}\n{"b":"é"}\n')
        blocked = self.tmp / "file"
        blocked.write_text("")
        self.assertFalse(logs.append(blocked / "log.jsonl", {"a": 1}), "a log under a file cannot be written")


class Lock(repos.Scratch):
    def test_take_holds_it_until_closed_and_probe_sees_it(self):
        path = self.tmp / "lane.lock"
        self.assertEqual(lock.probe(path), lock.FREE, "a lock nobody made was never taken")
        self.assertFalse(path.exists(), "probing creates nothing")
        held = lock.take(path)
        self.assertEqual(lock.probe(path), lock.BUSY)
        self.assertIsNone(lock.take(path, wait=False))
        held.close()
        self.assertEqual(lock.probe(path), lock.FREE)
        again = lock.take(path, wait=False)
        self.assertIsNotNone(again)
        again.close()


class Processes(repos.Scratch):
    def test_start_dates_in_utc_then_in_the_local_zone(self):
        self.stub("ps", 'if [ "$TZ" = UTC ]; then echo "Mon Nov 24 15:17:16  2026"; else echo "Mon Nov 24 09:17:16 2026"; fi')
        host = Host(self.environ)
        self.assertEqual(host.start_dates(42), ["Mon Nov 24 15:17:16 2026", "Mon Nov 24 09:17:16 2026"])

    def test_start_dates_of_a_process_that_does_not_run(self):
        self.stub("ps", "exit 1")
        self.assertEqual(Host(self.environ).start_dates(42), [])

    def test_directories_of_every_process(self):
        self.stub("lsof", 'printf "p12\\nfcwd\\nn/tmp/a b\\np13\\nfcwd\\nn/srv\\n"; exit 1')
        self.assertEqual(Host(self.environ).directories(), [(12, "/tmp/a b"), (13, "/srv")])

    def test_directories_when_lsof_cannot_be_read(self):
        self.stub("lsof", "exit 1")
        self.assertIsNone(Host(self.environ).directories())
        self.environ["PATH"] = str(self.bin)
        (self.bin / "lsof").unlink()
        self.assertIsNone(Host(self.environ).directories())


if __name__ == "__main__":
    unittest.main()
