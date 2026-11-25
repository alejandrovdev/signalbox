"""Tests of the merge lane against a made-up GitHub (see tests/lanes.py)."""

import os
import re
import signal
import unittest
from pathlib import Path
from unittest import mock

from signalbox import names
from signalbox.atoms import lock
from signalbox.features import lane as lane_module
from tests import lanes
from tests.lanes import SETTING_CALL, merge_call, status_call, view_call

RECORD_KEYS = [
    "version",
    "startedAt",
    "pullRequest",
    "head",
    "status",
    "waitSeconds",
    "gateSeconds",
    "durationSeconds",
    "attempts",
    "baseTip",
    "tree",
    "mergeCommit",
    "failedStep",
    "gateLog",
]


def is_count(value):
    return isinstance(value, int) and value >= 0


class MergeLane(lanes.Lane):
    def test_merges_a_pull_request_whose_merged_result_passes_and_leaves_the_callers_checkout_alone(self):
        base_before = self.origin_rev("main")
        head = self.open_pr(104, "feat/a")
        before = self.caller_state()

        code, stdout, stderr = self.lane(104)

        self.assertEqual(code, 0, stderr)
        [checked] = self.gate_calls()
        self.assertEqual(checked["base"], base_before)
        self.assertEqual(checked["head"], head)
        self.assertEqual(checked["workers"], "3", "the gate gets the project's gate_env")
        merge_commit = self.origin_rev("main")
        self.assertEqual(self.origin_rev("main^{tree}"), checked["tree"])
        self.assertEqual(self.origin_rev("main^1"), base_before)
        self.assertEqual(self.origin_rev("main^2"), head)
        self.assertIn(
            "lane: #104 merged: main %s holds the checked tree %s." % (merge_commit[:12], checked["tree"][:12]), stdout
        )
        self.assertIn("check: every check passed.", stderr, "the gate's output reaches the caller")
        self.assertEqual(
            self.gh_calls(),
            [
                view_call(104),
                status_call(head, "success", "checked tree %s on main %s" % (checked["tree"][:12], base_before[:12])),
                merge_call(104, head),
                SETTING_CALL,
            ],
        )
        [record] = self.records()
        self.assertEqual(list(record), RECORD_KEYS)
        for key, value in {
            "version": 1,
            "pullRequest": 104,
            "head": head,
            "status": "merged",
            "attempts": 1,
            "baseTip": base_before,
            "tree": checked["tree"],
            "mergeCommit": merge_commit,
            "failedStep": None,
            "gateLog": None,
        }.items():
            self.assertEqual(record[key], value, key)
        self.assertRegex(record["startedAt"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")
        for key in ("waitSeconds", "gateSeconds", "durationSeconds"):
            self.assertTrue(is_count(record[key]), key)
        self.assertEqual(self.caller_state(), before)
        self.assertNotEqual(
            self.host.run(["git", "-C", str(self.lane_worktree), "symbolic-ref", "-q", "HEAD"]).returncode,
            0,
            "the merge worktree is left detached",
        )
        self.assertFalse(list((self.tmp / "logs" / "shop").glob("gates/*")), "a passing gate's output is not kept")

    def test_merges_nothing_when_the_gate_fails_and_keeps_its_whole_output(self):
        base_before = self.origin_rev("main")
        head = self.open_pr(104, "feat/a")
        (self.control / "gate-fail").write_text("")

        code, stdout, stderr = self.lane(104)

        self.assertEqual(code, 1)
        self.assertIn('check: FAILED at "Lint" (exit 1).', stderr)
        self.assertIn('lane: #104 not merged: the gate failed at "Lint".', stderr)
        [checked] = self.gate_calls()
        self.assertEqual(self.gh_calls(), [view_call(104), status_call(head, "failure", 'failed at "Lint"')])
        self.assertEqual(self.origin_rev("main"), base_before)
        [record] = self.records()
        self.assertEqual(
            {key: record[key] for key in ("status", "attempts", "baseTip", "tree", "mergeCommit", "failedStep")},
            {
                "status": "gate_failed",
                "attempts": 1,
                "baseTip": base_before,
                "tree": checked["tree"],
                "mergeCommit": None,
                "failedStep": "Lint",
            },
        )
        kept = record["gateLog"]
        self.assertTrue(kept.startswith(str(self.tmp / "logs" / "shop" / "gates") + "/"), kept)
        self.assertIn("lane: the gate's whole output is in %s" % kept, stderr)
        text = Path(kept).read_text()
        self.assertIn('check: FAILED at "Lint" (exit 1).', text)
        self.assertIn("the failing test's message", text)

    def test_a_gate_whose_failed_step_is_not_found_says_its_exit_code(self):
        head = self.open_pr(104, "feat/a")
        (self.control / "gate-fail").write_text("")
        code, _, stderr = self.lane(104, self.project(gate_failed_step=None))
        self.assertEqual(code, 1)
        self.assertIn("lane: #104 not merged: the gate failed (exit 1).", stderr)
        self.assertEqual(self.gh_calls()[-1], status_call(head, "failure", "the gate failed (exit 1)"))

    def test_refuses_a_pull_request_that_conflicts_with_the_base_before_any_gate(self):
        self.open_pr(104, "feat/a")
        branch_head = self.push_commit("feat/a", "README.md", "the branch's line")
        self.describe(104, "feat/a", branch_head)
        base_before = self.push_commit("main", "README.md", "main's line")
        before = self.caller_state()

        code, _, stderr = self.lane(104)

        self.assertEqual(code, 1)
        self.assertIn("lane: #104 not merged: it conflicts with main; update the branch with main and run again.", stderr)
        self.assertEqual(self.gate_calls(), [])
        self.assertEqual(self.gh_calls(), [view_call(104), status_call(branch_head, "failure", "conflicts with main")])
        self.assertEqual(self.origin_rev("main"), base_before)
        [record] = self.records()
        self.assertEqual(
            {key: record[key] for key in ("status", "head", "attempts", "baseTip", "tree", "gateSeconds", "mergeCommit")},
            {
                "status": "conflict",
                "head": branch_head,
                "attempts": 1,
                "baseTip": base_before,
                "tree": None,
                "gateSeconds": None,
                "mergeCommit": None,
            },
        )
        self.assertEqual(self.git(self.lane_worktree, "status", "--porcelain"), "")
        self.assertNotEqual(self.host.run(["git", "-C", str(self.lane_worktree), "rev-parse", "-q", "--verify", "MERGE_HEAD"]).returncode, 0)
        self.assertEqual(self.caller_state(), before)

    def test_refuses_a_pull_request_that_is_closed_a_draft_on_another_base_or_from_a_fork_before_any_fetch(self):
        cases = [
            ({"state": "CLOSED"}, "lane: #104 refused: it is closed, not open."),
            ({"isDraft": True}, "lane: #104 refused: it is a draft."),
            ({"baseRefName": "release"}, "lane: #104 refused: it is based on release, not main."),
            ({"isCrossRepository": True}, "lane: #104 refused: its head is in another repository."),
        ]
        head = self.open_pr(104, "feat/a")
        self.push_commit("main", "later.txt", "merged after the caller's last fetch")
        tracked = self.git(self.checkout, "rev-parse", "refs/remotes/origin/main")
        for overrides, message in cases:
            with self.subTest(message=message):
                (self.control / "gh-calls").unlink(missing_ok=True)
                self.describe(104, "feat/a", head, **overrides)
                code, _, stderr = self.lane(104)
                self.assertEqual(code, 1)
                self.assertIn(message, stderr)
                self.assertEqual(self.gh_calls(), [view_call(104)])
                self.assertEqual(self.git(self.checkout, "rev-parse", "refs/remotes/origin/main"), tracked)
                self.assertFalse(self.lane_worktree.exists())
                self.assertEqual(self.gate_calls(), [])
                record = self.records()[-1]
                self.assertEqual(
                    {key: record[key] for key in RECORD_KEYS if key not in ("startedAt", "waitSeconds", "durationSeconds")},
                    {
                        "version": 1,
                        "pullRequest": 104,
                        "head": head,
                        "status": "refused",
                        "gateSeconds": None,
                        "attempts": 0,
                        "baseTip": None,
                        "tree": None,
                        "mergeCommit": None,
                        "failedStep": None,
                        "gateLog": None,
                    },
                )

    def test_is_refused_by_match_head_commit_when_the_head_changed_after_the_check(self):
        base_before = self.origin_rev("main")
        head = self.open_pr(104, "feat/a")
        self.on_event("during-gate", 'push-commit feat/a later.txt "pushed during the gate"')

        code, _, stderr = self.lane(104)

        self.assertEqual(code, 1)
        self.assertIn("Head branch was modified", stderr)
        self.assertIn("lane: #104 not merged: gh pr merge refused it.", stderr)
        self.assertEqual(self.gh_calls()[-1], merge_call(104, head))
        self.assertEqual(self.origin_rev("main"), base_before)
        self.assertEqual([(r["status"], r["head"], r["mergeCommit"]) for r in self.records()], [("merge_failed", head, None)])

    def test_checks_again_over_the_new_base_when_the_base_moves_during_the_gate(self):
        base_before = self.origin_rev("main")
        head = self.open_pr(104, "feat/a")
        self.on_event("during-gate", 'push-commit main moved.txt "merged during the gate"')

        code, stdout, stderr = self.lane(104)

        self.assertEqual(code, 0, stderr)
        moved = self.origin_rev("main^1")
        self.assertNotEqual(moved, base_before)
        calls = self.gate_calls()
        self.assertEqual([(call["base"], call["head"]) for call in calls], [(base_before, head), (moved, head)])
        self.assertIn("lane: main moved during the gate; attempt 2 of 3.", stdout)
        self.assertEqual(self.origin_rev("main^{tree}"), calls[1]["tree"])
        self.assertEqual(
            self.gh_calls(),
            [
                view_call(104),
                status_call(head, "success", "checked tree %s on main %s" % (calls[1]["tree"][:12], moved[:12])),
                merge_call(104, head),
                SETTING_CALL,
            ],
        )
        [record] = self.records()
        self.assertEqual(
            (record["status"], record["attempts"], record["baseTip"], record["tree"], record["mergeCommit"]),
            ("merged", 2, moved, calls[1]["tree"], self.origin_rev("main")),
        )

    def test_refuses_after_3_attempts_when_the_base_moves_during_each_gate(self):
        head = self.open_pr(104, "feat/a")
        self.on_event("during-gate", 'push-commit main moved.txt "moved by $$"', once=False)

        code, _, stderr = self.lane(104)

        self.assertEqual(code, 1)
        self.assertEqual(len(self.gate_calls()), 3)
        self.assertIn("lane: #104 not merged: main moved during each of 3 attempts; run again.", stderr)
        self.assertEqual(self.gh_calls(), [view_call(104), status_call(head, "failure", "main moved during each of 3 attempts")])
        self.assertEqual([(r["status"], r["attempts"]) for r in self.records()], [("base_moved", 3)])

    def test_with_dry_run_checks_and_merges_nothing_and_posts_nothing(self):
        base_before = self.origin_rev("main")
        head = self.open_pr(104, "feat/a")

        code, stdout, _ = self.lane(104, dry_run=True)

        self.assertEqual(code, 0)
        [checked] = self.gate_calls()
        self.assertIn(
            "lane: #104 checked: tree %s on main %s; nothing merged (--dry-run)." % (checked["tree"][:12], base_before[:12]), stdout
        )
        self.assertEqual(self.gh_calls(), [view_call(104)])
        self.assertEqual(self.origin_rev("main"), base_before)
        self.assertEqual([(r["status"], r["head"], r["tree"]) for r in self.records()], [("checked", head, checked["tree"])])

    def test_warns_and_still_merges_when_the_status_cannot_be_posted(self):
        head = self.open_pr(104, "feat/a")
        (self.control / "api-fail").write_text("")

        code, _, stderr = self.lane(104)

        self.assertEqual(code, 0)
        self.assertIn("lane: warning: could not post the signalbox/merge-lane status on %s." % head[:12], stderr)
        self.assertEqual(self.gh_calls()[-2:], [merge_call(104, head), SETTING_CALL])
        self.assertEqual([r["status"] for r in self.records()], ["merged"])

    def test_after_a_merge_removes_the_worktree_of_the_merged_branch_and_deletes_the_branch(self):
        head = self.open_pr(104, "feat/a")
        merged = self.tmp / "wt-a"
        self.git(self.checkout, "fetch", "--quiet", "origin")
        self.git(self.checkout, "worktree", "add", "--quiet", "-b", "feat/a", str(merged), "origin/feat/a")
        self.open_pr(105, "feat/b")
        opened = self.tmp / "wt-b"
        self.git(self.checkout, "fetch", "--quiet", "origin")
        self.git(self.checkout, "worktree", "add", "--quiet", "-b", "feat/b", str(opened), "origin/feat/b")
        self.host.cwd_list = []
        before = self.caller_state()

        code, stdout, stderr = self.lane(104)

        self.assertEqual(code, 0)
        self.assertNotIn("warning", stderr)
        shown = stdout.rstrip("\n").split("\n")
        self.assertRegex(shown[-3], r"^lane: #104 merged: main [0-9a-f]{12} holds the checked tree ")
        self.assertEqual(
            shown[-2:],
            [
                "sweep: removed worktree %s (feat/a, merged into origin/main)" % merged,
                "sweep: deleted branch feat/a (merged into origin/main)",
            ],
        )
        self.assertFalse(merged.exists())
        self.assertTrue(opened.exists())
        self.assertEqual(
            self.git(self.checkout, "for-each-ref", "--format=%(refname)", "refs/heads", "refs/remotes/origin").split(),
            [
                "refs/heads/feat/b",
                "refs/heads/main",
                "refs/heads/work",
                "refs/remotes/origin/HEAD",
                "refs/remotes/origin/feat/b",
                "refs/remotes/origin/main",
            ],
        )
        self.assertEqual([(r["status"], r["head"]) for r in self.records()], [("merged", head)])
        self.assertEqual(self.caller_state(), before)

    def test_leaves_the_sweep_out_of_a_project_without_it(self):
        self.open_pr(104, "feat/a")
        features = ("board", "session-brief", "merge-lane", "merge-guard", "metrics")
        code, stdout, _ = self.lane(104, self.project(features=features))
        self.assertEqual(code, 0)
        self.assertNotIn("sweep:", stdout)

    def test_warns_after_a_merge_when_github_keeps_merged_branches_or_the_setting_cannot_be_read(self):
        self.open_pr(104, "feat/a")
        (self.control / "keep-merged-branches").write_text("")
        code, _, stderr = self.lane(104)
        self.assertEqual(code, 0)
        self.assertEqual(
            stderr.split("\n")[-2],
            'lane: warning: GitHub keeps merged branches; turn on "Automatically delete head branches" in the repository\'s settings.',
        )
        self.open_pr(105, "feat/b")
        (self.control / "api-fail").write_text("")
        code, _, stderr = self.lane(105)
        self.assertEqual(code, 0)
        self.assertIn("lane: warning: could not read whether GitHub deletes merged branches.\n", stderr)
        self.assertEqual([r["status"] for r in self.records()], ["merged", "merged"])

    def test_a_sweep_that_fails_is_a_warning_and_the_run_is_recorded_as_merged(self):
        self.open_pr(104, "feat/a")
        with mock.patch("signalbox.features.sweep.sweep", side_effect=RuntimeError("no sweep today")):
            code, stdout, stderr = self.lane(104)
        self.assertEqual(code, 0)
        self.assertIn("lane: #104 merged: main ", stdout)
        self.assertIn("lane: warning: the sweep of merged worktrees and branches failed: no sweep today\n", stderr)
        self.assertEqual([r["status"] for r in self.records()], ["merged"])

    def test_exits_1_when_the_bases_tree_after_the_merge_is_not_the_checked_one(self):
        self.open_pr(104, "feat/a")
        self.on_event("after-merge", 'push-commit main outside.txt "merged outside the lane"')

        code, _, stderr = self.lane(104)

        self.assertEqual(code, 1)
        [checked] = self.gate_calls()
        after = self.origin_rev("main")
        self.assertIn(
            "lane: #104 merged, but main %s has tree %s, not the checked tree %s."
            % (after[:12], self.origin_rev("main^{tree}")[:12], checked["tree"][:12]),
            stderr,
        )
        self.assertEqual([(r["status"], r["tree"], r["mergeCommit"]) for r in self.records()], [("tree_mismatch", checked["tree"], after)])

    def test_prints_and_exits_the_same_when_the_lane_log_cannot_be_written(self):
        self.open_pr(104, "feat/a")
        self.environ["SIGNALBOX_LOG_DIR"] = str(self.tmp / "logs")

        def printed(result):
            code, stdout, stderr = result
            return code, re.sub(r"[0-9a-f]{12}", "<id>", stdout), stderr

        written = printed(self.lane(104, dry_run=True))
        (self.tmp / "a-file").write_text("")
        self.environ["SIGNALBOX_LOG_DIR"] = str(self.tmp / "a-file")
        unwritable = printed(self.lane(104, dry_run=True))
        self.assertEqual(written[0], 0)
        self.assertEqual(unwritable, written)

    def test_names_a_missing_gh(self):
        self.open_pr(104, "feat/a")
        self.environ["SIGNALBOX_GH"] = str(self.tmp / "no-gh")
        code, _, stderr = self.lane(104)
        self.assertEqual(code, 1)
        self.assertEqual(stderr, "lane: gh is required but not installed: install it with: brew install gh\n")
        self.assertEqual(self.records(), [])

    def test_merges_with_the_projects_method(self):
        head = self.open_pr(104, "feat/a")
        code, _, stderr = self.lane(104, self.project(merge_method="squash"))
        self.assertEqual(code, 0, stderr)
        self.assertIn(merge_call(104, head, "squash"), self.gh_calls())
        [record] = self.records()
        self.assertEqual(self.origin_rev("main^{tree}"), record["tree"], "a squash writes the checked tree too")

    def test_refuses_a_pull_request_that_changes_its_own_gate_unless_told_to_allow_it(self):
        head = self.push_commit("feat/gate", "scripts/check.sh", "exit 0", "main")
        self.describe(104, "feat/gate", head)

        code, _, stderr = self.lane(104)

        self.assertEqual(code, 1)
        self.assertIn(
            "lane: #104 refused: it changes the gate (scripts/check.sh), which would check itself. "
            "Review that change, then run again with --allow-gate-change.",
            stderr,
        )
        self.assertEqual(self.gate_calls(), [])
        self.assertEqual([r["status"] for r in self.records()], ["gate_changed"])

        code, _, stderr = self.lane(104, allow_gate_change=True)
        self.assertEqual(code, 0, stderr)
        self.assertEqual([r["status"] for r in self.records()], ["gate_changed", "merged"])

    def test_makes_the_merge_worktree_again_when_it_holds_a_stale_index_lock(self):
        self.open_pr(104, "feat/a")
        self.lane(104, dry_run=True)
        gitdir = self.git(self.lane_worktree, "rev-parse", "--absolute-git-dir").strip()
        (self.lane_worktree / "node_modules").mkdir()
        open(os.path.join(gitdir, "index.lock"), "w").close()

        code, stdout, stderr = self.lane(104)

        self.assertEqual(code, 0, stderr)
        self.assertIn("lane: the merge worktree held a stale index.lock (a killed git left it); made it again.", stdout)
        self.assertFalse((self.lane_worktree / "node_modules").exists())
        self.assertEqual(self.records()[-1]["status"], "merged")

    def test_makes_the_merge_worktree_again_when_its_folder_is_gone(self):
        self.open_pr(104, "feat/a")
        self.lane(104, dry_run=True)
        self.host.run(["rm", "-rf", str(self.lane_worktree)])
        code, _, stderr = self.lane(104)
        self.assertEqual(code, 0, stderr)


class MergeLaneProcesses(lanes.Lane):
    """The lane as another process: one at a time, signals, and a gate a killed lane left."""

    def test_runs_one_at_a_time_the_second_waits_and_checks_a_tree_that_holds_the_first_ones_merge(self):
        first_head = self.open_pr(104, "feat/a")
        second_head = self.open_pr(105, "feat/b")
        self.on_event(
            "during-gate", 'touch "%s/first-started"\nwhile [ ! -e "%s/release" ]; do sleep 0.1; done' % (self.control, self.control)
        )
        first = self.command("merge", "104")
        self.wait_for(lambda: (self.control / "first-started").exists(), "the first gate")
        second = self.command("merge", "105")
        self.wait_for(lambda: lock.probe(self.checkout / ".git" / names.LANE_LOCK) == lock.BUSY, "the lock")
        waited = second.stdout.readline()
        self.assertEqual(waited, "lane: another merge is running; #105 waits for its turn.\n")
        self.assertEqual(len(self.gate_calls()), 1)
        (self.control / "release").write_text("")
        first.communicate(timeout=120)
        second.communicate(timeout=120)

        self.assertEqual((first.returncode, second.returncode), (0, 0))
        first_record, second_record = self.records()
        self.assertEqual((first_record["pullRequest"], first_record["head"], first_record["status"]), (104, first_head, "merged"))
        self.assertEqual((second_record["pullRequest"], second_record["head"], second_record["status"]), (105, second_head, "merged"))
        second_gate = self.gate_calls()[1]
        self.assertEqual(second_gate["base"], first_record["mergeCommit"])
        self.assertEqual(second_record["baseTip"], first_record["mergeCommit"])
        self.assertEqual(self.origin_rev("main^1"), first_record["mergeCommit"])
        self.assertEqual(self.origin_rev("main^{tree}"), second_gate["tree"])

    def gate_that_hangs(self):
        pid_file = self.control / "gate-pid"
        self.on_event("during-gate", 'echo $$ > "%s"\nexec sleep 60' % pid_file)
        return pid_file

    def test_stops_its_gate_and_leaves_the_lock_free_and_the_base_unchanged_when_stopped(self):
        base_before = self.origin_rev("main")
        self.open_pr(104, "feat/a")
        pid_file = self.gate_that_hangs()
        before = self.caller_state()
        running = self.command("merge", "104")
        self.wait_for(lambda: pid_file.exists() and pid_file.read_text().strip(), "the gate")
        gate = int(pid_file.read_text())

        running.send_signal(signal.SIGTERM)
        running.communicate(timeout=60)

        self.assertEqual(running.returncode, 143)
        self.wait_for(lambda: not lanes.alive(gate), "the gate to stop", seconds=15)
        self.assertEqual(self.gh_calls(), [view_call(104)])
        self.assertEqual(self.origin_rev("main"), base_before)
        self.assertEqual([(r["status"], r["mergeCommit"]) for r in self.records()], [("interrupted", None)])
        self.assertEqual(lock.probe(self.checkout / ".git" / names.LANE_LOCK), lock.FREE)
        self.assertEqual(self.caller_state(), before)

    def test_the_next_run_stops_a_gate_a_killed_lane_left_running(self):
        self.open_pr(104, "feat/a")
        pid_file = self.gate_that_hangs()
        running = self.command("merge", "104")
        self.wait_for(lambda: pid_file.exists() and pid_file.read_text().strip(), "the gate")
        gate = int(pid_file.read_text())
        running.send_signal(signal.SIGKILL)
        running.communicate(timeout=60)
        self.assertTrue(lanes.alive(gate), "SIGKILL leaves the gate running")

        (self.control / "during-gate").unlink(missing_ok=True)
        again = self.command("merge", "104")
        stdout, stderr = again.communicate(timeout=120)

        self.assertEqual(again.returncode, 0, stderr)
        self.assertRegex(stdout, r"^lane: stopped the gate a stopped run left running \(pid \d+\)\.\n")
        self.wait_for(lambda: not lanes.alive(gate), "the gate to stop", seconds=15)

    def test_waits_for_a_legacy_lock_while_another_tool_holds_it(self):
        self.open_pr(104, "feat/a")
        legacy = self.checkout / ".git" / "old-lane.lock"
        held = lock.take(legacy)
        try:
            running = self.command("merge", "104", project=self.project(legacy_locks=("old-lane.lock",)))
            self.assertEqual(running.stdout.readline(), "lane: another merge is running; #104 waits for its turn.\n")
            self.assertEqual(self.gate_calls(), [])
        finally:
            held.close()
        running.communicate(timeout=120)
        self.assertEqual(running.returncode, 0)


if __name__ == "__main__":
    unittest.main()
