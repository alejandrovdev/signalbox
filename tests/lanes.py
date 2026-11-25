"""A made-up GitHub for the merge lane: a bare `origin` with a `main`, a clone as the caller's
checkout, a stub `gh` that answers `pr view` from the test's JSON, merges on the bare origin
(honoring `--match-head-commit`) and records every call, and a gate committed in the made-up
repository that records what it checked and passes or fails as the test's control files say.
`git` is the real one.
"""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

from signalbox import config, repository
from tests import repos

GATE = """#!/bin/sh
ctrl="$LANE_TEST_CONTROL"
printf '%s %s %s %s\\n' "$(git rev-parse 'HEAD^{tree}')" "$(git rev-parse 'HEAD^1')" \\
  "$(git rev-parse 'HEAD^2')" "${CHECK_WORKERS:-none}" >> "$ctrl/gate-calls"
if [ -x "$ctrl/during-gate" ]; then "$ctrl/during-gate"; fi
if [ -e "$ctrl/gate-fail" ]; then echo 'check: FAILED at "Lint" (exit 1).' >&2; echo "the failing test's message"; exit 1; fi
echo "check: every check passed."
"""

GH = """#!%(python)s
import json, os, subprocess, sys
ctrl = os.environ["LANE_TEST_CONTROL"]
origin = os.environ["LANE_TEST_ORIGIN"]
args = sys.argv[1:]
with open(os.path.join(ctrl, "gh-calls"), "a") as calls:
    calls.write("\\x1f".join(args) + "\\n")
identity = dict(os.environ, GIT_AUTHOR_NAME="GitHub", GIT_AUTHOR_EMAIL="noreply@example.com",
                GIT_COMMITTER_NAME="GitHub", GIT_COMMITTER_EMAIL="noreply@example.com")
def og(*words):
    return subprocess.run(["git", "--git-dir", origin, *words], capture_output=True, text=True, env=identity)
def control(name):
    return os.path.join(ctrl, name)
if args[:2] == ["pr", "view"]:
    path = control("pr-%%s.json" %% args[2])
    if not os.path.exists(path):
        sys.exit("GraphQL: Could not resolve to a PullRequest with the number of %%s." %% args[2])
    print(open(path).read())
    sys.exit(0)
if args[:2] == ["pr", "merge"]:
    pr = json.load(open(control("pr-%%s.json" %% args[2])))
    match = args[args.index("--match-head-commit") + 1] if "--match-head-commit" in args else ""
    tip = og("rev-parse", "refs/heads/" + pr["headRefName"]).stdout.strip()
    if match and match != tip:
        sys.exit("GraphQL: Head branch was modified. Review and try the merge again. (mergePullRequest)")
    main = og("rev-parse", "refs/heads/main").stdout.strip()
    tree = og("merge-tree", "--write-tree", main, tip)
    if tree.returncode != 0:
        sys.exit("GraphQL: Pull Request is not mergeable")
    parents = ["-p", main] if "--squash" in args else ["-p", main, "-p", tip]
    commit = og("commit-tree", tree.stdout.split()[0], *parents, "-m", "Merge pull request #" + args[2]).stdout.strip()
    og("update-ref", "refs/heads/main", commit, main)
    if os.access(control("after-merge"), os.X_OK):
        subprocess.run([control("after-merge")])
    sys.exit(0)
if args[:1] == ["api"]:
    if os.path.exists(control("api-fail")):
        sys.exit("HTTP 403: Resource not accessible by integration")
    if args[1:2] == ["repos/{owner}/{repo}"]:
        print(json.dumps({"delete_branch_on_merge": not os.path.exists(control("keep-merged-branches"))}))
        sys.exit(0)
    print("{}")
    sys.exit(0)
sys.exit("stub gh: unexpected call: " + " ".join(args))
"""

PUSH_COMMIT = """#!/bin/sh
set -e
g() { git -C "$LANE_TEST_SEED" -c user.name=Test -c user.email=test@example.com -c commit.gpgsign=false "$@"; }
g fetch --quiet origin
g checkout --quiet -B "$1" "origin/${4:-$1}"
printf '%s\\n' "$3" >> "$LANE_TEST_SEED/$2"
g add -A
g commit --quiet -m "$3"
g push --quiet origin "$1"
"""

VIEW_FIELDS = "number,state,isDraft,baseRefName,headRefName,headRefOid,isCrossRepository"
FAILED_STEP = 'check: FAILED at "(?P<step>[^"]+)"'


class Lane(repos.Scratch):
    def setUp(self):
        super().setUp()
        self.control = self.tmp / "control"
        self.control.mkdir()
        self.origin = self.tmp / "origin.git"
        self.seed = self.tmp / "seed"
        self.checkout = self.tmp / "checkout"
        self.environ.update(
            LANE_TEST_CONTROL=str(self.control), LANE_TEST_ORIGIN=str(self.origin), LANE_TEST_SEED=str(self.seed)
        )
        self.stub("gh", "").write_text(GH % {"python": sys.executable})
        self.stub("push-commit", PUSH_COMMIT[len("#!/bin/sh\n") :])
        self.git(self.tmp, "init", "--quiet", "--bare", "--initial-branch", "main", str(self.origin))
        self.seed.mkdir()
        self.git(self.seed, "init", "--quiet", "--initial-branch", "main")
        self.write(self.seed / "scripts/check.sh", GATE)
        self.write(self.seed / "README.md", "Made-up repository\n")
        self.write(self.seed / ".gitignore", "node_modules/\n")
        self.git(self.seed, "add", ".")
        self.git(self.seed, "commit", "--quiet", "--message", "start")
        self.git(self.seed, "remote", "add", "origin", str(self.origin))
        self.git(self.seed, "push", "--quiet", "origin", "main")
        self.git(self.tmp, "clone", "--quiet", str(self.origin), str(self.checkout))
        # The caller's checkout: another branch, a modified file and a staged one.
        self.git(self.checkout, "checkout", "--quiet", "-b", "work")
        self.write(self.checkout / "README.md", "Made-up repository\nlocal edit\n")
        self.write(self.checkout / "staged.txt", "staged\n")
        self.git(self.checkout, "add", "staged.txt")
        self.lane_worktree = self.tmp / "cache" / "shop" / "merge.noindex"
        self.err_printed = []

    # the made-up GitHub

    def origin_rev(self, ref):
        return self.git(self.origin, "rev-parse", ref).strip()

    def push_commit(self, branch, file, line, base=None):
        done = subprocess.run(
            [str(self.bin / "push-commit"), branch, file, line, *([base] if base else [])],
            env=self.environ,
            capture_output=True,
            text=True,
        )
        self.assertEqual(done.returncode, 0, done.stderr)
        return self.origin_rev("refs/heads/" + branch)

    def open_pr(self, number, branch, **overrides):
        """Push a branch with one commit over main and describe it as pull request `number`."""
        head = self.push_commit(branch, "%s.txt" % branch.replace("/", "-"), branch, "main")
        self.describe(number, branch, head, **overrides)
        return head

    def describe(self, number, branch, head, **overrides):
        pull = {
            "number": number,
            "state": "OPEN",
            "isDraft": False,
            "baseRefName": "main",
            "headRefName": branch,
            "headRefOid": head,
            "isCrossRepository": False,
        }
        pull.update(overrides)
        (self.control / ("pr-%d.json" % number)).write_text(json.dumps(pull))

    def on_event(self, name, body, once=True):
        """A script the stubs run: `during-gate` from the gate, `after-merge` from gh pr merge."""
        path = self.control / name
        path.write_text("#!/bin/sh\n" + ('rm -f -- "$0"\n' if once else "") + body + "\n")
        path.chmod(0o755)

    def gh_calls(self):
        path = self.control / "gh-calls"
        return [line.split("\x1f") for line in path.read_text().splitlines()] if path.exists() else []

    def gate_calls(self):
        path = self.control / "gate-calls"
        if not path.exists():
            return []
        return [dict(zip(("tree", "base", "head", "workers"), line.split())) for line in path.read_text().splitlines()]

    def records(self):
        path = self.tmp / "logs" / "shop" / "lane.jsonl"
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def caller_state(self):
        return {
            "branch": self.git(self.checkout, "symbolic-ref", "HEAD"),
            "head": self.git(self.checkout, "rev-parse", "HEAD"),
            "status": self.git(self.checkout, "status", "--porcelain=v1", "--untracked-files=all"),
            "diff": self.git(self.checkout, "diff"),
            "staged": self.git(self.checkout, "diff", "--cached"),
        }

    # the project and the lane

    def project(self, **values):
        values.setdefault("gate", "sh scripts/check.sh")
        values.setdefault("gate_env", (("CHECK_WORKERS", "3"),))
        values.setdefault("gate_failed_step", FAILED_STEP)
        features = values.pop("features", tuple(config.FEATURES))
        return config.Project("shop", str(self.checkout), features, **values)

    def err(self, line=""):
        self.err_printed.append(line)

    def lane(self, number, project=None, **options):
        """Run the lane in this process, from the caller's checkout; (code, stdout, stderr)."""
        from signalbox.features import lane

        self.printed, self.err_printed = [], []
        opened = repository.open(project or self.project(), self.host, self.checkout)
        code = lane.merge(opened, self.host, self.out, self.err, number, caller=[self.checkout], **options)
        return code, self.text(), "".join(line + "\n" for line in self.err_printed)

    def config_file(self, project=None):
        path = Path(self.environ["SIGNALBOX_CONFIG"])
        config.save(config.Config((project or self.project(),)), path)
        return path

    def command(self, *words, project=None):
        """`signalbox <words>` as another process, from the caller's checkout."""
        self.config_file(project)
        environ = dict(self.environ, PYTHONPATH=str(Path(__file__).resolve().parent.parent))
        return subprocess.Popen(
            [sys.executable, "-m", "signalbox", *words],
            cwd=self.checkout,
            env=environ,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

    def wait_for(self, condition, what, seconds=60):
        deadline = time.time() + seconds
        while not condition():
            if time.time() > deadline:
                raise AssertionError("timed out waiting for " + what)
            time.sleep(0.05)


def view_call(number):
    return ["pr", "view", str(number), "--json", VIEW_FIELDS]


def status_call(head, state, description):
    return [
        "api",
        "--method",
        "POST",
        "repos/{owner}/{repo}/statuses/" + head,
        "-f",
        "state=" + state,
        "-f",
        "context=signalbox/merge-lane",
        "-f",
        "description=" + description,
    ]


def merge_call(number, head, method="merge"):
    return ["pr", "merge", str(number), "--" + method, "--match-head-commit", head]


SETTING_CALL = ["api", "repos/{owner}/{repo}"]


def alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True
