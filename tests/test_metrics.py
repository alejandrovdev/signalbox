"""Tests of the flow metrics (`metrics`) over a temporary repository with merges at fixed
dates, real git, a stub `gh` and a lane log."""

import datetime
import json
import unittest

from signalbox import repository
from signalbox.features import metrics
from tests import repos
from tests.repos import lines

SINCE = datetime.date(2026, 11, 10)
UNTIL = datetime.date(2026, 11, 13)
FEATURE_DOCS = ("plans/*.txt",)
GH_LIST = "pr list --state merged --limit 500 --json number,createdAt,mergedAt"


def lane_run(started_at, status, merge_commit=None, wait=0, gate=None, attempts=1, failed_step=None):
    return dict(
        repos.lane_record(1, started_at, status),
        mergeCommit=merge_commit,
        waitSeconds=wait,
        gateSeconds=gate,
        attempts=attempts,
        failedStep=failed_step,
    )


class Metrics(repos.Scratch):
    def setUp(self):
        super().setUp()
        self.host.environ["TZ"] = "UTC"

    def merge_branch(self, repo, name, start, files, date):
        """Commits the files on a new branch from `start`, then merges it into main at `date`."""
        self.git(repo, "checkout", "-qb", name, start)
        for file, content in files.items():
            self.write(repo / file, content)
        self.commit_all(repo, name, "2026-11-02T00:00:00Z")
        self.git(repo, "checkout", "-q", "main")
        self.git(repo, "merge", "-q", "--no-ff", "-m", "Merge %s" % name, name, date=date)

    def repo_with_merges(self):
        """Main's first-parent line: m0 before the window; m1 fresh; m2, m3 and m4 from branches
        cut at m0 (1, 2 and 4 first-parent commits behind); m3 and m4 changed files main had
        changed too; m2 (README.md) and m5 (a file the feature-document globs match) touch
        documentation only and came less than two minutes after the previous merge; a direct
        commit on main inside the window, and m6 after the window. origin/main is at main."""
        repo = self.tmp / "repo"
        repo.mkdir()
        self.git(repo, "init", "-q", "-b", "main")
        self.write(repo / "src/app.py", lines())
        self.write(repo / "README.md", lines())
        self.commit_all(repo, "base", "2026-11-01T00:00:00Z")
        self.merge_branch(repo, "f0", "main", {"docs/guide.md": "guide"}, "2026-11-05T12:00:00Z")
        m0 = self.git(repo, "rev-parse", "HEAD").strip()
        self.merge_branch(repo, "f1", m0, {"src/app.py": lines({1: "f1"})}, "2026-11-10T12:00:00Z")
        self.merge_branch(repo, "f2", m0, {"README.md": lines({1: "f2"})}, "2026-11-10T12:01:00Z")
        self.merge_branch(repo, "f3", m0, {"src/app.py": lines({3: "f3"}), "docs/x.md": "x"}, "2026-11-11T03:00:00Z")
        self.write(repo / "other.txt", "direct")
        self.commit_all(repo, "direct", "2026-11-11T10:00:00Z")
        self.merge_branch(
            repo, "f4", m0, {"README.md": lines({3: "f4"}), "src/app.py": lines({5: "f4"})}, "2026-11-12T10:00:00Z"
        )
        self.merge_branch(repo, "f5", "main", {"plans/data.txt": "1,2"}, "2026-11-12T10:01:30Z")
        self.merge_branch(repo, "f6", "main", {"notes/later.txt": "later"}, "2026-11-14T10:00:00Z")
        self.git(repo, "update-ref", "refs/remotes/origin/main", "main")
        return repo

    def open(self, folder, **values):
        values.setdefault("feature_docs", FEATURE_DOCS)
        return repository.open(repos.project(self.tmp / "repo", **values), self.host, folder)

    def report(self, folder, as_json=True, project=None, **options):
        options.setdefault("since", SINCE)
        options.setdefault("until", UNTIL)
        options.setdefault("github", False)
        self.printed = []
        code = metrics.report(self.open(folder, **(project or {})), self.host, self.out, as_json=as_json, **options)
        self.assertEqual(code, 0)
        return json.loads(self.text()) if as_json else self.text()

    def test_measures_the_merges_on_the_base_first_parent_line_in_the_window(self):
        repo = self.repo_with_merges()
        report = self.report(repo)
        self.assertEqual(report["window"], {"since": "2026-11-10T00:00:00.000Z", "until": "2026-11-13T00:00:00.000Z"})
        self.assertEqual(report["base"], "origin/main")
        self.assertEqual(
            report["merges"],
            {
                "total": 5,
                "perDay": {"2026-11-10": 2, "2026-11-11": 1, "2026-11-12": 2},
                "stale": {"count": 3, "share": 0.6, "maxBehind": 4},
                "staleSharedFiles": {
                    "merges": 2,
                    "topFiles": [{"file": "src/app.py", "merges": 2}, {"file": "README.md", "merges": 1}],
                },
                "docsOnly": 2,
                "within2Minutes": 2,
                "outsideLane": 5,
            },
        )
        self.assertEqual(list(report), ["window", "base", "merges", "pullRequests", "githubError", "lane", "live"])

    def test_counts_the_merges_per_local_day(self):
        repo = self.repo_with_merges()
        self.host.environ["TZ"] = "America/Mexico_City"
        report = self.report(repo)
        self.assertEqual(report["window"], {"since": "2026-11-10T06:00:00.000Z", "until": "2026-11-13T06:00:00.000Z"})
        self.assertEqual(report["merges"]["perDay"], {"2026-11-10": 3, "2026-11-12": 2})

    def test_reads_the_base_as_last_fetched(self):
        repo = self.repo_with_merges()
        self.git(repo, "update-ref", "refs/remotes/origin/main", "main~2")
        self.assertEqual(self.report(repo)["merges"]["total"], 4)

        self.git(repo, "update-ref", "refs/remotes/origin/release", "main~1")
        report = self.report(repo, project={"base": "release"})
        self.assertEqual(report["base"], "origin/release")
        self.assertEqual(report["merges"]["total"], 5)

    def test_defaults_to_the_last_fourteen_days_until_now(self):
        repo = self.repo_with_merges()
        self.host.now = datetime.datetime(2026, 11, 24, 15, 30, tzinfo=datetime.timezone.utc).timestamp()
        report = self.report(repo, since=None, until=None)
        self.assertEqual(report["window"], {"since": "2026-11-10T00:00:00.000Z", "until": "2026-11-24T15:30:00.000Z"})
        self.assertEqual(report["merges"]["total"], 6)

    def test_refuses_a_window_that_ends_before_it_starts(self):
        repo = self.repo_with_merges()
        with self.assertRaises(repository.Refusal) as refused:
            self.report(repo, since=UNTIL, until=SINCE)
        self.assertEqual(str(refused.exception), "--since must come before --until")
        with self.assertRaises(repository.Refusal):
            self.report(repo, since=SINCE, until=SINCE)
        self.assertEqual(self.printed, [])

    def test_counts_documentation_by_file_and_never_an_empty_merge(self):
        repo = self.tmp / "repo"
        repo.mkdir()
        self.git(repo, "init", "-q", "-b", "main")
        self.write(repo / "README.md", lines())
        self.commit_all(repo, "base", "2026-11-01T00:00:00Z")
        self.merge_branch(repo, "data", "main", {"research/data.csv": "1,2"}, "2026-11-10T12:00:00Z")
        self.merge_branch(repo, "notes", "main", {"notes/x.md": "x", "docs/a.txt": "a"}, "2026-11-10T13:00:00Z")
        self.git(repo, "checkout", "-qb", "empty")
        self.git(repo, "commit", "-q", "--allow-empty", "-m", "nothing", date="2026-11-02T00:00:00Z")
        self.git(repo, "checkout", "-q", "main")
        self.git(repo, "merge", "-q", "--no-ff", "-m", "Merge empty", "empty", date="2026-11-10T14:00:00Z")
        self.git(repo, "update-ref", "refs/remotes/origin/main", "main")

        merges = self.report(repo)["merges"]
        self.assertEqual(merges["total"], 3)
        self.assertEqual(merges["docsOnly"], 1)

    def test_measures_the_pull_requests_merged_in_the_window_from_gh(self):
        repo = self.repo_with_merges()

        def pull(number, created, merged):
            return {"number": number, "createdAt": created, "mergedAt": merged}

        calls = self.stub_gh(
            json.dumps(
                [
                    pull(1, "2026-11-10T11:00:00Z", "2026-11-10T11:05:30Z"),
                    pull(2, "2026-11-10T10:00:00Z", "2026-11-10T12:00:00Z"),
                    pull(3, "2026-11-11T08:00:00Z", "2026-11-11T08:20:59Z"),
                    pull(4, "2026-11-08T00:00:00Z", "2026-11-09T23:59:59Z"),
                    pull(5, "2026-11-12T00:00:00Z", "2026-11-12T00:45:00Z"),
                    pull(6, "2026-11-12T01:00:00Z", "2026-11-12T01:07:00Z"),
                    pull(7, "2026-11-12T23:00:00Z", "2026-11-13T00:00:00Z"),
                    pull(8, None, "2026-11-12T01:00:00Z"),
                ]
            )
        )
        report = self.report(repo, github=True)
        self.assertEqual(calls(), [GH_LIST])
        self.assertEqual(report["pullRequests"], {"merged": 5, "openToMergeMinutes": {"median": 20, "p90": 120, "max": 120}})
        self.assertIsNone(report["githubError"])

    def test_does_not_call_gh_without_github(self):
        repo = self.repo_with_merges()
        calls = self.stub_gh("[]")
        report = self.report(repo, github=False)
        self.assertEqual(calls(), [])
        self.assertIsNone(report["pullRequests"])
        self.assertIsNone(report["githubError"])

    def test_reports_a_failing_gh_and_still_succeeds(self):
        repo = self.repo_with_merges()
        calls = self.stub_gh(stderr="gh: To get started with GitHub CLI, run gh auth login\n", code=4)
        report = self.report(repo, github=True)
        self.assertEqual(len(calls()), 1)
        self.assertIsNone(report["pullRequests"])
        self.assertEqual(report["githubError"], "gh: To get started with GitHub CLI, run gh auth login")
        self.assertEqual(report["merges"]["total"], 5)

        self.stub_gh(code=3)
        self.assertEqual(self.report(repo, github=True)["githubError"], "gh exited with code 3")
        self.stub_gh("not json")
        self.assertTrue(self.report(repo, github=True)["githubError"].startswith("gh printed no JSON: "))
        self.stub_gh("{}")
        self.assertEqual(self.report(repo, github=True)["githubError"], "gh printed no list of pull requests")

    def test_reports_gh_not_installed(self):
        repo = self.repo_with_merges()
        self.host.environ["SIGNALBOX_GH"] = str(self.tmp / "no-such-gh")
        report = self.report(repo, github=True)
        self.assertIsNone(report["pullRequests"])
        self.assertEqual(report["githubError"], "gh is not installed")
        self.assertIn("Not measured: gh is not installed.", self.report(repo, as_json=False, github=True))

    def test_measures_the_lane_runs_in_the_window_and_the_merges_outside_it(self):
        repo = self.repo_with_merges()
        # Newest first: m6, m5, m4, m3, m2, m1, m0.
        m6, _, m4, m3, m2, m1, _ = self.git(repo, "log", "--first-parent", "--merges", "--format=%H", "main").split()
        self.lane_log(
            [
                lane_run("2026-11-09T23:59:00Z", "merged", m2, gate=90),
                lane_run("2026-11-10T11:58:00Z", "merged", m1, gate=100),
                lane_run("2026-11-11T02:55:00Z", "merged", m3, wait=30, gate=200, attempts=2),
                "not json",
                lane_run("2026-11-11T05:00:00Z", "gate_failed", wait=10, gate=50, failed_step="Lint"),
                lane_run("2026-11-12T09:55:00Z", "merged", m4, wait=5, gate=300, attempts=3),
                "[1]",
                lane_run("2026-11-12T12:00:00Z", "refused", attempts=None),
                lane_run("2026-11-12T13:00:00Z", "gate_failed", wait=20, gate=40, failed_step="Lint"),
                lane_run("2026-11-12T14:00:00Z", "gate_failed", wait=15, gate=10),
                lane_run("2026-11-12T15:00:00Z", "interrupted", wait=None, attempts=None, failed_step="Test"),
                lane_run("2026-11-13T00:00:00Z", "merged", m6, gate=60),
            ]
        )
        report = self.report(repo)
        self.assertEqual(
            report["lane"],
            {
                "runs": 8,
                "byStatus": {"gate_failed": 3, "interrupted": 1, "merged": 3, "refused": 1},
                "waitSeconds": {"median": 10, "p90": 30},
                "gateSeconds": {"median": 200, "p90": 300},
                "maxAttempts": 3,
                "failedSteps": {"Lint": 2},
                "skippedLines": 2,
            },
        )
        self.assertEqual(report["merges"]["outsideLane"], 1)
        self.assertEqual(report["merges"]["total"], 5)

        text = self.report(repo, as_json=False)
        self.assertIn("\n## Merge lane\n", text)
        self.assertIn("| Outside the merge lane | 1 |", text)
        self.assertIn("| Runs | 8 |", text)
        self.assertIn("| Gate of merged runs, median (s) | 200 |", text)
        self.assertIn("| gate_failed | 3 |", text)
        self.assertIn("| Lint | 2 |", text)

    def test_counts_every_merge_outside_the_lane_without_a_lane_log(self):
        repo = self.repo_with_merges()
        report = self.report(repo)
        self.assertEqual(
            report["lane"],
            {
                "runs": 0,
                "byStatus": {},
                "waitSeconds": {"median": None, "p90": None},
                "gateSeconds": {"median": None, "p90": None},
                "maxAttempts": None,
                "failedSteps": {},
                "skippedLines": 0,
            },
        )
        self.assertEqual(report["merges"]["outsideLane"], 5)

    def test_shows_the_live_state_of_every_worktree_on_a_branch(self):
        repo, _, _ = self.repo_with_worktrees()
        live = self.report(repo)["live"]
        self.assertEqual(
            live["worktrees"],
            [
                {"branch": "main", "behind": 0, "changedFiles": 0, "conflictsWithBase": False},
                {"branch": "a", "behind": 1, "changedFiles": 4, "conflictsWithBase": True},
                {"branch": "b", "behind": 0, "changedFiles": 3, "conflictsWithBase": False},
            ],
        )
        self.assertEqual(live["conflicting"], 1)
        self.assertEqual(
            live["sharedFiles"],
            {"count": 2, "topFiles": [{"file": "README.md", "branches": 2}, {"file": "shared.txt", "branches": 2}]},
        )

    def test_prints_every_section_as_markdown_without_json(self):
        repo = self.repo_with_merges()
        text = self.report(repo, as_json=False)
        self.assertTrue(text.startswith("# Flow metrics of shop\n"))
        for heading in ("## Window", "## Merges", "## Pull requests", "## Merge lane", "## Live"):
            self.assertIn("\n%s\n" % heading, text)
        self.assertIn("| Total | 5 |", text)
        self.assertIn("Not measured (--no-github).", text)
        self.assertIn("| Branch | Behind | Changed files | Conflicts with origin/main |", text)
        self.assertIn("| main | 0 | 0 | no |", text)
        with self.assertRaises(ValueError):
            json.loads(text)

    def test_reads_a_date_as_year_month_day(self):
        self.assertEqual(metrics.parse_date("2026-11-10"), datetime.date(2026, 11, 10))
        for bad in ("2026-13-40", "2026-02-30", "2026-1-5", "20261110", "yesterday", ""):
            with self.assertRaises(ValueError) as refused:
                metrics.parse_date(bad)
            self.assertEqual(str(refused.exception), "takes a date as YYYY-MM-DD: %s" % bad)


if __name__ == "__main__":
    unittest.main()
