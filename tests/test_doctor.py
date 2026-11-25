"""Tests of what the prerequisites checks offer Homebrew to install, of finding Homebrew, and
of running an install in front of the person."""

import tempfile
import unittest
from pathlib import Path

from signalbox import doctor
from signalbox.host import Host
from tests import repos


class Installable(repos.Scratch):
    def setUp(self):
        super().setUp()
        self.stub_gh()
        self.environ["SIGNALBOX_CLAUDE"] = str(self.stub("fake-claude", "exit 0"))
        self.environ["SIGNALBOX_GIT"] = str(self.stub("fake-git", "echo git version 2.45.0"))
        self.settings = self.home / ".claude" / "settings.json"

    def offered(self):
        named = doctor.named_prerequisites(self.host, self.settings)
        return [(name, doctor.brew_command(check)) for name, check in doctor.installable(named)]

    def test_nothing_is_offered_when_every_tool_is_there_and_the_hooks_are_never_offered(self):
        self.assertEqual(self.offered(), [])

    def test_git_gh_and_claude_code_missing_are_offered_in_their_order_with_their_commands(self):
        for name in ("git", "gh", "claude"):
            self.environ["SIGNALBOX_" + name.upper()] = str(self.tmp / ("no-" + name))

        self.assertEqual(
            self.offered(),
            [("git", "brew install git"), ("gh", "brew install gh"), ("claude", "brew install --cask claude-code")],
        )

    def test_a_git_older_than_2_38_is_offered_too(self):
        self.environ["SIGNALBOX_GIT"] = str(self.stub("old-git", "echo git version 2.30.1"))

        self.assertEqual(self.offered(), [("git", "brew install git")])

    def test_a_gh_that_is_not_signed_in_is_not_offered(self):
        self.stub_gh(code=1)

        self.assertEqual(self.offered(), [])
        self.assertTrue(doctor.needs_sign_in(doctor.named_prerequisites(self.host, self.settings)))

    def test_a_tool_named_where_no_program_is_counts_as_missing(self):
        self.environ["SIGNALBOX_CLAUDE"] = str(self.tmp / "no-claude")
        self.environ["SIGNALBOX_GIT"] = str(self.tmp / "no-git")

        checks = dict(doctor.named_prerequisites(self.host, self.settings))

        self.assertEqual(checks["claude"].line(), "missing  Claude Code (claude): the session brief and the merge guard need it")
        self.assertEqual(checks["git"].line(), "missing  git: install it with: brew install git")


class Homebrew(repos.Scratch):
    def test_found_where_it_is_named_and_none_when_no_program_is_there(self):
        brew = self.stub("brew", "exit 0")
        self.environ["SIGNALBOX_BREW"] = str(brew)
        self.assertEqual(doctor.homebrew(self.host), str(brew))

        self.environ["SIGNALBOX_BREW"] = str(self.tmp / "no-brew")
        self.assertIsNone(doctor.homebrew(self.host))


class RunInView(unittest.TestCase):
    def test_runs_a_program_with_the_terminal_and_gives_its_exit_code(self):
        host = Host()

        self.assertEqual(host.run_in_view(["/usr/bin/true"]), 0)
        self.assertEqual(host.run_in_view(["/usr/bin/false"]), 1)
        self.assertEqual(host.run_in_view([str(Path(tempfile.gettempdir()) / "no-such-program-here")]), 127)


if __name__ == "__main__":
    unittest.main()
