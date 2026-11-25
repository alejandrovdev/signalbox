"""Tests of `setup`, `init`, `deinit`, `doctor` and `uninstall`: the screens each asks, what it
writes. The person is a stand-in that presses keys; the configuration, the Claude Code
settings and the repositories are scratch ones."""

import io
import json
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from signalbox import cli, config, hooks, linking, screens
from signalbox.ui import scene
from signalbox.ui.widgets import BACKSPACE, CTRL_C, DOWN, ENTER, ESC, SPACE, UP, Choice
from tests import repos


def screen_text(sequence):
    """The whole screen a sequence draws at its step, as text."""
    canvas = scene.compose(scene.view_of(sequence, "1.0.0"), 140, 42, 0)
    return "\n".join(canvas.line(row) for row in range(canvas.height))


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

    def group(self, name, *projects):
        """Add a group of the linked projects to the saved configuration."""
        saved = self.saved()
        configuration = config.Config(saved.projects, saved.defaults, saved.groups + (config.Group(name, projects),))
        config.save(configuration, self.file)

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
        self.assertIn("missing  Claude Code hooks: this setup installs them", body)
        self.assertRegex(body, r"ok       git \d+\.\d+")


    def test_the_screens_show_the_default_features_and_that_the_hooks_install(self):
        keys = repos.Keys(ENTER, ENTER, "n")
        self.run_cli("setup", keys=keys)

        features = ", ".join(config.PRESETS["recommended"])
        for title in ("Prerequisites", "Default features", "Summary"):
            self.assertEqual(scene.view_of(keys.at(title)).answers, {"features": features, "hooks": "install"}, title)
        text = screen_text(keys.at("Summary"))
        self.assertRegex(text, r"FEATURES +board, ")
        self.assertRegex(text, r"HOOKS +install")

    def test_with_no_hooks_the_screens_say_the_hooks_are_not_installed(self):
        keys = repos.Keys(ENTER, DOWN, SPACE, ENTER, "n")  # session-brief off
        self.run_cli("setup", "--no-hooks", keys=keys)

        features = ", ".join(name for name in config.FEATURES if name != "session-brief")
        self.assertEqual(scene.view_of(keys.at("Summary")).answers, {"features": features, "hooks": "not installed"})


INSTALL = screens.INSTALL_TITLE


class InstallingWhatIsMissing(Linking):
    """With Homebrew there and git, gh or Claude Code missing, setup warns, offers to install
    them, and installs only after the summary's yes."""

    def setUp(self):
        super().setUp()
        self.brew = str(self.stub("brew", "exit 99"))  # never run: the host records instead
        self.environ["SIGNALBOX_BREW"] = self.brew
        self.environ["SIGNALBOX_CLAUDE"] = str(self.stub("fake-claude", "exit 0"))

    def missing(self, *names):
        for name in names:
            self.environ["SIGNALBOX_" + name.upper()] = str(self.tmp / ("no-" + name))

    def done(self):
        return [
            "Saved the configuration in %s." % self.file,
            "Installed the Claude Code hooks in %s." % self.settings,
            "Next: in a repository's folder, run: signalbox init",
        ]

    def test_it_warns_and_offers_to_install_starting_on_skip(self):
        self.missing("gh", "claude")
        keys = repos.Keys(ENTER, "q")

        self.assertEqual(self.run_cli("setup", keys=keys), 1)

        self.assertEqual(keys.titles(), ["Prerequisites", INSTALL])
        body = keys.last("Prerequisites").body
        self.assertEqual(body[-2:], ("", screens.WARNING))
        self.assertNotIn(screens.NOTHING_INSTALLED, body)
        offer = keys.last(INSTALL)
        self.assertIsInstance(offer, Choice)
        self.assertEqual(
            [(item.value, item.label, item.note) for item in offer.items],
            [
                ("yes", "Install with Homebrew", "brew install gh; brew install --cask claude-code"),
                ("no", "Skip", "install them yourself; `signalbox doctor` lists them"),
            ],
        )
        self.assertEqual((offer.cursor, offer.current), (1, None), "it starts on Skip, marked as nothing saved")
        self.assertEqual(scene.view_of(keys.at(INSTALL)).answers["install"], "nothing")
        text = screen_text(keys.at(INSTALL))
        self.assertIn("Install with Homebrew", text)
        self.assertRegex(text, r"INSTALL +nothing")
        self.assertEqual(self.host.installs, [])
        self.assertEqual(self.printed, ["Nothing was changed."])

    def test_without_homebrew_or_with_nothing_missing_nothing_is_offered(self):
        for gone, brew, last in (("gh", False, screens.GET_HOMEBREW), (None, True, screens.NOTHING_INSTALLED)):
            self.environ["SIGNALBOX_BREW"] = self.brew if brew else str(self.tmp / "no-brew")
            self.environ.pop("SIGNALBOX_GH", None)
            if gone:
                self.missing(gone)
            keys = repos.Keys(ENTER, "q")

            self.run_cli("setup", keys=keys)

            self.assertEqual(keys.titles(), ["Prerequisites", "Default features"])
            body = keys.last("Prerequisites").body
            self.assertEqual(body[-2:], ("", last))
            self.assertNotIn(screens.WARNING, body)
            self.assertNotIn("install", scene.view_of(keys.at("Default features")).answers)

    def test_the_install_is_listed_in_the_summary_and_runs_after_its_yes_before_saving(self):
        self.missing("gh", "claude")
        keys = repos.Keys(ENTER, UP, ENTER, ENTER, "y")

        self.assertEqual(self.run_cli("setup", keys=keys), 0)

        self.assertEqual(keys.titles(), ["Prerequisites", INSTALL, "Default features", "Summary"])
        self.assertEqual(keys.last("Summary").body[-1], "Install with Homebrew: gh, Claude Code")
        self.assertEqual(scene.view_of(keys.at("Summary")).answers["install"], "gh, Claude Code")
        self.assertRegex(screen_text(keys.at("Summary")), r"INSTALL +gh, Claude Code")
        self.assertEqual(
            self.host.installs, [[self.brew, "install", "gh"], [self.brew, "install", "--cask", "claude-code"]]
        )
        self.assertEqual(
            self.printed,
            [
                "Installing gh: brew install gh",
                linking.GH_SIGN_IN,
                "Installing Claude Code: brew install --cask claude-code",
                linking.CLAUDE_SIGN_IN,
            ]
            + self.done(),
        )
        self.assertEqual(self.saved().defaults.features, config.PRESETS["recommended"])
        self.assertEqual(hooks.installed(self.settings), list(hooks.EVENTS))

    def test_skip_installs_nothing_and_the_summary_does_not_list_it(self):
        self.missing("gh")
        keys = repos.Keys(ENTER, ENTER, ENTER, "y")

        self.assertEqual(self.run_cli("setup", keys=keys), 0)

        self.assertFalse([line for line in keys.last("Summary").body if "Homebrew" in line])
        self.assertEqual(scene.view_of(keys.at("Summary")).answers["install"], "nothing")
        self.assertEqual(self.host.installs, [])
        self.assertEqual(self.printed, self.done())

    def test_the_final_no_quitting_or_ctrl_c_after_choosing_to_install_installs_and_writes_nothing(self):
        self.missing("gh")
        for keys in (repos.Keys(ENTER, UP, ENTER, ENTER, "n"), repos.Keys(ENTER, UP, ENTER, "q"), repos.Keys(ENTER, UP, CTRL_C)):
            self.assertEqual(self.run_cli("setup", keys=keys), 1)

            self.assertEqual(self.host.installs, [])
            self.assertEqual(self.printed, ["Nothing was changed."])
            self.assertFalse(self.file.exists())
            self.assertFalse(self.settings.exists())

    def test_going_back_from_install_to_skip_installs_nothing(self):
        self.missing("gh")
        keys = repos.Keys(ENTER, UP, ENTER, ESC, DOWN, ENTER, ENTER, "y")

        self.assertEqual(self.run_cli("setup", keys=keys), 0)

        self.assertEqual(self.host.installs, [])
        self.assertFalse(any("Homebrew" in line for line in keys.last("Summary").body))

    def test_yes_alone_installs_nothing(self):
        self.missing("gh", "claude")

        self.assertEqual(self.run_cli("setup", "--yes"), 0)

        self.assertEqual(self.keys.asked, 0)
        self.assertEqual(self.host.installs, [])
        self.assertEqual(self.printed, self.done())

    def test_install_missing_with_yes_installs_reports_a_failure_goes_on_saves_and_exits_one(self):
        self.missing("gh", "claude")
        self.host.install_codes[self.brew + " install gh"] = 1

        self.assertEqual(self.run_cli("setup", "--install-missing", "--yes"), 1)

        self.assertEqual(self.keys.asked, 0)
        self.assertEqual(
            self.host.installs, [[self.brew, "install", "gh"], [self.brew, "install", "--cask", "claude-code"]]
        )
        self.assertEqual(
            self.printed,
            [
                "Installing gh: brew install gh",
                "Could not install gh: brew exited 1.",
                "Installing Claude Code: brew install --cask claude-code",
                linking.CLAUDE_SIGN_IN,
            ]
            + self.done(),
        )
        self.assertEqual(self.saved().defaults.features, config.PRESETS["recommended"])
        self.assertEqual(hooks.installed(self.settings), list(hooks.EVENTS))

    def test_a_failed_claude_code_install_does_not_say_to_sign_in(self):
        self.missing("claude")
        self.host.install_codes[self.brew + " install --cask claude-code"] = 127

        self.assertEqual(self.run_cli("setup", "--install-missing", "--yes"), 1)

        self.assertEqual(
            self.printed[:2],
            ["Installing Claude Code: brew install --cask claude-code", "Could not install Claude Code: brew exited 127."],
        )
        self.assertNotIn(linking.CLAUDE_SIGN_IN, self.printed)

    def test_an_old_git_is_installed_with_homebrew(self):
        self.environ["SIGNALBOX_GIT"] = str(self.stub("old-git", "echo git version 2.30.1"))

        self.assertEqual(self.run_cli("setup", "--install-missing", "--yes"), 0)

        self.assertEqual(self.host.installs, [[self.brew, "install", "git"]])
        self.assertEqual(self.printed, ["Installing git: brew install git"] + self.done())

    def test_the_install_missing_flag_answers_the_screen_and_the_summary_still_asks(self):
        self.missing("gh")
        keys = repos.Keys(ENTER, ENTER, "n")

        self.assertEqual(self.run_cli("setup", "--install-missing", keys=keys), 1)

        self.assertNotIn(INSTALL, keys.titles())
        self.assertEqual(keys.last("Summary").body[-1], "Install with Homebrew: gh")
        self.assertEqual(self.host.installs, [])
        self.assertEqual(self.printed, ["Nothing was changed."])

    def test_install_missing_without_homebrew_says_why_nothing_was_installed_and_exits_one(self):
        self.environ["SIGNALBOX_BREW"] = str(self.tmp / "no-brew")
        self.missing("gh")

        self.assertEqual(self.run_cli("setup", "--install-missing", "--yes"), 1)

        self.assertEqual(self.host.installs, [])
        self.assertEqual(self.printed, [linking.NO_HOMEBREW % "gh"] + self.done())

    def test_install_missing_with_nothing_missing_says_so(self):
        self.assertEqual(self.run_cli("setup", "--install-missing", "--yes"), 0)

        self.assertEqual(self.host.installs, [])
        self.assertEqual(self.printed, [linking.NOTHING_MISSING] + self.done())

    def test_a_gh_not_signed_in_is_never_installed_and_setup_says_to_sign_in(self):
        self.stub_gh(code=1)

        self.assertEqual(self.run_cli("setup", "--install-missing", "--yes"), 0)

        self.assertEqual(self.host.installs, [])
        self.assertEqual(self.printed, [linking.NOTHING_MISSING, linking.GH_SIGN_IN] + self.done())
        self.assertNotIn("auth login", " ".join(" ".join(words) for words in self.host.installs))

    def test_the_plain_questions_offer_the_install_too(self):
        self.missing("gh")
        asked, said = iter(["", "1", "", "y"]), []
        from signalbox import ui

        console = ui.Console(read=lambda prompt="": said.append(prompt) or next(asked), write=said.append, tty=False)
        self.keys = console
        self.printed = []
        code = cli.main(["setup"], host=self.host, out=self.out, err=self.errors.append, asker=console, folder=self.tmp)

        self.assertEqual(code, 0)
        self.assertIn(INSTALL, "\n".join(str(line) for line in said))
        self.assertEqual(self.host.installs, [[self.brew, "install", "gh"]])


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

    def test_the_screens_show_the_project_its_base_its_features_and_its_gate(self):
        self.write(self.repo / "Makefile", "check:\n\ttrue\n")
        keys = repos.Keys(ENTER, ENTER, ENTER, "n")
        self.run_cli("init", "--base", "trunk", keys=keys, folder=self.repo)

        features = ", ".join(config.PRESETS["recommended"])
        shown = {"project": "shop", "base": "trunk", "features": features, "gate": "make check"}
        for title in ("Project name", "Features", "Gate", "Summary"):
            self.assertEqual(scene.view_of(keys.at(title)).answers, shown, title)
        text = screen_text(keys.at("Summary"))
        for label, value in (("PROJECT", "shop"), ("BASE", "trunk"), ("GATE", "make check")):
            self.assertRegex(text, r"%s +%s" % (label, value))
        self.assertFalse(self.file.exists())

    def test_without_the_merge_lane_the_screens_show_no_gate(self):
        keys = repos.Keys(ENTER, "n")
        self.run_cli("init", "--preset", "minimal", keys=keys, folder=self.repo)

        self.assertEqual(scene.view_of(keys.at("Summary")).answers, {"project": "shop", "base": "main", "features": "board, sweep"})
        self.assertNotIn("GATE", screen_text(keys.at("Summary")))

    def test_the_panel_about_a_feature_says_what_it_needs_what_needs_it_and_its_tools(self):
        keys = repos.Keys(ENTER, DOWN, DOWN, "q")  # the cursor on merge-lane
        self.run_cli("init", keys=keys, folder=self.repo)

        text = screen_text(keys.at("Features"))
        about = text[text.index("╭─ ◆ merge-lane") :]
        self.assertIn(config.FEATURES["merge-lane"].summary[:40], about)
        self.assertRegex(about, r"NEEDS +nothing else")
        self.assertRegex(about, r"NEEDED BY +merge-guard")
        self.assertRegex(about, r"TOOLS +git, gh")
        self.assertFalse(self.file.exists())

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

    def test_renaming_a_project_renames_it_in_its_group(self):
        self.link(self.base_repo("api"), name="api", features=("board",))
        self.link(self.repo, features=("board",))
        self.group("platform", "api", "shop")

        self.assertEqual(self.run_cli("init", "--name", "store", "--yes", folder=self.repo), 0)

        self.assertEqual(self.saved().groups, (config.Group("platform", ("api", "store")),))
        self.assertIn('Renamed "shop" to "store".', self.printed)

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

    def test_a_removed_project_leaves_its_group_and_says_so(self):
        for name in ("api", "web", "shop"):
            self.link(self.base_repo(name), name=name, features=("board",))
        self.group("platform", "api", "shop", "web")

        self.assertEqual(self.run_cli("deinit", folder=self.tmp / "shop"), 0)

        self.assertEqual(self.saved().groups, (config.Group("platform", ("api", "web")),))
        self.assertIn('Removed "shop" from the group "platform", which keeps: api, web.', self.printed)

    def test_a_group_left_with_one_project_goes_and_says_so(self):
        for name in ("api", "shop", "blog"):
            self.link(self.base_repo(name), name=name, features=("board",))
        self.group("platform", "api", "shop")

        self.assertEqual(self.run_cli("deinit", folder=self.tmp / "shop"), 0)

        self.assertEqual(self.saved().groups, ())
        self.assertEqual([project.name for project in self.saved().projects], ["api", "blog"])
        self.assertIn('Removed the group "platform": only "api" was left in it.', self.printed)
        self.assertEqual(self.printed[0], 'Unlinked "shop" (%s) from %s.' % (self.tmp / "shop", self.file))

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

    def shared_hooks(self, repo):
        """`init --shared-hooks`, with a hook of the team's own beside ours; the settings file."""
        shared = repo / ".claude" / "settings.json"
        mine = {"type": "command", "command": "echo mine"}
        self.write(shared, json.dumps({"hooks": {"Stop": [{"hooks": [mine]}]}}))
        self.assertEqual(self.run_cli("init", "--preset", "minimal", "--yes", "--shared-hooks", folder=repo), 0)
        self.assertEqual(hooks.installed(shared), list(hooks.EVENTS))
        return shared, {"hooks": {"Stop": [{"hooks": [mine]}]}}

    def test_with_shared_hooks_removes_the_hooks_init_wrote_in_the_repository(self):
        repo = self.base_repo("shop")
        shared, theirs = self.shared_hooks(repo)
        self.assertEqual(self.run_cli("deinit", "--shared-hooks", folder=repo), 0)
        self.assertEqual(self.saved().projects, ())
        self.assertIn("Removed the shared hooks from %s: commit that file." % shared, self.printed)
        self.assertEqual(json.loads(shared.read_text()), theirs)

    def test_without_the_flag_says_the_shared_hooks_stay_and_how_to_remove_them(self):
        repo = self.base_repo("shop")
        shared, _ = self.shared_hooks(repo)
        self.assertEqual(self.run_cli("deinit", folder=repo), 0)
        self.assertEqual(hooks.installed(shared), list(hooks.EVENTS))
        self.assertIn("The shared hooks stay in %s: remove them with: signalbox deinit %s --shared-hooks" % (shared, repo), self.printed)
        # The command it names works on the repository it just unlinked.
        self.assertEqual(self.run_cli("deinit", str(repo), "--shared-hooks"), 0)
        self.assertEqual(self.printed, ["Removed the shared hooks from %s: commit that file." % shared])
        self.assertEqual(hooks.installed(shared), [])

    def test_says_nothing_of_shared_hooks_a_repository_does_not_have(self):
        repo = self.base_repo("shop")
        self.link(repo)
        self.assertEqual(self.run_cli("deinit", folder=repo), 0)
        self.assertFalse(any("shared hooks" in line for line in self.printed), self.printed)
        self.link(repo)
        self.assertEqual(self.run_cli("deinit", "--shared-hooks", folder=repo), 0)
        self.assertIn("No shared hook was in %s." % (repo / ".claude" / "settings.json"), self.printed)

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
        self.write(Path(self.environ["SIGNALBOX_SESSIONS_DIR"]) / "s1.json", "{}\n")

        self.assertEqual(self.run_cli("uninstall"), 0)
        self.assertEqual(json.loads(self.settings.read_text()), {"model": "opus"})
        self.assertTrue(self.file.exists())
        self.assertTrue((self.tmp / "logs").exists())
        self.assertIn("Removed the Claude Code hooks from %s." % self.settings, self.printed)

        self.assertEqual(self.run_cli("uninstall", "--purge"), 0)
        self.assertFalse(self.file.exists())
        self.assertFalse((self.tmp / "logs").exists())
        self.assertFalse(Path(self.environ["SIGNALBOX_SESSIONS_DIR"]).exists(), "the sessions' records go too")

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
