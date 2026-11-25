"""Tests of the Claude Code hooks: installing and removing the user-level ones, and what
`signalbox hook <event>` does in and outside a linked project."""

import io
import json
import unittest

from signalbox import cli, config, hooks
from tests import repos

OTHER = {"type": "command", "command": "echo mine", "timeout": 5}


class Install(repos.Scratch):
    def setUp(self):
        super().setUp()
        self.settings = self.home / ".claude" / "settings.json"

    def read(self):
        return json.loads(self.settings.read_text())

    def test_installs_one_hook_per_event_in_a_file_that_was_not_there(self):
        self.assertEqual(hooks.install(self.settings, "/bin/signalbox"), list(hooks.EVENTS))
        settings = self.read()
        self.assertEqual(
            settings["hooks"],
            {
                "SessionStart": [{"hooks": [{"type": "command", "command": "/bin/signalbox hook session-start", "timeout": 15}]}],
                "UserPromptSubmit": [
                    {"hooks": [{"type": "command", "command": "/bin/signalbox hook user-prompt-submit", "timeout": 15}]}
                ],
                "PreToolUse": [
                    {"matcher": "Bash", "hooks": [{"type": "command", "command": "/bin/signalbox hook pre-tool-use", "timeout": 10}]}
                ],
            },
        )
        self.assertEqual(hooks.installed(self.settings), list(hooks.EVENTS))

    def test_keeps_every_other_setting_and_hook_and_installs_once(self):
        self.write(
            self.settings,
            json.dumps({"model": "opus", "hooks": {"SessionStart": [{"hooks": [OTHER]}], "Stop": [{"hooks": [OTHER]}]}}, indent=2),
        )
        hooks.install(self.settings, "signalbox")
        self.assertEqual(hooks.install(self.settings, "signalbox"), [], "nothing new the second time")
        settings = self.read()
        self.assertEqual(settings["model"], "opus")
        self.assertEqual(settings["hooks"]["Stop"], [{"hooks": [OTHER]}])
        self.assertEqual(
            settings["hooks"]["SessionStart"],
            [{"hooks": [OTHER]}, {"hooks": [{"type": "command", "command": "signalbox hook session-start", "timeout": 15}]}],
        )

    def test_a_new_command_replaces_the_old_one(self):
        hooks.install(self.settings, "/old/signalbox")
        hooks.install(self.settings, "/new/signalbox")
        commands = [hook["command"] for groups in self.read()["hooks"].values() for group in groups for hook in group["hooks"]]
        self.assertEqual(sorted(commands), sorted("/new/signalbox hook %s" % event for event in hooks.EVENTS))

    def test_remove_takes_only_its_own_hooks(self):
        self.write(self.settings, json.dumps({"hooks": {"SessionStart": [{"hooks": [OTHER]}]}}))
        hooks.install(self.settings, "signalbox")
        self.assertEqual(hooks.remove(self.settings), list(hooks.EVENTS))
        self.assertEqual(self.read(), {"hooks": {"SessionStart": [{"hooks": [OTHER]}]}})
        self.assertEqual(hooks.remove(self.settings), [])
        self.assertEqual(hooks.installed(self.settings), [])

    def test_a_linked_settings_file_stays_a_link_and_keeps_its_mode(self):
        real = self.tmp / "dotfiles" / "settings.json"
        self.write(real, json.dumps({"model": "opus"}))
        real.chmod(0o600)
        self.settings.parent.mkdir(parents=True)
        self.settings.symlink_to(real)
        hooks.install(self.settings, "signalbox")
        self.assertTrue(self.settings.is_symlink())
        self.assertEqual(hooks.installed(real), list(hooks.EVENTS))
        self.assertEqual(real.stat().st_mode & 0o777, 0o600)
        hooks.remove(self.settings)
        self.assertTrue(self.settings.is_symlink())
        self.assertEqual(json.loads(real.read_text()), {"model": "opus"})

    def test_a_file_that_is_not_json_is_left_alone(self):
        self.write(self.settings, "{ not json")
        with self.assertRaises(hooks.SettingsError):
            hooks.install(self.settings, "signalbox")
        self.assertEqual(self.settings.read_text(), "{ not json")
        self.assertEqual(hooks.installed(self.settings), [])


class Hook(repos.Scratch):
    def setUp(self):
        super().setUp()
        self.repo = self.base_repo()
        self.host.spawn = lambda words, cwd=None: self.host.spawned.append((list(words), cwd))
        self.errors = []

    def link(self, features=None, **values):
        values.setdefault("gate", "make check")
        project = config.Project("shop", str(self.repo), tuple(config.FEATURES) if features is None else features, **values)
        config.save(config.Config((project,)), self.environ["SIGNALBOX_CONFIG"])

    def hook(self, event, data):
        self.printed, self.errors = [], []
        stdin = io.StringIO(data if isinstance(data, str) else json.dumps(data))
        return cli.main(["hook", event], host=self.host, out=self.out, err=self.errors.append, stdin=stdin, folder=self.tmp)

    def bash(self, command, cwd=None):
        return {"session_id": "s", "hook_event_name": "PreToolUse", "tool_name": "Bash", "cwd": str(cwd or self.repo), "tool_input": {"command": command}}

    def test_the_guard_refuses_a_direct_merge_in_a_linked_project(self):
        self.link()
        self.assertEqual(self.hook("pre-tool-use", self.bash("gh pr merge 104 --squash")), 2)
        self.assertEqual(self.errors, ["Merge through the lane: signalbox merge 104. A direct gh pr merge skips the check of the merged result."])
        self.assertEqual(self.printed, [])

    def test_the_guard_refuses_in_every_worktree_of_the_project(self):
        self.link()
        worktree = self.add_worktree(self.repo, "wt-a", "a")
        self.assertEqual(self.hook("pre-tool-use", self.bash("gh pr merge 7", cwd=worktree)), 2)

    def test_the_guard_lets_other_commands_through(self):
        self.link()
        for command in ("git status", "signalbox merge 104", 'git commit -m "gh pr merge"'):
            self.assertEqual(self.hook("pre-tool-use", self.bash(command)), 0)
        self.assertEqual(self.errors, [])

    def test_the_guard_is_off_without_the_feature_and_outside_a_linked_project(self):
        self.link(features=("board", "merge-lane"))
        self.assertEqual(self.hook("pre-tool-use", self.bash("gh pr merge 104")), 0)
        other = self.base_repo("other")
        self.link()
        self.assertEqual(self.hook("pre-tool-use", self.bash("gh pr merge 104", cwd=other)), 0)
        self.assertEqual(self.hook("pre-tool-use", self.bash("gh pr merge 104", cwd=self.tmp)), 0)
        self.assertEqual(self.errors, [])

    def test_the_guard_reads_a_merge_written_in_pieces(self):
        self.link()
        for command in ("gh pr mer''ge 104", "gh pr m\\erge 104", "gh api -X PUT repos/o/r/pulls/104/mer''ge"):
            with self.subTest(command=command):
                self.assertEqual(self.hook("pre-tool-use", self.bash(command)), 2)

    def test_fails_open_on_input_it_cannot_read(self):
        self.link()
        for data in ("not json", "", "null", "[]", "{}", '{"tool_input":{}}', '{"tool_input":{"command":42}}'):
            with self.subTest(data=data):
                self.assertEqual(self.hook("pre-tool-use", data), 0)
                self.assertEqual((self.printed, self.errors), ([], []))

    def test_session_start_briefs_the_session_and_sweeps_in_the_background(self):
        self.link()
        self.assertEqual(self.hook("session-start", {"session_id": "s", "cwd": str(self.repo)}), 0)
        self.assertEqual(self.printed[0].split("\n")[0], "This worktree: main, 0 behind origin/main, no conflict with origin/main.")
        [(words, cwd)] = self.host.spawned
        self.assertEqual(words[-1], "sweep")
        self.assertEqual(cwd, str(self.repo))

    def test_session_start_does_only_what_the_features_ask(self):
        self.link(features=("board",))
        self.assertEqual(self.hook("session-start", {"cwd": str(self.repo)}), 0)
        self.assertEqual((self.printed, self.host.spawned), ([], []))

    def test_session_start_is_silent_outside_a_linked_project_and_when_it_fails(self):
        self.assertEqual(self.hook("session-start", {"cwd": str(self.repo)}), 0)
        self.link()
        self.git(self.repo, "update-ref", "-d", "refs/remotes/origin/main")
        self.assertEqual(self.hook("session-start", {"cwd": str(self.repo)}), 0)
        self.assertEqual((self.printed, self.errors), ([], []))

    def test_user_prompt_submit_reports_what_changed(self):
        self.link()
        mine = self.add_worktree(self.repo, "wt-a", "a")
        self.write(mine / "a.txt", "a")
        other = self.add_worktree(self.repo, "wt-b", "b")
        self.assertEqual(self.hook("user-prompt-submit", {"cwd": str(mine)}), 0)
        self.assertEqual(self.printed, [])
        self.write(other / "a.txt", "b")
        self.assertEqual(self.hook("user-prompt-submit", {"cwd": str(mine)}), 0)
        self.assertEqual(self.printed, ["a.txt is now also changed by b."])


if __name__ == "__main__":
    unittest.main()
