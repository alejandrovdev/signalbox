"""Tests of the command line: usage errors, commands run outside a linked project, a project
without the feature, the hooks' silence, and the background sweep (a real detached process)."""

import io
import json
import subprocess
import sys
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from signalbox import cli, config
from tests import repos

ROOT = Path(__file__).resolve().parent.parent


class CommandLine(repos.Scratch):
    def setUp(self):
        super().setUp()
        self.errors = []

    def link(self, folder, features=None, **values):
        values.setdefault("gate", "make check")
        project = config.Project("shop", str(folder), tuple(config.FEATURES) if features is None else features, **values)
        config.save(config.Config((project,)), self.environ["SIGNALBOX_CONFIG"])

    def run_cli(self, *words, folder=None):
        self.printed, self.errors = [], []
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = cli.main(list(words), host=self.host, out=self.out, err=self.errors.append, folder=folder or self.tmp)
        self.usage = stderr.getvalue() + stdout.getvalue()
        return code

    def test_prints_its_version(self):
        self.assertEqual(self.run_cli("--version"), 0)
        self.assertRegex(self.printed[0], r"^signalbox \d+\.\d+\.\d+$")

    def test_a_missing_or_unknown_command_is_a_usage_error(self):
        self.assertEqual(self.run_cli(), 2)
        self.assertIn("name a command", self.usage)
        self.assertEqual(self.run_cli("bogus"), 2)
        self.assertEqual(self.run_cli("status", "--bogus"), 2)

    def test_merge_usage_errors(self):
        calls = self.stub_gh()
        for words in ([], ["abc"], ["0104"], ["--bogus"]):
            with self.subTest(words=words):
                self.assertEqual(self.run_cli("merge", *words), 2)
                self.assertIn("usage: signalbox merge", self.usage)
        self.assertEqual(self.run_cli("merge", "104", "105"), 2, "one pull request at a time")
        self.assertIn("unrecognized arguments: 105", self.usage)
        self.assertEqual(calls(), [])

    def test_a_command_outside_a_linked_project_says_how_to_link_it(self):
        repo = self.base_repo()
        self.assertEqual(self.run_cli("status", folder=repo), 1)
        self.assertEqual(self.errors, ["signalbox: %s is in no linked project. Link its repository with: signalbox init" % repo])
        plain = self.tmp / "plain"
        plain.mkdir()
        self.assertEqual(self.run_cli("sweep", folder=plain), 1)
        self.assertIn("is in no linked project", self.errors[0])

    def test_a_command_whose_feature_is_off_says_so(self):
        repo = self.base_repo()
        self.link(repo, features=("board",))
        self.assertEqual(self.run_cli("merge", "4", folder=repo), 1)
        self.assertEqual(
            self.errors, ['signalbox: The project "shop" does not have the merge-lane feature. Turn it on with: signalbox init %s' % repo]
        )

    def test_status_in_any_worktree_of_a_linked_project(self):
        repo = self.base_repo()
        worktree = self.add_worktree(repo, "wt-a", "a")
        self.link(repo)
        self.assertEqual(self.run_cli("status", "--json", "--no-github", folder=worktree), 0)
        self.assertEqual([row["branch"] for row in json.loads(self.printed[0])["worktrees"]], ["main", "a"])

    def test_a_configuration_that_does_not_parse_is_reported_by_key(self):
        repo = self.base_repo()
        Path(self.environ["SIGNALBOX_CONFIG"]).parent.mkdir(parents=True)
        Path(self.environ["SIGNALBOX_CONFIG"]).write_text('[projects.shop]\npath = "/s"\npreset = "all"\n')
        self.assertEqual(self.run_cli("status", folder=repo), 1)
        self.assertEqual(
            self.errors, ['%s: projects.shop.preset: unknown preset "all"; one of: minimal, recommended' % self.environ["SIGNALBOX_CONFIG"]]
        )

    def test_brief_and_changes_print_nothing_and_exit_0_outside_a_repository_or_a_project(self):
        plain = self.tmp / "plain"
        plain.mkdir()
        repo = self.base_repo()
        for folder in (plain, repo):
            for command in ("brief", "changes"):
                self.assertEqual(self.run_cli(command, folder=folder), 0)
                self.assertEqual((self.printed, self.errors), ([], []))

    def test_brief_in_a_linked_project(self):
        repo = self.base_repo()
        self.link(repo)
        self.assertEqual(self.run_cli("brief", folder=repo), 0)
        self.assertTrue(self.printed[0].startswith("This worktree: main"))


class Background(repos.Scratch):
    """`sweep --background`, as a real detached process."""

    def merged_worktree(self, repo):
        folder = self.add_worktree(repo, "wt-done", "feat/done")
        self.write(folder / "done.txt", "done")
        self.commit_all(folder, "done", repos.hours_ago(2))
        self.git(repo, "merge", "-q", "--no-ff", "-m", "Merge", "feat/done", date=repos.hours_ago(-1))
        self.git(repo, "update-ref", "refs/remotes/origin/main", "main")
        return folder

    def signalbox(self, folder, *words):
        project = config.Project("shop", str(folder), ("board", "sweep"))
        config.save(config.Config((project,)), self.environ["SIGNALBOX_CONFIG"])
        environ = dict(self.environ, PYTHONPATH=str(ROOT))
        return subprocess.run(
            [sys.executable, "-m", "signalbox", *words], cwd=folder, env=environ, capture_output=True, text=True, timeout=60
        )

    def test_returns_at_once_prints_nothing_and_the_sweep_still_happens(self):
        repo = self.base_repo()
        done = self.merged_worktree(repo)
        ran = self.signalbox(repo, "sweep", "--background")
        self.assertEqual((ran.returncode, ran.stdout, ran.stderr), (0, "", ""))
        deadline = time.time() + 30
        while done.exists() or "feat/done" in self.git(repo, "branch"):
            self.assertLess(time.time(), deadline, "the background sweep removed nothing")
            time.sleep(0.1)

    def test_with_dry_run_changes_nothing(self):
        repo = self.base_repo()
        done = self.merged_worktree(repo)
        ran = self.signalbox(repo, "sweep", "--dry-run", "--background")
        self.assertEqual((ran.returncode, ran.stdout, ran.stderr), (0, "", ""))
        until = time.time() + 3
        while time.time() < until:
            self.assertTrue(done.exists())
            time.sleep(0.1)
        self.assertIn("feat/done", self.git(repo, "branch"))


if __name__ == "__main__":
    unittest.main()
