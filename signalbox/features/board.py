"""The board: what every worktree of a repository changes, how far each is from the base,
which files two of them change at once, and whether the merge lane is busy. Computed from
git on every call, so nobody maintains it; it never fetches.

    status(repository, host, out, as_json, github)   print the board; 0
    group_status(name, opened, host, out, as_json, github)
                                                     the board of each project of a group,
                                                     in its order; 1 when one cannot be read
    feature_documents(files, patterns)               the feature documents among changed files
    open_pull_requests(repository, host)             branch: number from gh, or None
"""

import json
import os
from pathlib import PurePosixPath

from .. import names
from ..atoms import lock, logs, patterns, worktrees

LAST_RUNS = 3


def feature_documents(files, globs):
    """The names (without extension) of the changed files the feature-document globs match."""
    return [PurePosixPath(file).stem for file in sorted(files) if patterns.any_match(file, globs)]


def open_pull_requests(repository, host):
    """The open pull request of each branch from gh; None when gh is missing or fails."""
    gh = host.gh()
    if gh is None:
        return None
    done = host.run([gh, "pr", "list", "--state", "open", "--json", "number,headRefName"], cwd=repository.top)
    if done.returncode != 0:
        return None
    try:
        listed = json.loads(done.stdout)
    except ValueError:
        return None
    if not isinstance(listed, list):
        return None
    return {pull["headRefName"]: pull["number"] for pull in listed if isinstance(pull, dict) and "headRefName" in pull}


def lane_state(repository, host):
    """The merge lane: whether its lock is held, and its last runs, newest first."""
    records, _ = logs.read(names.lane_log(repository.project.name, host.environ))
    return {
        "lock": lock.probe(os.path.join(repository.common_dir, names.LANE_LOCK)),
        "lastRuns": list(reversed(records[-LAST_RUNS:])),
    }


def report(repository, host, github=True):
    project = repository.project
    found = worktrees.all_facts(repository.git, repository.top, repository.tip, last_commit=True)
    pulls = open_pull_requests(repository, host) if github else None
    shared = worktrees.shared_files(found)
    now = host.clock()
    rows = []
    for one in found:
        row = {
            "branch": one.branch,
            "featureDocuments": feature_documents(one.files, project.feature_docs),
            "behind": one.behind,
            "ahead": one.ahead,
            "changedFiles": len(one.files),
            "conflictsWithBase": one.conflicts,
            "hoursSinceLastCommit": int((now - one.last_commit) // 3600),
        }
        if pulls is not None:
            row["pullRequest"] = pulls.get(one.branch)
        row["sharedFiles"] = [
            {"file": file, "branches": [branch for branch in shared[file] if branch != one.branch]}
            for file in sorted(one.files)
            if file in shared
        ]
        rows.append(row)
    result = {"base": repository.remote_base, "worktrees": rows}
    if project.has("merge-lane"):
        result["mergeLane"] = lane_state(repository, host)
    return result, pulls is not None


def cell(value):
    return "n/a" if value is None else str(value)


def table(head, rows):
    return [
        "| %s |" % " | ".join(head),
        "| %s |" % " | ".join("---" for _ in head),
    ] + ["| %s |" % " | ".join(cell(value) for value in row) for row in rows]


def markdown(project, result, with_pulls):
    lines = ["# Board of %s" % project.name, "", "## Worktrees", ""]
    head = ["Branch", "Feature documents", "Behind", "Ahead", "Changed files", "Conflicts with %s" % result["base"]]
    head.append("Hours since last commit")
    rows = []
    for row in result["worktrees"]:
        cells = [
            row["branch"],
            ", ".join(row["featureDocuments"]),
            row["behind"],
            row["ahead"],
            row["changedFiles"],
            "yes" if row["conflictsWithBase"] else "no",
            row["hoursSinceLastCommit"],
        ]
        if with_pulls:
            cells.append("" if row["pullRequest"] is None else "#%d" % row["pullRequest"])
        rows.append(cells)
    lines += table(head + (["Pull request"] if with_pulls else []), rows) + [""]
    lines += ["## Shared files", ""]
    shared = [
        [row["branch"], item["file"], ", ".join(item["branches"])] for row in result["worktrees"] for item in row["sharedFiles"]
    ]
    if shared:
        lines += table(["Branch", "File", "Also changed by"], shared) + [""]
    else:
        lines += ["No file is changed in two worktrees.", ""]
    lane = result.get("mergeLane")
    if lane is not None:
        lines += ["## Merge lane", "", "Lock: %s." % lane["lock"], ""]
        if lane["lastRuns"]:
            runs = [
                [run.get("startedAt"), run.get("pullRequest"), run.get("status"), run.get("waitSeconds"), run.get("gateSeconds"), run.get("attempts")]
                for run in lane["lastRuns"]
            ]
            lines += table(["Started", "Pull request", "Status", "Wait (s)", "Gate (s)", "Attempts"], runs) + [""]
        else:
            lines += ["No run in the lane log.", ""]
    return "\n".join(lines).rstrip("\n")


def status(repository, host, out, as_json=False, github=True):
    result, with_pulls = report(repository, host, github)
    out(json.dumps(result, indent=2) if as_json else markdown(repository.project, result, with_pulls))
    return 0


def group_status(name, opened, host, out, as_json=False, github=True):
    """`opened` holds, in the group's order, (project, its Repository or why it cannot be read)."""
    boards, failed = [], False
    for project, repository in opened:
        if isinstance(repository, str):
            failed = True
            boards.append((project, {"error": repository}, False))
        else:
            result, with_pulls = report(repository, host, github)
            boards.append((project, result, with_pulls))
    if as_json:
        projects = [dict({"project": project.name}, **result) for project, result, _ in boards]
        out(json.dumps({"group": name, "projects": projects}, indent=2))
    else:
        parts = []
        for project, result, with_pulls in boards:
            if "error" in result:
                parts.append("# Board of %s\n\nNot read: %s" % (project.name, result["error"]))
            else:
                parts.append(markdown(project, result, with_pulls))
        out("\n\n".join(parts))
    return 1 if failed else 0
