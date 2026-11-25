"""Tests of the sweep over temporary repositories with real git; `ps` and `lsof` are stand-ins."""

import json
import unittest

from signalbox import repository
from signalbox.features import sweep
from tests import repos
from tests.repos import hours_ago, lines

AGENT_STARTED_UTC = "Tue Nov 24 15:17:16 2026"
AGENT_STARTED_LOCAL = "Tue Nov 24 09:17:16 2026"


class Sweep(repos.Scratch):
    def setUp(self):
        super().setUp()
        self.claude_projects = self.home / ".claude" / "projects"
        self.environ["SIGNALBOX_CLAUDE_PROJECTS"] = str(self.claude_projects)

    def merge_into_main(self, repo, branch, date=None):
        """Merge a branch into main with a merge commit, as the lane does, and move origin/main.
        Dated after everything the worktree did: a merge follows the work it merges."""
        self.git(repo, "merge", "-q", "--no-ff", "-m", "Merge %s" % branch, branch, date=date or hours_ago(-1))
        self.git(repo, "update-ref", "refs/remotes/origin/main", "main")

    def merged_worktree(self, repo, name, branch):
        """A worktree on `branch` with one commit of its own, already merged into main."""
        folder = self.add_worktree(repo, name, branch)
        self.write(folder / ("%s.txt" % name), name)
        self.commit_all(folder, name, hours_ago(2))
        self.merge_into_main(repo, branch)
        return folder

    def branches(self, repo):
        return self.git(repo, "for-each-ref", "--format=%(refname)", "refs/heads", "refs/remotes").split()

    def sweep(self, folder, caller=None, project=None, **options):
        project = project or repos.project(self.tmp / "repo", features=("board", "sweep"))
        opened = repository.open(project, self.host, folder)
        code = sweep.sweep(opened, self.host, self.out, caller=caller or [folder], **options)
        self.assertEqual(code, 0)
        text, self.printed = self.text(), []
        return text

    def test_removes_a_clean_worktree_on_a_merged_branch_with_its_branch_and_remote_tracking_ref(self):
        repo = self.base_repo()
        self.write(repo / ".git/info/exclude", "node_modules/\n*.tsbuildinfo\n")
        done = self.merged_worktree(repo, "wt-done", "feat/done")
        self.git(repo, "update-ref", "refs/remotes/origin/feat/done", "feat/done")
        self.write(done / "node_modules/pkg/index.js", "")
        self.write(done / "tsconfig.tsbuildinfo", "{}")

        self.assertEqual(
            self.sweep(repo),
            "sweep: removed worktree %s (feat/done, merged into origin/main)\n"
            "sweep: deleted branch feat/done (merged into origin/main)\n" % done,
        )
        self.assertFalse(done.exists())
        self.assertNotIn("wt-done", self.git(repo, "worktree", "list", "--porcelain"))
        self.assertEqual(self.branches(repo), ["refs/heads/main", "refs/remotes/origin/main"])

    def test_passes_over_a_locked_worktree_whose_folder_is_gone(self):
        repo = self.base_repo()
        gone = self.merged_worktree(repo, "wt-gone", "feat/gone")
        self.git(repo, "worktree", "lock", str(gone))
        self.host.run(["rm", "-rf", str(gone)])
        done = self.merged_worktree(repo, "wt-done", "feat/done")
        self.assertIn("sweep: removed worktree %s" % done, self.sweep(repo))

    def test_the_build_output_is_the_projects(self):
        repo = self.base_repo()
        self.write(repo / ".git/info/exclude", "node_modules/\n.cache/\n")
        done = self.merged_worktree(repo, "wt-done", "feat/done")
        self.write(done / ".cache/x", "")
        self.write(done / "node_modules/x", "")
        project = repos.project(self.tmp / "repo", features=("sweep",), build_output=(".cache",))
        self.assertEqual(
            self.sweep(repo, project=project),
            "sweep: kept worktree %s (feat/done): ignored files that are not build output (node_modules)\n" % done,
        )

    def test_keeps_a_worktree_whose_branch_never_left_main_and_a_branch_that_is_not_merged(self):
        repo = self.base_repo()
        fresh = self.add_worktree(repo, "wt-fresh", "feat/fresh")
        opened = self.add_worktree(repo, "wt-open", "feat/open")
        self.write(opened / "open.txt", "open")
        self.commit_all(opened, "open", hours_ago(2))
        self.git(repo, "branch", "feat/unmerged", "feat/open")

        self.assertEqual(self.sweep(repo), "sweep: nothing to sweep.\n")
        self.assertTrue(fresh.exists())
        self.assertTrue(opened.exists())
        self.assertEqual(
            self.branches(repo),
            ["refs/heads/feat/fresh", "refs/heads/feat/open", "refs/heads/feat/unmerged", "refs/heads/main", "refs/remotes/origin/main"],
        )

    def test_deletes_a_merged_branch_no_worktree_holds_but_not_one_at_the_bases_tip_nor_the_base(self):
        repo = self.base_repo()
        self.git(repo, "branch", "old-cut")
        self.git(repo, "worktree", "remove", str(self.merged_worktree(repo, "wt-merged", "feat/merged")))
        # The main checkout moves to a branch with work of its own, and origin/main moves on:
        # local main is then merged, behind origin/main and held by no worktree.
        self.git(repo, "checkout", "-q", "-b", "work")
        self.write(repo / "work.txt", "work")
        self.commit_all(repo, "work", hours_ago(1))
        tip = self.git(repo, "commit-tree", "-p", "main", "-m", "main moves", "main^{tree}").strip()
        self.git(repo, "update-ref", "refs/remotes/origin/main", tip)
        self.git(repo, "branch", "just-cut", "refs/remotes/origin/main")

        self.assertEqual(
            self.sweep(repo),
            "sweep: deleted branch feat/merged (merged into origin/main)\n"
            "sweep: deleted branch old-cut (merged into origin/main)\n",
        )
        self.assertEqual(
            self.branches(repo), ["refs/heads/just-cut", "refs/heads/main", "refs/heads/work", "refs/remotes/origin/main"]
        )

    def test_keeps_a_remote_tracking_ref_that_holds_commits_the_base_does_not(self):
        repo = self.base_repo()
        done = self.merged_worktree(repo, "wt-done", "feat/done")
        self.write(done / "later.txt", "pushed after the merge")
        self.commit_all(done, "later", hours_ago(0.5))
        self.git(repo, "update-ref", "refs/remotes/origin/feat/done", "feat/done")
        self.git(done, "reset", "-q", "--hard", "HEAD^")

        self.sweep(repo)
        self.assertFalse(done.exists())
        self.assertEqual(self.branches(repo), ["refs/heads/main", "refs/remotes/origin/feat/done", "refs/remotes/origin/main"])

    def test_keeps_a_merged_worktree_it_must_not_take_and_says_why(self):
        repo = self.base_repo()
        self.write(repo / ".git/info/exclude", ".sync/\n")
        modified = self.merged_worktree(repo, "wt-modified", "feat/modified")
        self.write(modified / "README.md", lines({3: "not committed"}))
        untracked = self.merged_worktree(repo, "wt-untracked", "feat/untracked")
        self.write(untracked / "notes.txt", "untracked")
        locked = self.merged_worktree(repo, "wt-locked", "feat/locked")
        self.git(repo, "worktree", "lock", "--reason", "on a removable disk", str(locked))
        dump = self.merged_worktree(repo, "wt-dump", "feat/dump")
        self.write(dump / ".sync/local/dump.sql", "a database dump")
        busy = self.merged_worktree(repo, "wt-busy", "feat/busy")
        self.host.cwd_list = [(1, "/"), (4242, str(busy / "sub")), (4243, str(busy) + "-other")]
        own = self.merged_worktree(repo, "wt-own", "feat/own")
        before = self.branches(repo)

        self.assertEqual(
            self.sweep(own),
            "".join(
                # In `git worktree list` order, which is by path.
                line + "\n"
                for line in [
                    "sweep: kept worktree %s (feat/busy): a process runs in it (pid 4242)" % busy,
                    "sweep: kept worktree %s (feat/dump): ignored files that are not build output (.sync)" % dump,
                    "sweep: kept worktree %s (feat/locked): locked (on a removable disk)" % locked,
                    "sweep: kept worktree %s (feat/modified): uncommitted changes" % modified,
                    "sweep: kept worktree %s (feat/own): it holds the caller's directory; a sweep run from elsewhere removes it" % own,
                    "sweep: kept worktree %s (feat/untracked): uncommitted changes" % untracked,
                ]
            ),
        )
        for folder in (modified, untracked, locked, dump, busy, own):
            self.assertTrue(folder.exists())
        self.assertEqual(self.branches(repo), before)

    def test_keeps_every_merged_worktree_when_lsof_cannot_be_read(self):
        repo = self.base_repo()
        done = self.merged_worktree(repo, "wt-done", "feat/done")
        self.host.cwd_list = None
        self.assertEqual(
            self.sweep(repo), "sweep: kept worktree %s (feat/done): cannot tell whether a process runs in it (lsof failed)\n" % done
        )

    def test_never_removes_the_worktree_that_holds_the_callers_directory(self):
        repo = self.base_repo()
        done = self.merged_worktree(repo, "wt-done", "feat/done")
        inside = done / "deep" / "folder"
        inside.mkdir(parents=True)
        self.assertEqual(
            self.sweep(repo, caller=[repo, inside]),
            "sweep: kept worktree %s (feat/done): it holds the caller's directory; a sweep run from elsewhere removes it\n" % done,
        )
        self.assertTrue(done.exists())

    def test_keeps_a_worktree_somebody_used_after_its_branch_was_merged(self):
        repo = self.base_repo()
        done = self.add_worktree(repo, "wt-done", "feat/done")
        self.write(done / "done.txt", "done")
        self.commit_all(done, "done", hours_ago(3))
        self.merge_into_main(repo, "feat/done", hours_ago(1))
        self.git(repo, "worktree", "remove", str(done))
        # Cut now, from a commit main already holds: nothing in it yet, and not finished either.
        later = self.tmp / "wt-next"
        self.git(repo, "worktree", "add", "-q", "-b", "feat/next", str(later), "feat/done")

        self.assertEqual(
            self.sweep(repo),
            "sweep: kept worktree %s (feat/next): used since its branch was merged\n"
            "sweep: deleted branch feat/done (merged into origin/main)\n" % later,
        )
        self.assertTrue(later.exists())
        self.assertIn("refs/heads/feat/next", self.branches(repo))

    def test_keeps_the_main_checkout_on_a_merged_branch_with_its_branch(self):
        repo = self.base_repo()
        self.git(repo, "checkout", "-q", "-b", "feat/in-main")
        self.write(repo / "in-main.txt", "in main")
        self.commit_all(repo, "in main", hours_ago(2))
        tip = self.git(repo, "commit-tree", "-p", "main", "-p", "feat/in-main", "-m", "Merge", "feat/in-main^{tree}").strip()
        self.git(repo, "update-ref", "refs/remotes/origin/main", tip)
        elsewhere = self.tmp / "wt-elsewhere"
        self.git(repo, "worktree", "add", "-q", "-b", "feat/elsewhere", str(elsewhere), "main")

        self.assertEqual(self.sweep(elsewhere), "sweep: kept worktree %s (feat/in-main): the main checkout\n" % repo)
        self.assertIn("refs/heads/feat/in-main", self.branches(repo))
        self.assertIn("refs/heads/main", self.branches(repo))

    def agent_worktree(self, repo, agent, pid, branch=None):
        """A merged worktree locked as Claude Code locks an agent's."""
        branch = branch or "feat/%s" % agent
        folder = self.merged_worktree(repo, "wt-%s" % agent, branch)
        reason = "claude agent %s (pid %d start %s)" % (agent, pid, AGENT_STARTED_UTC)
        self.git(repo, "worktree", "lock", "--reason", reason, str(folder))
        return folder, reason

    def test_releases_an_agents_lock_once_its_process_is_gone_and_respects_it_while_it_runs(self):
        repo = self.base_repo()
        gone, _ = self.agent_worktree(repo, "agent-a1", 999999)
        live, reason = self.agent_worktree(repo, "agent-a2", 4242)
        # Claude Code writes the start date as `ps` prints it in UTC and the C locale.
        self.host.started = {4242: [AGENT_STARTED_UTC, AGENT_STARTED_LOCAL]}

        self.assertEqual(
            self.sweep(repo),
            "sweep: removed worktree %s (feat/agent-a1, merged into origin/main)\n"
            "sweep: kept worktree %s (feat/agent-a2): locked (%s)\n"
            "sweep: deleted branch feat/agent-a1 (merged into origin/main)\n" % (gone, live, reason),
        )
        self.assertFalse(gone.exists())
        self.assertTrue(live.exists())

    def test_an_agent_lock_read_in_the_local_zone_still_holds(self):
        repo = self.base_repo()
        folder, reason = self.agent_worktree(repo, "agent-a3", 4242)
        self.git(repo, "worktree", "unlock", str(folder))
        local = reason.replace(AGENT_STARTED_UTC, AGENT_STARTED_LOCAL)
        self.git(repo, "worktree", "lock", "--reason", local, str(folder))
        self.host.started = {4242: [AGENT_STARTED_UTC, AGENT_STARTED_LOCAL]}
        self.assertIn("locked (%s)" % local, self.sweep(repo))

    def agent_metadata(self, agent, folder, finished):
        path = self.claude_projects / "-some-project" / "a-session-id" / "subagents" / ("%s.meta.json" % agent)
        self.write(path, json.dumps({"worktreePath": str(folder), "worktreeBranch": "worktree-" + agent, "finished": finished}))

    def test_removes_a_finished_agents_worktree_while_its_parent_session_still_runs(self):
        repo = self.base_repo()
        finished, _ = self.agent_worktree(repo, "agent-a4", 4242)
        running, reason = self.agent_worktree(repo, "agent-a5", 4242)
        self.host.started = {4242: [AGENT_STARTED_UTC, AGENT_STARTED_LOCAL]}  # the parent session runs
        self.agent_metadata("agent-a4", finished, True)
        self.agent_metadata("agent-a5", running, False)

        self.assertEqual(
            self.sweep(repo),
            "sweep: removed worktree %s (feat/agent-a4, merged into origin/main)\n"
            "sweep: kept worktree %s (feat/agent-a5): locked (%s)\n"
            "sweep: deleted branch feat/agent-a4 (merged into origin/main)\n" % (finished, running, reason),
        )

    def test_a_finished_agents_metadata_that_names_another_worktree_does_not_release_the_lock(self):
        repo = self.base_repo()
        folder, reason = self.agent_worktree(repo, "agent-a6", 4242)
        self.host.started = {4242: [AGENT_STARTED_UTC]}
        self.agent_metadata("agent-a6", self.tmp / "elsewhere", True)
        self.assertEqual(self.sweep(repo), "sweep: kept worktree %s (feat/agent-a6): locked (%s)\n" % (folder, reason))

    def test_never_deletes_the_branch_a_running_agent_may_return_to(self):
        repo = self.base_repo()
        # The agent's worktree starts on worktree-agent-a7, at main; the agent then switches to
        # a branch of its own, so its first branch is merged and held by no worktree.
        folder = self.add_worktree(repo, "wt-agent-a7", "worktree-agent-a7")
        self.git(folder, "switch", "-q", "-c", "feat/a7")
        self.write(folder / "work.txt", "work")
        self.commit_all(folder, "work")
        reason = "claude agent agent-a7 (pid 4242 start %s)" % AGENT_STARTED_UTC
        self.git(repo, "worktree", "lock", "--reason", reason, str(folder))
        self.git(repo, "branch", "worktree-agent-a8")  # an agent's branch whose agent is gone
        self.write(repo / "later.txt", "main moves on")
        self.commit_all(repo, "later")
        self.git(repo, "update-ref", "refs/remotes/origin/main", "main")
        self.host.started = {4242: [AGENT_STARTED_UTC]}

        self.assertEqual(self.sweep(repo), "sweep: deleted branch worktree-agent-a8 (merged into origin/main)\n")
        self.assertIn("refs/heads/worktree-agent-a7", self.branches(repo))

        self.host.started = {}  # the agent's session ended: nobody returns to it
        self.assertIn("sweep: deleted branch worktree-agent-a7 (merged into origin/main)\n", self.sweep(repo))

    def test_takes_a_branch_the_lane_merged_with_squash(self):
        repo = self.base_repo()
        done = self.add_worktree(repo, "wt-done", "feat/done")
        self.write(done / "done.txt", "done")
        self.commit_all(done, "done", hours_ago(2))
        head = self.git(done, "rev-parse", "HEAD").strip()
        self.git(repo, "merge", "-q", "--squash", "feat/done")
        self.git(repo, "commit", "-qm", "Squashed", date=hours_ago(-1))
        self.git(repo, "update-ref", "refs/remotes/origin/main", "main")
        self.git(repo, "update-ref", "refs/remotes/origin/feat/done", "feat/done")
        record = repos.lane_record(7, "2026-11-25T10:00:00Z", "merged")
        record.update(head=head, mergeCommit=self.git(repo, "rev-parse", "main").strip())
        self.lane_log([record])

        self.assertEqual(
            self.sweep(repo),
            "sweep: removed worktree %s (feat/done, merged into origin/main)\n"
            "sweep: deleted branch feat/done (merged into origin/main)\n" % done,
        )
        self.assertEqual(self.branches(repo), ["refs/heads/main", "refs/remotes/origin/main"])

    def test_dry_run_says_what_it_would_do_and_changes_nothing(self):
        repo = self.base_repo()
        done = self.merged_worktree(repo, "wt-done", "feat/done")
        self.git(repo, "branch", "old-cut", "main~1")
        before = self.branches(repo)
        self.assertEqual(
            self.sweep(repo, dry_run=True),
            "sweep: would remove worktree %s (feat/done, merged into origin/main)\n"
            "sweep: would delete branch feat/done (merged into origin/main)\n"
            "sweep: would delete branch old-cut (merged into origin/main)\n" % done,
        )
        self.assertTrue(done.exists())
        self.assertEqual(self.branches(repo), before)

    def test_json_reports_the_worktrees_removed_the_branches_deleted_and_what_it_kept(self):
        repo = self.base_repo()
        done = self.merged_worktree(repo, "wt-done", "feat/done")
        dirty = self.merged_worktree(repo, "wt-dirty", "feat/dirty")
        self.write(dirty / "notes.txt", "untracked")
        self.assertEqual(
            json.loads(self.sweep(repo, as_json=True)),
            {
                "dryRun": False,
                "removedWorktrees": [{"path": str(done), "branch": "feat/done"}],
                "deletedBranches": ["feat/done"],
                "kept": [{"path": str(dirty), "branch": "feat/dirty", "reason": "uncommitted changes"}],
            },
        )
        self.assertFalse(done.exists())

    def test_keeps_a_worktree_with_an_untracked_file_a_git_setting_hides_from_status(self):
        repo = self.base_repo()
        dirty = self.merged_worktree(repo, "wt-dirty", "feat/dirty")
        self.write(dirty / "notes.txt", "untracked")
        self.git(repo, "config", "status.showUntrackedFiles", "no")
        self.assertEqual(self.sweep(repo), "sweep: kept worktree %s (feat/dirty): uncommitted changes\n" % dirty)
        self.assertEqual((dirty / "notes.txt").read_text(), "untracked")

    def test_keeps_a_merged_worktree_when_no_reflog_says_when_it_was_last_used(self):
        repo = self.base_repo()
        self.git(repo, "config", "core.logAllRefUpdates", "false")
        done = self.merged_worktree(repo, "wt-done", "feat/done")
        self.assertEqual(
            self.sweep(repo), "sweep: kept worktree %s (feat/done): cannot tell when it was last used (no reflog)\n" % done
        )
        self.assertTrue(done.exists())


if __name__ == "__main__":
    unittest.main()
