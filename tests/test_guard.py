"""Tests of the merge guard's reading of commands."""

import unittest

from signalbox.features import guard

REFUSED = [
    ("gh pr merge 104", "104"),
    ("gh pr merge 104 --merge --delete-branch", "104"),
    ("gh pr merge --squash --subject 'Release 7' 104", "104"),
    ("gh pr merge https://github.com/owner/repo/pull/104", "104"),
    ("gh pr merge", "<number>"),
    ("make test && gh pr merge 104", "104"),
    ("git push; gh pr merge 104", "104"),
    ("git fetch || gh pr merge 104", "104"),
    ("echo y | gh pr merge 104", "104"),
    ("git fetch\ngh pr merge 104", "104"),
    ("env GH_PROMPT_DISABLED=1 gh pr merge 104", "104"),
    ("GH_TOKEN=x gh pr merge 104", "104"),
    ("bash -c 'gh pr merge 104 --merge'", "104"),
    ("gh api --method PUT repos/owner/repo/pulls/104/merge", "104"),
    ("gh api -X PUT /repos/{owner}/{repo}/pulls/104/merge -f merge_method=merge", "104"),
    ("curl -X PUT https://api.github.com/repos/owner/repo/pulls/104/merge", "104"),
    # gh's own options take a value wherever they stand: the value is not the subcommand.
    ("gh -R owner/repo pr merge 104", "104"),
    ("gh --repo owner/repo pr merge 104 --merge", "104"),
    ("gh pr -R owner/repo merge 104", "104"),
    ("gh --repo=owner/repo pr merge 104", "104"),
    ("timeout 60 gh pr merge 104", "104"),
    ("timeout -k 5 60 gh pr merge 104", "104"),
    ("gh api --method=put repos/owner/repo/pulls/104/merge", "104"),
    ("/opt/homebrew/bin/gh pr merge 104", "104"),
]

PASSED = [
    'git commit -m "docs: never run gh pr merge 104 by hand"',
    "rg 'gh pr merge' docs",
    "echo gh pr merge 104",
    "gh pr view 104 --json state",
    "gh api repos/owner/repo/pulls/104",
    'grep -n "pulls/104/merge" README.md',
    "signalbox merge 104",
    "ls -la",
    # Asking is not merging: the command's help, and a GET that says whether it was merged.
    "gh pr merge --help",
    "gh api repos/owner/repo/pulls/104/merge",
    "curl -s https://api.github.com/repos/owner/repo/pulls/104/merge",
    "timeout 60 make test",
    "gh -R owner/repo pr view 104",
    "",
]


class Guard(unittest.TestCase):
    def test_refuses_a_direct_merge(self):
        for command, number in REFUSED:
            with self.subTest(command=command):
                self.assertEqual(guard.direct_merge(command), number)

    def test_lets_every_other_command_through(self):
        for command in PASSED:
            with self.subTest(command=command):
                self.assertIsNone(guard.direct_merge(command))

    def test_says_to_use_the_lane(self):
        self.assertEqual(
            guard.refusal("104"),
            "Merge through the lane: signalbox merge 104. A direct gh pr merge skips the check of the merged result.",
        )


if __name__ == "__main__":
    unittest.main()
