"""Tests of the session records: what `signalbox hook <event>` writes about the session it
runs for, and how the host finds that session's `claude` process."""

import io
import json
import unittest

from signalbox import cli, config
from signalbox.host import Host
from tests import repos

STARTED = ["Tue Nov 24 15:17:16 2026", "Tue Nov 24 09:17:16 2026"]
SESSION = "0123abcd-4567-89ef-0123-456789abcdef"


class Recording(repos.Scratch):
    def setUp(self):
        super().setUp()
        self.repo = self.base_repo()
        self.host.spawn = lambda words, cwd=None: self.host.spawned.append((list(words), cwd))
        self.host.claude = 4242
        self.host.started = {4242: list(STARTED)}
        self.host.now = 1000.0
        self.errors = []
        self.sessions = self.tmp / "sessions"

    def hook(self, event, data):
        self.printed, self.errors = [], []
        stdin = io.StringIO(data if isinstance(data, str) else json.dumps(data))
        return cli.main(["hook", event], host=self.host, out=self.out, err=self.errors.append, stdin=stdin, folder=self.tmp)

    def bash(self, command, cwd=None, session=SESSION):
        return {"session_id": session, "tool_name": "Bash", "cwd": str(cwd or self.repo), "tool_input": {"command": command}}

    def record(self, session=SESSION):
        return json.loads((self.sessions / ("%s.json" % session)).read_text())

    def test_the_first_hook_finds_the_sessions_process_once_and_records_where_it_stands(self):
        self.assertEqual(self.hook("session-start", {"session_id": SESSION, "cwd": str(self.repo)}), 0)
        self.assertEqual((self.printed, self.errors, self.host.spawned), ([], [], []))
        self.assertEqual(self.record(), {"cwd": str(self.repo), "pid": 4242, "started": STARTED, "updated": 1000})
        self.assertEqual(self.host.walks, 1)

    def test_a_later_hook_in_another_folder_updates_the_record_without_looking_again(self):
        self.hook("session-start", {"session_id": SESSION, "cwd": str(self.repo)})
        worktree = self.add_worktree(self.repo, "wt-a", "a")
        self.host.started = {}  # the dates come from the record, not from `ps`
        self.host.now = 1010.0
        self.assertEqual(self.hook("pre-tool-use", self.bash("git status", cwd=worktree)), 0)
        self.assertEqual(self.record(), {"cwd": str(worktree), "pid": 4242, "started": STARTED, "updated": 1010})
        self.assertEqual(self.host.walks, 1)

    def test_nothing_is_written_while_the_folder_is_the_same_and_the_record_is_a_minute_old_or_less(self):
        self.hook("user-prompt-submit", {"session_id": SESSION, "cwd": str(self.repo)})
        self.host.now = 1060.0
        self.hook("pre-tool-use", self.bash("ls"))
        self.assertEqual(self.record()["updated"], 1000)
        self.host.now = 1061.0
        self.hook("pre-tool-use", self.bash("ls"))
        self.assertEqual(self.record()["updated"], 1061)
        self.assertEqual(self.host.walks, 1)

    def test_a_record_that_cannot_be_read_is_written_again(self):
        self.write(self.sessions / ("%s.json" % SESSION), "not json")
        self.hook("pre-tool-use", self.bash("ls"))
        self.assertEqual(self.record(), {"cwd": str(self.repo), "pid": 4242, "started": STARTED, "updated": 1000})
        self.assertEqual(self.host.walks, 1)

    def test_records_nothing_without_a_usable_session_id(self):
        for session in (None, "", 42, "../escape", "a/b", "a b", "x" * 129):
            with self.subTest(session=session):
                data = {"cwd": str(self.repo)} if session is None else {"session_id": session, "cwd": str(self.repo)}
                self.assertEqual(self.hook("pre-tool-use", dict(data, tool_name="Bash", tool_input={"command": "ls"})), 0)
                self.assertEqual((self.printed, self.errors), ([], []))
        self.assertFalse(self.sessions.exists() and any(self.sessions.iterdir()))
        self.assertEqual(self.host.walks, 0)

    def test_records_nothing_without_a_claude_process_or_its_start(self):
        self.host.claude = None
        self.assertEqual(self.hook("pre-tool-use", self.bash("ls")), 0)
        self.host.claude = 4243  # `ps` knows no such process
        self.assertEqual(self.hook("pre-tool-use", self.bash("ls")), 0)
        self.host.started = None  # `ps` cannot be read
        self.assertEqual(self.hook("pre-tool-use", self.bash("ls")), 0)
        self.assertFalse(self.sessions.exists() and any(self.sessions.iterdir()))
        self.assertEqual((self.printed, self.errors), ([], []))

    def test_the_guard_still_refuses_a_direct_merge_and_the_session_is_recorded(self):
        project = config.Project("shop", str(self.repo), tuple(config.FEATURES), gate="make check")
        config.save(config.Config((project,)), self.environ["SIGNALBOX_CONFIG"])
        self.assertEqual(self.hook("pre-tool-use", self.bash("gh pr merge 104 --squash")), 2)
        self.assertEqual(self.errors, ["Merge through the lane: signalbox merge 104. A direct gh pr merge skips the check of the merged result."])
        self.assertEqual(self.printed, [])
        self.assertEqual(self.record()["cwd"], str(self.repo))


class SessionProcess(repos.Scratch):
    def stub_ps(self, answers):
        """A `ps -o ppid=,comm= -p <pid>` that answers from a pid's line in `answers` (any
        other pid: the first line), recording each pid asked."""
        calls = self.tmp / "ps-calls"
        cases = "".join("  %s) echo '%s';;\n" % (pid, line) for pid, line in answers[1:])
        self.stub("ps", 'echo "$4" >> \'%s\'\ncase "$4" in\n%s  *) echo \'%s\';;\nesac' % (calls, cases, answers[0][1]))
        return lambda: calls.read_text().split() if calls.exists() else []

    def test_walks_up_to_the_first_claude_ancestor(self):
        asked = self.stub_ps([(None, "  100 sh"), (100, "  200 -zsh"), (200, "    1 /usr/local/bin/claude")])
        self.assertEqual(Host(self.environ).session_process(), 200)
        self.assertEqual(asked()[1:], ["100", "200"])

    def test_gives_up_after_six_levels_and_when_ps_fails(self):
        asked = self.stub_ps([(None, "  100 /bin/zsh")])
        self.assertIsNone(Host(self.environ).session_process())
        self.assertEqual(len(asked()), 6)
        self.stub("ps", "exit 1")
        self.assertIsNone(Host(self.environ).session_process())


if __name__ == "__main__":
    unittest.main()
