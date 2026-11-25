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
        self.assertEqual(self.run_cli("merge", "104", "105"), 2, "one pull request of each project")
        self.assertIn("'105' is not <project>#<pr>, such as api#12", self.usage)
        self.assertEqual(calls(), [])

    def test_metrics_in_a_linked_project_and_a_bad_date(self):
        repo = self.base_repo()
        self.link(repo)
        self.assertEqual(self.run_cli("metrics", "--json", "--no-github", "--since", "2026-11-01", folder=repo), 0)
        self.assertEqual(json.loads(self.printed[0])["base"], "origin/main")
        self.assertEqual(self.run_cli("metrics", "--since", "yesterday", folder=repo), 2)
        self.assertIn("YYYY-MM-DD", self.usage)

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

    def link_group(self, *others):
        """shop (in `repo`, with a worktree `a`) and api (with a worktree `x` that changes a
        file), in the group platform, api first; then any other projects, last."""
        repo = self.base_repo()
        self.add_worktree(repo, "wt-a", "a")
        api = self.base_repo("api")
        self.write(self.add_worktree(api, "api-x", "x") / "x.txt", "x")
        shop = config.Project("shop", str(repo), tuple(config.FEATURES), gate="make check")
        projects = (config.Project("api", str(api), ("board",)), shop) + others
        group = config.Group("platform", tuple(project.name for project in projects))
        config.save(config.Config(projects, groups=(group,)), self.environ["SIGNALBOX_CONFIG"])
        return repo

    def test_status_group_prints_the_board_of_each_project_of_the_group_in_its_order(self):
        repo = self.link_group()
        self.assertEqual(self.run_cli("status", "--no-github", folder=repo), 0)
        own = self.printed[0]
        self.assertEqual(self.run_cli("status", "--group", "--no-github", folder=repo), 0)
        [text] = self.printed
        self.assertTrue(text.startswith("# Board of api\n"), text)
        self.assertIn("| x |", text)
        self.assertTrue(text.endswith("\n\n" + own), text)

    def test_status_group_as_json_and_with_the_pull_requests(self):
        repo = self.link_group()
        calls = self.stub_gh("[]")
        self.assertEqual(self.run_cli("status", "--json", folder=repo), 0)
        own = json.loads(self.printed[0])
        self.assertEqual(self.run_cli("status", "--group", "--json", folder=repo), 0)
        found = json.loads(self.printed[0])
        self.assertEqual(list(found), ["group", "projects"])
        self.assertEqual(found["group"], "platform")
        api, shop = found["projects"]
        self.assertEqual(shop, dict({"project": "shop"}, **own))
        self.assertEqual(list(api)[:2], ["project", "base"])
        self.assertEqual([(row["branch"], row["changedFiles"], row["pullRequest"]) for row in api["worktrees"]], [("main", 0, None), ("x", 1, None)])
        self.assertNotIn("mergeLane", api, "api has no merge lane")
        self.assertEqual(len(calls()), 3, "gh lists the pull requests of each repository")

    def test_status_group_names_a_project_it_cannot_read_and_exits_1(self):
        repo = self.link_group(config.Project("gone", str(self.tmp / "gone"), ("board",)))
        self.assertEqual(self.run_cli("status", "--group", "--json", "--no-github", folder=repo), 1)
        gone = json.loads(self.printed[0])["projects"][-1]
        self.assertEqual(gone, {"project": "gone", "error": "not a git repository: %s" % (self.tmp / "gone")})
        self.assertEqual(self.run_cli("status", "--group", "--no-github", folder=repo), 1)
        self.assertTrue(self.printed[0].endswith("# Board of gone\n\nNot read: not a git repository: %s" % (self.tmp / "gone")))

    def test_status_group_outside_a_group_says_how_to_make_one(self):
        repo = self.base_repo()
        self.link(repo)
        self.assertEqual(self.run_cli("status", "--group", folder=repo), 1)
        self.assertEqual(
            self.errors,
            [
                'signalbox: The project "shop" is in no group. Make one in the configuration (%s): '
                '[groups.<name>] with projects = ["shop", "<another project>"], in dependency order.' % self.environ["SIGNALBOX_CONFIG"]
            ],
        )

    def test_brief_names_the_worktrees_of_the_other_repositories_of_its_group(self):
        repo = self.link_group()
        self.assertEqual(self.run_cli("brief", folder=repo), 0)
        self.assertTrue(self.printed[0].endswith("\nOther repositories of platform:\n- api: x"), self.printed)

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
