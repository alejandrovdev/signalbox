"""Tests of the board (`status`) and the session brief (`brief`, `changes`) over temporary
repositories with worktrees, real git and a stub `gh`."""

import fcntl
import json
import os
import unittest

from signalbox import names, repository
from signalbox.features import board, brief
from tests import repos
from tests.repos import lines

MERGE_LINE = "Merge with signalbox merge <number>. While you work run the focused checks; the lane runs the gate (make check)."
FEATURE_DOCS = ("docs/features/*.md",)


class Board(repos.Scratch):
    def open(self, folder, **values):
        values.setdefault("gate", "make check")
        values.setdefault("feature_docs", FEATURE_DOCS)
        return repository.open(repos.project(self.tmp / "repo", **values), self.host, folder)

    def status(self, folder, **options):
        code = board.status(self.open(folder), self.host, self.out, **options)
        self.assertEqual(code, 0)
        return self.text()

    def test_lists_every_worktree_on_a_branch_and_the_merge_lane(self):
        _, a, _ = self.repo_with_worktrees()
        runs = [
            repos.lane_record(1, "2026-11-20T10:00:00Z", "merged"),
            repos.lane_record(2, "2026-11-21T10:00:00Z", "gate_failed"),
            repos.lane_record(3, "2026-11-22T10:00:00Z", "merged"),
            repos.lane_record(4, "2026-11-23T10:00:00Z", "conflict"),
        ]
        self.lane_log(runs[:2] + ["not json"] + runs[2:])
        calls = self.stub_gh(
            json.dumps([{"number": 12, "headRefName": "a"}, {"number": 13, "headRefName": "b"}, {"number": 14, "headRefName": "gone"}])
        )

        report = json.loads(self.status(a, as_json=True))

        self.assertEqual(calls(), ["pr list --state open --json number,headRefName"])
        self.assertEqual(
            report,
            {
                "base": "origin/main",
                "worktrees": [
                    {
                        "branch": "main",
                        "featureDocuments": [],
                        "behind": 0,
                        "ahead": 0,
                        "changedFiles": 0,
                        "conflictsWithBase": False,
                        "hoursSinceLastCommit": 50,
                        "pullRequest": None,
                        "sharedFiles": [],
                    },
                    {
                        "branch": "a",
                        "featureDocuments": ["feature-a"],
                        "behind": 1,
                        "ahead": 1,
                        "changedFiles": 4,
                        "conflictsWithBase": True,
                        "hoursSinceLastCommit": 5,
                        "pullRequest": 12,
                        "sharedFiles": [
                            {"file": "README.md", "branches": ["b"]},
                            {"file": "shared.txt", "branches": ["b"]},
                        ],
                    },
                    {
                        "branch": "b",
                        "featureDocuments": ["feature-b"],
                        "behind": 0,
                        "ahead": 1,
                        "changedFiles": 3,
                        "conflictsWithBase": False,
                        "hoursSinceLastCommit": 30,
                        "pullRequest": 13,
                        "sharedFiles": [
                            {"file": "README.md", "branches": ["a"]},
                            {"file": "shared.txt", "branches": ["a"]},
                        ],
                    },
                ],
                "mergeLane": {"lock": "free", "lastRuns": [runs[3], runs[2], runs[1]]},
            },
        )

    def test_leaves_the_pull_requests_out_with_no_github_or_when_gh_fails(self):
        _, a, _ = self.repo_with_worktrees()
        calls = self.stub_gh("[]")
        report = json.loads(self.status(a, as_json=True, github=False))
        self.assertEqual(calls(), [])
        self.assertEqual([row["branch"] for row in report["worktrees"]], ["main", "a", "b"])
        for row in report["worktrees"]:
            self.assertNotIn("pullRequest", row)
        self.assertEqual(report["mergeLane"], {"lock": "free", "lastRuns": []})

        self.printed = []
        calls = self.stub_gh(stderr="gh: not logged in\n", code=4)
        report = json.loads(self.status(a, as_json=True))
        self.assertEqual(len(calls()), 1)
        for row in report["worktrees"]:
            self.assertNotIn("pullRequest", row)

    def test_leaves_the_pull_requests_out_when_gh_is_not_installed(self):
        _, a, _ = self.repo_with_worktrees()
        self.host.environ["SIGNALBOX_GH"] = str(self.tmp / "no-such-gh")
        report = json.loads(self.status(a, as_json=True))
        for row in report["worktrees"]:
            self.assertNotIn("pullRequest", row)

    def test_reports_the_lane_busy_while_its_lock_is_held(self):
        repo, a, _ = self.repo_with_worktrees()
        with open(repo / ".git" / names.LANE_LOCK, "a") as held:
            fcntl.flock(held, fcntl.LOCK_EX)
            report = json.loads(self.status(a, as_json=True, github=False))
        self.assertEqual(report["mergeLane"]["lock"], "busy")

    def test_reports_the_lane_unknown_when_its_lock_cannot_be_read(self):
        repo, a, _ = self.repo_with_worktrees()
        lock = repo / ".git" / names.LANE_LOCK
        lock.write_text("")
        lock.chmod(0)
        try:
            report = json.loads(self.status(a, as_json=True, github=False))
        finally:
            lock.chmod(0o600)
        self.assertEqual(report["mergeLane"]["lock"], "unknown" if os.getuid() else "free")

    def test_leaves_out_a_locked_worktree_whose_folder_is_gone(self):
        repo, a, _ = self.repo_with_worktrees()
        self.git(repo, "worktree", "lock", str(a))
        self.host.run(["rm", "-rf", str(a)])
        report = json.loads(self.status(repo, as_json=True, github=False))
        self.assertEqual([row["branch"] for row in report["worktrees"]], ["main", "b"])

    def test_prints_markdown_tables_without_json(self):
        _, a, _ = self.repo_with_worktrees()
        text = self.status(a, github=False)
        for heading in ("# Board of shop", "## Worktrees", "## Shared files", "## Merge lane"):
            self.assertIn(heading + "\n", text)
        self.assertIn("| a | feature-a | 1 | 1 | 4 | yes | 5 |", text)
        self.assertIn("| a | README.md | b |", text)
        self.assertIn("Lock: free.", text)
        self.assertNotIn("Pull request", text)

    def test_leaves_the_merge_lane_out_of_a_project_without_it(self):
        _, a, _ = self.repo_with_worktrees()
        code = board.status(self.open(a, features=("board",)), self.host, self.out, as_json=True, github=False)
        self.assertEqual(code, 0)
        self.assertNotIn("mergeLane", json.loads(self.text()))

    def test_refuses_outside_a_git_repository_or_without_the_base_on_origin(self):
        plain = self.tmp / "plain"
        plain.mkdir()
        with self.assertRaises(repository.Refusal) as caught:
            self.open(plain)
        self.assertEqual(str(caught.exception), "not a git repository: %s" % plain)

        repo = self.tmp / "repo"
        repo.mkdir()
        self.git(repo, "init", "-q", "-b", "main")
        self.write(repo / "x.txt", "x")
        self.commit_all(repo, "base")
        with self.assertRaises(repository.Refusal) as caught:
            self.open(repo)
        self.assertEqual(str(caught.exception), "no such ref: origin/main; fetch it with: git fetch origin main")

    def test_the_base_is_the_configured_one_else_origin_head_else_main(self):
        repo = self.base_repo()
        self.git(repo, "update-ref", "refs/remotes/origin/trunk", "main")
        self.assertEqual(self.open(repo, base="trunk").remote_base, "origin/trunk")
        self.assertEqual(self.open(repo).remote_base, "origin/main")
        self.git(repo, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/trunk")
        self.assertEqual(self.open(repo).remote_base, "origin/trunk")


class Brief(repos.Scratch):
    def open(self, folder, **values):
        values.setdefault("gate", "make check")
        values.setdefault("feature_docs", FEATURE_DOCS)
        return repository.open(repos.project(self.tmp / "repo", **values), self.host, folder)

    def brief(self, folder, **values):
        brief.brief(self.open(folder, **values), self.host, self.out)
        text, self.printed = self.text(), []
        return text

    def test_prints_this_worktrees_line_the_other_worktrees_that_change_files_and_how_to_merge(self):
        repo, a, b = self.repo_with_worktrees()
        c = self.add_worktree(repo, "wt-c", "c")
        self.write(c / "c.txt", "c")
        self.add_worktree(repo, "wt-d", "d")
        calls = self.stub_gh("[]")

        self.assertEqual(
            self.brief(a),
            "\n".join(
                [
                    "This worktree: a, 1 behind origin/main, conflicts with origin/main.",
                    "Other worktrees that change files:",
                    "- b; features: feature-b; shares: README.md, shared.txt",
                    "- c",
                    MERGE_LINE,
                    "",
                ]
            ),
        )
        self.assertEqual(calls(), [], "the brief reads git only")
        self.assertEqual(
            self.brief(b).split("\n")[0], "This worktree: b, 0 behind origin/main, no conflict with origin/main."
        )

    def test_leaves_the_merge_line_out_without_the_merge_lane(self):
        repo = self.base_repo()
        self.assertEqual(
            self.brief(repo, features=("board", "session-brief")),
            "This worktree: main, 0 behind origin/main, no conflict with origin/main.\n",
        )

    def test_names_at_most_5_shared_files_then_n_more(self):
        repo = self.base_repo()
        a = self.add_worktree(repo, "wt-a", "a")
        b = self.add_worktree(repo, "wt-b", "b")
        for number in range(1, 8):
            self.write(a / ("f%d.txt" % number), "a")
            self.write(b / ("f%d.txt" % number), "b")
        self.assertEqual(
            self.brief(a),
            "\n".join(
                [
                    "This worktree: a, 0 behind origin/main, no conflict with origin/main.",
                    "Other worktrees that change files:",
                    "- b; shares: f1.txt, f2.txt, f3.txt, f4.txt, f5.txt +2 more",
                    MERGE_LINE,
                    "",
                ]
            ),
        )

    def test_keeps_to_2000_characters(self):
        repo = self.base_repo()
        a = self.add_worktree(repo, "wt-a", "a")
        self.write(a / "mine.txt", "a")
        for number in range(14):
            folder = self.add_worktree(repo, "wt-%d" % number, "long-branch-name-" * 10 + str(number))
            self.write(folder / ("own-%d.txt" % number), "x")
        text = self.brief(a)
        self.assertLessEqual(len(text), 2000)
        shown = text.rstrip("\n").split("\n")
        self.assertEqual(shown[0], "This worktree: a, 0 behind origin/main, no conflict with origin/main.")
        self.assertEqual(shown[1], "Other worktrees that change files:")
        self.assertEqual(shown[-1], MERGE_LINE)
        listed = [line for line in shown if line.startswith("- long-branch-name-")]
        self.assertGreater(len(listed), 0)
        self.assertEqual(shown[-2], "- +%d more worktrees" % (14 - len(listed)))

    def test_a_detached_worktree_says_so(self):
        repo = self.base_repo()
        detached = self.tmp / "detached"
        self.git(repo, "worktree", "add", "-q", "--detach", str(detached))
        self.assertTrue(self.brief(detached).startswith("This worktree: detached HEAD, 0 behind origin/main"))


class Changes(repos.Scratch):
    def open(self, folder):
        return repository.open(repos.project(self.tmp / "repo", gate="make check"), self.host, folder)

    def changes(self, folder):
        brief.changes(self.open(folder), self.host, self.out)
        text, self.printed = self.text(), []
        return text

    def state(self, repo, name):
        return json.loads((repo / ".git" / "worktrees" / name / names.CHANGES_STATE).read_text())

    def test_is_silent_on_the_first_and_an_unchanged_run_then_reports_a_new_conflict_and_a_new_shared_file_once(self):
        repo = self.base_repo()
        a = self.add_worktree(repo, "wt-a", "a")
        self.write(a / "shared.txt", lines({1: "a"}))
        self.commit_all(a, "a")
        self.write(a / "a.txt", "a")
        b = self.add_worktree(repo, "wt-b", "b")
        self.write(b / "b.txt", "b")

        self.assertEqual(self.changes(a), "")
        self.assertFalse((a / names.CHANGES_STATE).exists(), "the state lives in the worktree's git directory")
        self.assertEqual(self.state(repo, "wt-a"), {"version": 1, "conflictsWithBase": False, "shared": {}})
        self.assertEqual(self.changes(a), "")

        self.write(b / "a.txt", "b too")
        self.write(repo / "shared.txt", lines({1: "main"}))
        self.commit_all(repo, "main moves")
        self.git(repo, "update-ref", "refs/remotes/origin/main", "main")

        self.assertEqual(
            self.changes(a), "This branch (a) now conflicts with origin/main.\na.txt is now also changed by b.\n"
        )
        self.assertEqual(self.state(repo, "wt-a"), {"version": 1, "conflictsWithBase": True, "shared": {"a.txt": ["b"]}})
        self.assertEqual(self.changes(a), "")

    def test_prints_at_most_10_lines_then_n_more(self):
        repo = self.base_repo()
        a = self.add_worktree(repo, "wt-a", "a")
        b = self.add_worktree(repo, "wt-b", "b")
        files = ["f%02d.txt" % number for number in range(1, 13)]
        for name in files:
            self.write(a / name, "a")
        self.assertEqual(self.changes(a), "")
        for name in files:
            self.write(b / name, "b")
        self.assertEqual(
            self.changes(a), "".join("%s is now also changed by b.\n" % name for name in files[:10]) + "+2 more\n"
        )


if __name__ == "__main__":
    unittest.main()
