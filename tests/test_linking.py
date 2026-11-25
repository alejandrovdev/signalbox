"""Tests of `setup`, `init`, `deinit`, `doctor` and `uninstall`: the screens each asks, what it
writes. The person is a stand-in that presses keys; the configuration, the Claude Code
settings and the repositories are scratch ones."""

import io
import json
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from signalbox import cli, config, hooks
from signalbox.ui.widgets import BACKSPACE, DOWN, ENTER, SPACE
from tests import repos


class Linking(repos.Scratch):
    def setUp(self):
        super().setUp()
        self.file = Path(self.environ["SIGNALBOX_CONFIG"])
        self.settings = Path(self.environ["SIGNALBOX_CLAUDE_SETTINGS"])
        self.errors = []
        self.stub_gh()

    def run_cli(self, *words, keys=None, folder=None):
        self.keys = keys if keys is not None else repos.Keys(terminal=False)
        self.printed, self.errors = [], []
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = cli.main(list(words), host=self.host, out=self.out, err=self.errors.append, asker=self.keys, folder=folder or self.tmp)
        self.usage = stderr.getvalue()
        return code

    def saved(self):
        return config.load(self.file)

    def link(self, folder, name="shop", features=None, **values):
        values.setdefault("gate", "make check")
        project = config.Project(name, str(folder), tuple(config.FEATURES) if features is None else features, **values)
        configuration = config.Config(self.saved().projects + (project,))
        config.save(configuration, self.file)
        return project


class Setup(Linking):
    def test_asks_the_prerequisites_the_default_features_and_a_summary_then_installs_the_hooks(self):
        keys = repos.Keys(ENTER, ENTER, "y")
        self.assertEqual(self.run_cli("setup", keys=keys), 0)
        self.assertEqual(keys.titles(), ["Prerequisites", "Default features", "Summary"])
        self.assertEqual(self.saved(), config.Config((), config.Defaults(config.PRESETS["recommended"])))
        self.assertEqual(hooks.installed(self.settings), list(hooks.EVENTS))
        self.assertIn("Installed the Claude Code hooks in %s." % self.settings, self.printed)
        self.assertEqual(self.printed[-1], "Next: in a repository's folder, run: signalbox init")

    def test_the_default_features_are_chosen_with_their_needs(self):
        # Turn off everything but board and sweep: session-brief, merge-lane (merge-guard goes with it), metrics.
        keys = repos.Keys(ENTER, DOWN, SPACE, DOWN, SPACE, DOWN, DOWN, DOWN, SPACE, ENTER, "y")
        self.assertEqual(self.run_cli("setup", keys=keys), 0)
        self.assertEqual(self.saved().defaults.features, ("board", "sweep"))

    def test_says_no_and_nothing_changes(self):
        self.assertEqual(self.run_cli("setup", keys=repos.Keys(ENTER, ENTER, "n")), 1)
        self.assertFalse(self.file.exists())
        self.assertFalse(self.settings.exists())
        self.assertEqual(self.printed, ["Nothing was changed."])

    def test_with_flags_asks_nothing_and_no_hooks_installs_none(self):
        self.assertEqual(self.run_cli("setup", "--preset", "minimal", "--yes", "--no-hooks"), 0)
        self.assertEqual(self.saved().defaults.features, ("board", "sweep"))
        self.assertFalse(self.settings.exists())

    def test_without_a_terminal_names_the_flag_that_answers(self):
        self.assertEqual(self.run_cli("setup"), 2)
        self.assertIn('nothing answered "Prerequisites": give --yes', self.usage)

    def test_the_prerequisites_screen_says_what_is_missing(self):
        self.environ["SIGNALBOX_GH"] = str(self.tmp / "no-gh")
        keys = repos.Keys("q")
        self.run_cli("setup", keys=keys)
        body = "\n".join(keys.last("Prerequisites").body)
        self.assertIn("missing  gh: install it with: brew install gh", body)
        self.assertRegex(body, r"ok       git \d+\.\d+")


class Init(Linking):
    def setUp(self):
        super().setUp()
        self.repo = self.base_repo("shop")

    def test_links_the_repository_with_the_defaults_and_a_gate_it_proposes(self):
        self.write(self.repo / "Makefile", "check:\n\ttrue\n")
        config.save(config.Config((), config.Defaults(config.PRESETS["recommended"])), self.file)
        keys = repos.Keys(ENTER, ENTER, ENTER, "y")

        self.assertEqual(self.run_cli("init", keys=keys, folder=self.repo), 0)

        self.assertEqual(keys.titles(), ["Project name", "Features", "Gate", "Summary"])
        self.assertEqual(keys.last("Gate").value, "make check")
        [project] = self.saved().projects
        self.assertEqual((project.name, project.path, project.features, project.gate), ("shop", str(self.repo), tuple(config.FEATURES), "make check"))
        self.assertIn('Linked "shop" (%s) in %s.' % (self.repo, self.file), self.printed)
        self.assertIn("The Claude Code hooks are not installed: run signalbox setup.", self.printed)

    def test_proposes_the_gate_from_what_the_repository_has(self):
        from signalbox import linking

        cases = [
            ({"package.json": json.dumps({"scripts": {"verify": "x"}}), "pnpm-lock.yaml": ""}, "pnpm verify"),
            ({"package.json": json.dumps({"scripts": {"check": "x"}})}, "npm run check"),
            ({"package.json": json.dumps({"scripts": {"check": "x"}}), "yarn.lock": ""}, "yarn check"),
            ({"Makefile": "lint:\n\ttrue\nverify: lint\n\ttrue\n"}, "make verify"),
            ({"scripts/verify.sh": "true"}, "bash scripts/verify.sh"),
            ({"README.md": "nothing to run"}, None),
        ]
        for number, (files, gate) in enumerate(cases):
            folder = self.tmp / ("case-%d" % number)
            for name, text in files.items():
                self.write(folder / name, text)
            self.assertEqual(linking.proposed_gate(folder), gate, files)

    def test_a_link_from_a_worktree_links_the_main_checkout(self):
        worktree = self.add_worktree(self.repo, "wt-a", "a")
        self.assertEqual(self.run_cli("init", "--preset", "minimal", "--yes", folder=worktree), 0)
        self.assertEqual(self.saved().projects[0].path, str(self.repo))

    def test_without_the_merge_lane_no_gate_is_asked(self):
        keys = repos.Keys(ENTER, ENTER, "y")
        self.assertEqual(self.run_cli("init", "--preset", "minimal", keys=keys, folder=self.repo), 0)
        self.assertEqual(keys.titles(), ["Project name", "Summary"])
        self.assertIsNone(self.saved().projects[0].gate)

    def test_with_yes_and_no_gate_found_names_the_flag(self):
        self.assertEqual(self.run_cli("init", "--yes", folder=self.repo), 2)
        self.assertIn('nothing answered "Gate": give --gate', self.usage)
        self.assertFalse(self.file.exists())

    def test_flags_answer_every_screen(self):
        words = ["init", "--name", "web", "--feature", "board", "--feature", "merge-lane", "--gate", "make test", "--base", "trunk", "--yes"]
        self.assertEqual(self.run_cli(*words, folder=self.repo), 0)
        [project] = self.saved().projects
        self.assertEqual((project.name, project.features, project.gate, project.base), ("web", ("board", "merge-lane"), "make test", "trunk"))

    def test_a_feature_without_what_it_needs_is_a_usage_error(self):
        self.assertEqual(self.run_cli("init", "--feature", "merge-guard", "--yes", folder=self.repo), 2)
        self.assertIn('"merge-guard" needs "merge-lane"', self.usage)

    def test_a_linked_repository_starts_from_its_settings_and_keeps_its_other_keys(self):
        self.link(self.repo, features=("board", "sweep"), feature_docs=("docs/*.md",), gate=None)
        keys = repos.Keys(ENTER, ENTER, "y")
        self.assertEqual(self.run_cli("init", keys=keys, folder=self.repo), 0)
        self.assertEqual(keys.last("Features").checked, frozenset({"board", "sweep"}))
        self.assertEqual(keys.last("Summary").question, "Replace its settings?")
        [project] = self.saved().projects
        self.assertEqual(project.feature_docs, ("docs/*.md",))

    def test_suggests_a_free_name_and_refuses_a_taken_one_on_the_screen(self):
        self.link(self.base_repo("other"), name="shop", features=("board",))
        keys = repos.Keys(BACKSPACE, BACKSPACE, ENTER, *([BACKSPACE] * 4), "w", "e", "b", ENTER, "y")
        self.assertEqual(self.run_cli("init", "--preset", "minimal", keys=keys, folder=self.repo), 0)
        fields = [widget for widget in keys.shown if widget.title == "Project name"]
        self.assertEqual(fields[0].value, "shop-2", "the folder's name, numbered when taken")
        self.assertIn('the project "shop" already has this name', [widget.error for widget in fields])
        self.assertEqual([project.name for project in self.saved().projects], ["shop", "web"])

    def test_refuses_a_folder_that_is_in_no_repository(self):
        plain = self.tmp / "plain"
        plain.mkdir()
        self.assertEqual(self.run_cli("init", "--yes", folder=plain), 1)
        self.assertEqual(self.printed, ["%s is not in a git repository: run git init in it first. Nothing was changed." % plain])

    def test_shared_hooks_go_in_the_repositorys_settings(self):
        self.assertEqual(self.run_cli("init", "--preset", "minimal", "--yes", "--shared-hooks", folder=self.repo), 0)
        shared = self.repo / ".claude" / "settings.json"
        self.assertEqual(hooks.installed(shared), list(hooks.EVENTS))
        commands = [hook["command"] for groups in json.loads(shared.read_text())["hooks"].values() for group in groups for hook in group["hooks"]]
        self.assertTrue(all(command.startswith("signalbox hook ") and command.endswith(" --shared") for command in commands), commands)


class Deinit(Linking):
    def test_unlinks_the_project_of_the_folder_and_says_what_it_left(self):
        repo = self.base_repo("shop")
        self.link(repo)
        self.link(self.base_repo("blog"), name="blog", features=("board",))
        worktree = self.add_worktree(repo, "wt-a", "a")

        self.assertEqual(self.run_cli("deinit", folder=worktree), 0)

        self.assertEqual([project.name for project in self.saved().projects], ["blog"])
        self.assertEqual(self.printed[0], 'Unlinked "shop" (%s) from %s.' % (repo, self.file))
        self.assertTrue(any(line.startswith("Left as they are:") for line in self.printed))

    def test_removes_the_merge_worktree_it_made(self):
        repo = self.base_repo("shop")
        self.link(repo)
        merge = self.tmp / "cache" / "shop" / "merge.noindex"
        merge.parent.mkdir(parents=True)
        self.git(repo, "worktree", "add", "--quiet", "--detach", str(merge))
        self.assertEqual(self.run_cli("deinit", folder=repo), 0)
        self.assertFalse(merge.exists())
        self.assertNotIn("merge.noindex", self.git(repo, "worktree", "list"))

    def test_unlinks_a_project_whose_folder_is_gone(self):
        self.link(self.tmp / "gone")
        self.assertEqual(self.run_cli("deinit", str(self.tmp / "gone")), 0)
        self.assertEqual(self.saved().projects, ())

    def test_a_folder_of_no_project_says_which_are_linked(self):
        self.link(self.base_repo("shop"))
        other = self.base_repo("other")
        self.assertEqual(self.run_cli("deinit", folder=other), 1)
        self.assertEqual(self.printed, ["No project is linked to %s. The linked projects: shop (%s)" % (other, self.tmp / "shop")])


class Doctor(Linking):
    def test_checks_the_prerequisites_and_each_project(self):
        hooks.install(self.settings, "signalbox")
        self.stub("claude", "exit 0")
        repo = self.base_repo("shop")
        self.git(repo, "remote", "add", "origin", "git@github.com:someone/shop.git")
        self.link(repo)
        self.link(self.tmp / "gone", name="gone", features=("board",))

        self.assertEqual(self.run_cli("doctor"), 1, "a missing folder needs attention")

        text = "\n".join(self.printed)
        self.assertIn("Prerequisites", text)
        self.assertIn("ok       gh, signed in", text)
        self.assertIn("ok       Claude Code hooks in %s" % self.settings, text)
        self.assertIn("shop\n  ok       folder %s" % repo, text)
        self.assertIn("  ok       base branch origin/main", text)
        self.assertIn("  ok       gate: make check", text)
        self.assertIn("  ok       origin is on GitHub", text)
        self.assertIn("gone\n  missing  folder %s: create it, or correct `path` of the project in the configuration" % (self.tmp / "gone"), text)

    def test_says_when_the_hooks_are_missing_and_gh_is_not_signed_in(self):
        self.stub_gh(code=1)
        self.link(self.base_repo("shop"), features=("board", "session-brief"))
        self.assertEqual(self.run_cli("doctor"), 1)
        text = "\n".join(self.printed)
        self.assertIn("problem  gh: not signed in: sign in with: gh auth login", text)
        self.assertIn("missing  Claude Code hooks: install them with: signalbox setup", text)

    def test_a_tool_no_project_needs_does_not_fail_it(self):
        self.environ["SIGNALBOX_GH"] = str(self.tmp / "no-gh")
        self.link(self.base_repo("shop"), features=("board", "sweep"))
        self.assertEqual(self.run_cli("doctor"), 0, "\n".join(self.printed))
        self.assertIn("missing  gh: install it with: brew install gh (no project needs it)", "\n".join(self.printed))

    def test_with_no_project_says_how_to_link_one(self):
        hooks.install(self.settings, "signalbox")
        self.run_cli("doctor")
        self.assertEqual(self.printed[-1], "No project is linked. Link one from its folder with: signalbox init")


class Uninstall(Linking):
    def test_removes_the_hooks_and_keeps_everything_else_until_purged(self):
        self.write(self.settings, json.dumps({"model": "opus"}))
        hooks.install(self.settings, "signalbox")
        repo = self.base_repo("shop")
        self.link(repo)
        self.write(self.tmp / "logs" / "shop" / "lane.jsonl", "{}\n")

        self.assertEqual(self.run_cli("uninstall"), 0)
        self.assertEqual(json.loads(self.settings.read_text()), {"model": "opus"})
        self.assertTrue(self.file.exists())
        self.assertTrue((self.tmp / "logs").exists())
        self.assertIn("Removed the Claude Code hooks from %s." % self.settings, self.printed)

        self.assertEqual(self.run_cli("uninstall", "--purge"), 0)
        self.assertFalse(self.file.exists())
        self.assertFalse((self.tmp / "logs").exists())

    def test_purge_refuses_while_a_lane_runs(self):
        from signalbox.atoms import lock

        repo = self.base_repo("shop")
        self.link(repo)
        held = lock.take(repo / ".git" / "signalbox-lane.lock")
        try:
            self.assertEqual(self.run_cli("uninstall", "--purge"), 1)
        finally:
            held.close()
        self.assertEqual(self.printed[-1], 'A merge lane runs in "shop": run this again once it ends. Nothing was changed.')
        self.assertTrue(self.file.exists())


if __name__ == "__main__":
    unittest.main()
