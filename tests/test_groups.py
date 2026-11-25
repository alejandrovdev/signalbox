"""Tests of the group merge, `signalbox merge <pr> [<project>#<pr> ...]`, against made-up
GitHubs (see tests/lanes.py): every pull request is read first, then each lane runs in the
group's order, in its own repository, and the run stops at the first that does not merge."""

import io
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from signalbox import cli, config
from tests import lanes, repos
from tests.lanes import view_call

WARNING = "what merges stays merged if a later one fails."


class GroupMerge(lanes.Lane):
    def setUp(self):
        super().setUp()
        self.web = self.add_repository("web")

    def configure(self, *others, order=("web", "shop")):
        """shop (the caller's checkout), web and any other made-up repositories, in the group
        `platform` in the given order."""
        projects = (self.project(), self.web.project()) + tuple(other.project() for other in others)
        config.save(config.Config(projects, groups=(config.Group("platform", order),)), Path(self.environ["SIGNALBOX_CONFIG"]))

    def merge(self, *words):
        """`signalbox merge <words>` from the caller's checkout; (code, stdout, stderr)."""
        self.printed, self.err_printed = [], []
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            code = cli.main(["merge", *words], host=self.host, out=self.out, err=self.err, folder=self.checkout)
        return code, self.text(), "".join(line + "\n" for line in self.err_printed)

    def test_merges_every_pull_request_in_the_groups_order(self):
        self.configure()
        shop_base, web_base = self.origin_rev("main"), self.origin_rev("main", self.web)
        shop_head = self.open_pr(104, "feat/a")
        web_head = self.open_pr(7, "feat/w", self.web)

        code, stdout, stderr = self.merge("104", "web#7")

        self.assertEqual(code, 0, stderr)
        lines = stdout.splitlines()
        self.assertEqual(lines[0], "group: merging in order: web#7, shop#104; " + WARNING)
        merged = [line.split(":")[1] for line in lines if line.startswith("lane: #") and " merged: " in line]
        self.assertEqual(merged, [" #7 merged", " #104 merged"], "web first: the group's order, not the command line's")
        self.assertEqual(lines[-2:], ["group: web#7 merged", "group: shop#104 merged"])
        for other, base, head in ((None, shop_base, shop_head), (self.web, web_base, web_head)):
            self.assertEqual(self.origin_rev("main^1", other), base)
            self.assertEqual(self.origin_rev("main^2", other), head)
            [checked] = self.gate_calls(other)
            self.assertEqual(checked["head"], head, "each gate runs in its own repository")
        self.assertEqual(self.gh_calls(self.web)[:2], [view_call(7), view_call(7)], "read first, then by its lane")
        for name in ("shop", "web"):
            [record] = self.records(name)
            self.assertEqual((record["status"], record["group"], list(record)[-1]), ("merged", "platform", "group"))
        self.assertEqual(self.records("web")[0]["pullRequest"], 7)

    def test_stops_at_the_first_that_does_not_merge_and_tries_none_after_it(self):
        api = self.add_repository("api")
        self.configure(api, order=("web", "shop", "api"))
        web_head = self.open_pr(7, "feat/w", self.web)
        self.open_pr(104, "feat/a")
        self.open_pr(3, "feat/x", api)
        api_base = self.origin_rev("main", api)
        shop_base = self.origin_rev("main")
        (self.control / "gate-fail").write_text("")

        code, stdout, stderr = self.merge("104", "api#3", "web#7")

        self.assertEqual(code, 1)
        self.assertEqual(stdout.splitlines()[0], "group: merging in order: web#7, shop#104, api#3; " + WARNING)
        self.assertEqual(stdout.splitlines()[-3:], ["group: web#7 merged", "group: shop#104 not merged (gate_failed)", "group: api#3 not tried"])
        self.assertIn('lane: #104 not merged: the gate failed at "Lint".', stderr)
        self.assertEqual(self.origin_rev("main^2", self.web), web_head, "what merged stays merged")
        self.assertEqual(self.origin_rev("main"), shop_base)
        self.assertEqual(self.origin_rev("main", api), api_base)
        self.assertEqual(self.gh_calls(api), [view_call(3)], "only read")
        self.assertEqual((self.gate_calls(api), self.records("api")), ([], []))
        self.assertEqual([record["status"] for record in self.records("shop")], ["gate_failed"])
        self.assertEqual(self.records("shop")[0]["group"], "platform")

    def test_runs_nothing_when_any_pull_request_would_be_refused_and_names_each(self):
        self.configure()
        shop_head = self.open_pr(104, "feat/a")
        web_head = self.open_pr(7, "feat/w", self.web)
        self.describe(104, "feat/a", shop_head, state="CLOSED")
        self.describe(7, "feat/w", web_head, self.web, isDraft=True)
        tracked = self.git(self.checkout, "rev-parse", "refs/remotes/origin/main")

        code, stdout, stderr = self.merge("104", "web#7", "--allow-gate-change")

        self.assertEqual(code, 1)
        self.assertEqual(stdout, "")
        self.assertEqual(
            stderr,
            "group: web#7 refused: it is a draft.\n"
            "group: shop#104 refused: it is closed, not open.\n"
            "group: nothing was tried.\n",
        )
        self.assertEqual((self.gh_calls(), self.gh_calls(self.web)), ([view_call(104)], [view_call(7)]))
        self.assertEqual(self.git(self.checkout, "rev-parse", "refs/remotes/origin/main"), tracked, "nothing fetched")
        self.assertEqual((self.gate_calls(), self.gate_calls(self.web)), ([], []))
        self.assertEqual((self.records("shop"), self.records("web")), ([], []), "nothing logged")

    def test_runs_nothing_when_a_later_pull_request_changes_its_gate(self):
        self.configure()
        web_base = self.origin_rev("main", self.web)
        self.open_pr(7, "feat/w", self.web)
        gate = self.push_commit("feat/gate", "scripts/check.sh", "exit 0", "main")
        self.describe(104, "feat/gate", gate)

        code, stdout, stderr = self.merge("104", "web#7")

        self.assertEqual(code, 1)
        self.assertEqual(stdout, "")
        self.assertEqual(
            stderr,
            "group: shop#104 refused: it changes the gate (scripts/check.sh), which would check itself. "
            "Review that change, then run again with --allow-gate-change.\n"
            "group: nothing was tried.\n",
        )
        self.assertEqual(self.origin_rev("main", self.web), web_base, "web, first in order, was not merged")
        self.assertEqual((self.gate_calls(), self.gate_calls(self.web)), ([], []))
        self.assertEqual((self.records("shop"), self.records("web")), ([], []))

    def test_a_pull_request_gh_cannot_read_refuses_them_all(self):
        self.configure()
        self.open_pr(104, "feat/a")

        code, stdout, stderr = self.merge("104", "web#9")

        self.assertEqual(code, 1)
        self.assertIn("GraphQL: Could not resolve to a PullRequest with the number of 9.", stderr)
        self.assertTrue(stderr.endswith("group: web#9 refused: gh pr view could not read it.\ngroup: nothing was tried.\n"), stderr)
        self.assertEqual(self.gh_calls(), [view_call(104)])
        self.assertEqual(self.records("shop"), [])

    def test_with_dry_run_checks_each_and_merges_none(self):
        self.configure()
        shop_base, web_base = self.origin_rev("main"), self.origin_rev("main", self.web)
        self.open_pr(104, "feat/a")
        self.open_pr(7, "feat/w", self.web)

        code, stdout, stderr = self.merge("104", "web#7", "--dry-run")

        self.assertEqual(code, 0, stderr)
        self.assertEqual(stdout.splitlines()[-2:], ["group: web#7 checked, nothing merged (--dry-run)", "group: shop#104 checked, nothing merged (--dry-run)"])
        self.assertEqual((self.origin_rev("main"), self.origin_rev("main", self.web)), (shop_base, web_base))
        self.assertEqual((len(self.gate_calls()), len(self.gate_calls(self.web))), (1, 1))

    def test_a_bare_number_alone_is_one_lane_run_outside_any_group_run(self):
        self.configure()
        self.open_pr(104, "feat/a")

        code, stdout, stderr = self.merge("104")

        self.assertEqual(code, 0, stderr)
        self.assertNotIn("group:", stdout + stderr)
        [record] = self.records("shop")
        self.assertIsNone(record["group"])


class Usage(repos.Scratch):
    """What the command line refuses before reading anything (exit 2)."""

    def setUp(self):
        super().setUp()
        self.calls = self.stub_gh()
        self.folders = {name: self.base_repo(name) for name in ("shop", "web", "api", "blog")}

    def configure(self, groups):
        projects = tuple(
            config.Project(name, str(folder), tuple(config.FEATURES), gate="make check") for name, folder in self.folders.items()
        )
        config.save(config.Config(projects, groups=groups), self.environ["SIGNALBOX_CONFIG"])

    def usage(self, *words):
        stderr = io.StringIO()
        with redirect_stdout(io.StringIO()), redirect_stderr(stderr):
            code = cli.main(["merge", *words], host=self.host, out=self.out, err=self.out, folder=self.folders["shop"])
        return code, stderr.getvalue()

    def test_refuses_a_project_named_twice_unknown_or_outside_the_group(self):
        self.configure((config.Group("platform", ("web", "shop", "api")),))
        cases = [
            (["104", "shop#5"], 'the project "shop" is named twice'),
            (["104", "web#7", "web#8"], 'the project "web" is named twice'),
            (["104", "nope#3"], 'unknown project "nope"'),
            (["104", "blog#3"], 'the project "blog" is not in the group "platform" of "shop"'),
        ]
        for words, message in cases:
            with self.subTest(words=words):
                code, stderr = self.usage(*words)
                self.assertEqual(code, 2)
                self.assertEqual(stderr, "usage: signalbox merge ...\nsignalbox merge: error: %s\n" % message)
        self.assertEqual(self.calls(), [])
        self.assertEqual(self.printed, [])

    def test_refuses_another_projects_pull_request_when_this_project_is_in_no_group(self):
        self.configure(())
        code, stderr = self.usage("104", "web#7")
        self.assertEqual(code, 2)
        self.assertIn(
            'signalbox merge: error: the project "shop" is in no group: name pull requests of other projects only within a group '
            '([groups.<name>] in the configuration)',
            stderr,
        )
        self.assertEqual(self.calls(), [])

    def test_refuses_what_is_not_project_hash_number(self):
        self.configure((config.Group("platform", ("web", "shop")),))
        for word in ("105", "web#", "web#0", "#7", "Web#7"):
            with self.subTest(word=word):
                code, stderr = self.usage("104", word)
                self.assertEqual(code, 2)
                self.assertIn("is not <project>#<pr>, such as api#12", stderr)
        self.assertEqual(self.calls(), [])


if __name__ == "__main__":
    unittest.main()
