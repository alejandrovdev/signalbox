"""Flow metrics: how work reaches the base while several sessions work at once. It reads the
merges on the base's first-parent line, the pull requests GitHub merged (through gh), the
merge lane's log and the worktrees open now, over a window of local days. No model is
called and nothing is fetched: the base is read as last fetched.

    report(repository, host, out, since, until, as_json, github)   print the metrics; 0
    parse_date(text)                                               a YYYY-MM-DD date

The window runs from the start of the local day `since` (default: 14 days before today) to
the start of the local day `until`, excluded (default: now). Percentiles use the nearest
rank on the sorted values, `sorted[ceil(p * n) - 1]`; a measure with nothing to measure is
null.
"""

import argparse
import datetime
import json
import math
import re

from .. import names
from ..atoms import logs, patterns, worktrees
from ..atoms.git import paths
from ..repository import Refusal

DEFAULT_DAYS = 14
TOP_FILES = 10
QUICK_MERGE_SECONDS = 120
DOCS_FOLDER = "docs/"
NO_GH = "gh is not installed"
DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


class BadDate(argparse.ArgumentTypeError, ValueError):
    """A date that is not YYYY-MM-DD; argparse prints its message as it is."""


def parse_date(text):
    """A YYYY-MM-DD date; BadDate (a ValueError) otherwise."""
    try:
        if DATE.fullmatch(text):
            return datetime.date.fromisoformat(text)
    except ValueError:
        pass
    raise BadDate("takes a date as YYYY-MM-DD: %s" % text)


# time


def zone(host):
    """The zone the host's TZ names; None for this machine's own."""
    name = host.environ.get("TZ", "").lstrip(":")
    if not name:
        return None
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo(name)
    except (ImportError, ValueError, OSError, LookupError):
        return None


def start_of(day, tz):
    """The start of a local day, in seconds."""
    if tz is None:
        return datetime.datetime(day.year, day.month, day.day).timestamp()
    return datetime.datetime(day.year, day.month, day.day, tzinfo=tz).timestamp()


def local(seconds, tz):
    found = datetime.datetime.fromtimestamp(seconds, tz)
    return found if tz is not None else found.astimezone()


def local_day(seconds, tz):
    return local(seconds, tz).strftime("%Y-%m-%d")


def iso(seconds):
    """A moment as an ISO timestamp in UTC, with milliseconds."""
    moment = datetime.datetime.fromtimestamp(seconds, datetime.timezone.utc)
    return moment.strftime("%Y-%m-%dT%H:%M:%S.") + "%03dZ" % (moment.microsecond // 1000)


def parsed_time(text):
    """An ISO timestamp in seconds, or None."""
    if not isinstance(text, str):
        return None
    try:
        moment = datetime.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.astimezone()
    return moment.timestamp()


def window_of(host, since, until):
    tz = zone(host)
    now = host.clock()
    if since is None:
        since = local(now, tz).date() - datetime.timedelta(days=DEFAULT_DAYS)
    start = start_of(since, tz)
    end = now if until is None else start_of(until, tz)
    if start >= end:
        raise Refusal("--since must come before --until")
    return start, end, tz


# numbers


def is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def percentile(values, share):
    if not values:
        return None
    return sorted(values)[math.ceil(share * len(values)) - 1]


def round2(value):
    return None if value is None else math.floor(value * 100 + 0.5) / 100


def spread(values):
    return {"median": percentile(values, 0.5), "p90": percentile(values, 0.9)}


def top_files(counts, key):
    """The files counted most often first, then by name, at most TOP_FILES."""
    ranked = sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:TOP_FILES]
    return [{"file": file, key: count} for file, count in ranked]


def sorted_keys(counts):
    return dict(sorted(counts.items()))


# merges


def documentation(files, feature_docs):
    """Whether every file is documentation: Markdown, under docs/, or a feature document. A
    merge that changes no file is not."""
    return bool(files) and all(
        file.endswith(".md") or file.startswith(DOCS_FOLDER) or patterns.any_match(file, feature_docs) for file in files
    )


def first_parent_merges(git, top, tip):
    """The merges on the first-parent line of `tip`, oldest first: (hash, time, first, second)."""
    found = []
    for line in git.out(top, "log", "--first-parent", "--merges", "--format=%H %ct %P", tip).splitlines():
        words = line.split()
        if len(words) >= 4:
            found.append((words[0], int(words[1]), words[2], words[3]))
    return list(reversed(found))


def measure_merges(repository, window, lane_commits):
    git, top = repository.git, repository.top
    start, end, tz = window

    def changed(old, new):
        return paths(git.out(top, "diff", "--name-only", "-z", old, new))

    every = first_parent_merges(git, top, repository.tip)
    per_day, shared = {}, {}
    total = stale = shared_merges = docs_only = quick = outside = 0
    max_behind = None
    for index, (commit, at, first, second) in enumerate(every):
        if at < start or at >= end:
            continue
        total += 1
        if commit not in lane_commits:
            outside += 1
        day = local_day(at, tz)
        per_day[day] = per_day.get(day, 0) + 1
        if index > 0 and at - every[index - 1][1] < QUICK_MERGE_SECONDS:
            quick += 1
        if documentation(changed(first, commit), repository.project.feature_docs):
            docs_only += 1
        if git.run(top, "merge-base", "--is-ancestor", first, second, accepted=(0, 1)).returncode == 0:
            continue
        stale += 1
        behind = int(git.line(top, "rev-list", "--count", "--first-parent", first, "--not", second))
        max_behind = max(max_behind or 0, behind)
        base = git.line(top, "merge-base", first, second)
        base_side = set(changed(base, first))
        both = [file for file in changed(base, second) if file in base_side]
        if both:
            shared_merges += 1
            for file in both:
                shared[file] = shared.get(file, 0) + 1
    return {
        "total": total,
        "perDay": sorted_keys(per_day),
        "stale": {"count": stale, "share": None if total == 0 else round2(stale / total), "maxBehind": max_behind},
        "staleSharedFiles": {"merges": shared_merges, "topFiles": top_files(shared, "merges")},
        "docsOnly": docs_only,
        "within2Minutes": quick,
        "outsideLane": outside,
    }


# pull requests


def measure_pull_requests(repository, host, window):
    """(the pull requests merged in the window, None) or (None, the error)."""
    gh = host.gh()
    if gh is None:
        return None, NO_GH
    done = host.run(
        [gh, "pr", "list", "--state", "merged", "--limit", "500", "--json", "number,createdAt,mergedAt"], cwd=repository.top
    )
    if done.returncode != 0:
        return None, (done.stderr or "").strip() or "gh exited with code %d" % done.returncode
    try:
        listed = json.loads(done.stdout)
    except ValueError as error:
        return None, "gh printed no JSON: %s" % error
    if not isinstance(listed, list):
        return None, "gh printed no list of pull requests"
    start, end, _ = window
    minutes = []
    for pull in listed:
        if not isinstance(pull, dict):
            continue
        created, merged = parsed_time(pull.get("createdAt")), parsed_time(pull.get("mergedAt"))
        if created is not None and merged is not None and start <= merged < end:
            minutes.append(int((merged - created) // 60))
    found = spread(minutes)
    found["max"] = max(minutes) if minutes else None
    return {"merged": len(minutes), "openToMergeMinutes": found}, None


# the merge lane


def lane_commits(records):
    """The merge commits the lane made: the `mergeCommit` of every `merged` record, in any window."""
    return {
        record["mergeCommit"]
        for record in records
        if record.get("status") == "merged" and isinstance(record.get("mergeCommit"), str)
    }


def started_in(record, window):
    at = parsed_time(record.get("startedAt"))
    return at is not None and window[0] <= at < window[1]


def measure_lane(records, skipped, window):
    """The lane's runs whose `startedAt` falls in the window."""
    runs = [record for record in records if started_in(record, window)]
    by_status, failed_steps = {}, {}
    for run in runs:
        status = run.get("status")
        if isinstance(status, str):
            by_status[status] = by_status.get(status, 0) + 1
        if status == "gate_failed" and isinstance(run.get("failedStep"), str):
            failed_steps[run["failedStep"]] = failed_steps.get(run["failedStep"], 0) + 1

    def numbers(chosen, key):
        return [run[key] for run in chosen if is_number(run.get(key))]

    attempts = numbers(runs, "attempts")
    return {
        "runs": len(runs),
        "byStatus": sorted_keys(by_status),
        "waitSeconds": spread(numbers(runs, "waitSeconds")),
        "gateSeconds": spread(numbers([run for run in runs if run.get("status") == "merged"], "gateSeconds")),
        "maxAttempts": max(attempts) if attempts else None,
        "failedSteps": sorted_keys(failed_steps),
        "skippedLines": skipped,
    }


# the worktrees now


def measure_live(repository):
    found = worktrees.all_facts(repository.git, repository.top, repository.tip, conflict=True)
    rows = [
        {"branch": one.branch, "behind": one.behind, "changedFiles": len(one.files), "conflictsWithBase": one.conflicts}
        for one in found
    ]
    in_several = {file: len(branches) for file, branches in worktrees.shared_files(found).items()}
    return {
        "worktrees": rows,
        "conflicting": sum(1 for row in rows if row["conflictsWithBase"]),
        "sharedFiles": {"count": len(in_several), "topFiles": top_files(in_several, "branches")},
    }


def measure(repository, host, since=None, until=None, github=True):
    window = window_of(host, since, until)
    records, skipped = logs.read(names.lane_log(repository.project.name, host.environ))
    pulls, error = measure_pull_requests(repository, host, window) if github else (None, None)
    return {
        "window": {"since": iso(window[0]), "until": iso(window[1])},
        "base": repository.remote_base,
        "merges": measure_merges(repository, window, lane_commits(records)),
        "pullRequests": pulls,
        "githubError": error,
        "lane": measure_lane(records, skipped, window),
        "live": measure_live(repository),
    }


# Markdown


def cell(value):
    return "n/a" if value is None else str(value)


def table(head, rows):
    return [
        "| %s |" % " | ".join(head),
        "| %s |" % " | ".join("---" for _ in head),
    ] + ["| %s |" % " | ".join(cell(value) for value in row) for row in rows]


def metric_table(rows):
    return table(["Metric", "Value"], rows) + [""]


def markdown(project, report, github):
    base, merges, lane, live = report["base"], report["merges"], report["lane"], report["live"]
    lines = ["# Flow metrics of %s" % project.name, "", "## Window", ""]
    lines += table(["Since", "Until"], [[report["window"]["since"], report["window"]["until"]]]) + [""]

    lines += ["## Merges", ""]
    lines += metric_table(
        [
            ["Total", merges["total"]],
            ["Stale", merges["stale"]["count"]],
            ["Stale share", merges["stale"]["share"]],
            ["Stale, most commits behind", merges["stale"]["maxBehind"]],
            ["Stale with files changed on both sides", merges["staleSharedFiles"]["merges"]],
            ["Documentation only", merges["docsOnly"]],
            ["Within 2 minutes of the previous one", merges["within2Minutes"]],
            ["Outside the merge lane", merges["outsideLane"]],
        ]
    )
    if merges["perDay"]:
        lines += table(["Day", "Merges"], list(merges["perDay"].items())) + [""]
    if merges["staleSharedFiles"]["topFiles"]:
        rows = [[item["file"], item["merges"]] for item in merges["staleSharedFiles"]["topFiles"]]
        lines += table(["File changed on both sides", "Stale merges"], rows) + [""]

    lines += ["## Pull requests", ""]
    pulls, error = report["pullRequests"], report["githubError"]
    if pulls is not None:
        minutes = pulls["openToMergeMinutes"]
        head = ["Merged", "Open to merge, median (min)", "p90 (min)", "max (min)"]
        lines += table(head, [[pulls["merged"], minutes["median"], minutes["p90"], minutes["max"]]]) + [""]
    elif error == NO_GH:
        lines += ["Not measured: %s." % NO_GH, ""]
    elif error is not None:
        lines += ["Not measured: gh failed: %s" % error, ""]
    elif not github:
        lines += ["Not measured (--no-github).", ""]

    lines += ["## Merge lane", ""]
    lines += metric_table(
        [
            ["Runs", lane["runs"]],
            ["Wait for the lock, median (s)", lane["waitSeconds"]["median"]],
            ["Wait for the lock, p90 (s)", lane["waitSeconds"]["p90"]],
            ["Gate of merged runs, median (s)", lane["gateSeconds"]["median"]],
            ["Gate of merged runs, p90 (s)", lane["gateSeconds"]["p90"]],
            ["Most attempts", lane["maxAttempts"]],
            ["Skipped log lines", lane["skippedLines"]],
        ]
    )
    if lane["byStatus"]:
        lines += table(["Status", "Runs"], list(lane["byStatus"].items())) + [""]
    if lane["failedSteps"]:
        lines += table(["Failed step", "Runs"], list(lane["failedSteps"].items())) + [""]

    lines += ["## Live", ""]
    rows = [
        [row["branch"], row["behind"], row["changedFiles"], "yes" if row["conflictsWithBase"] else "no"]
        for row in live["worktrees"]
    ]
    lines += table(["Branch", "Behind", "Changed files", "Conflicts with %s" % base], rows) + [""]
    lines += [
        "Worktrees conflicting with %s: %d. Files changed in two or more: %d."
        % (base, live["conflicting"], live["sharedFiles"]["count"]),
        "",
    ]
    if live["sharedFiles"]["topFiles"]:
        rows = [[item["file"], item["branches"]] for item in live["sharedFiles"]["topFiles"]]
        lines += table(["Shared file", "Branches"], rows) + [""]
    return "\n".join(lines).rstrip("\n")


def report(repository, host, out, since=None, until=None, as_json=False, github=True):
    found = measure(repository, host, since, until, github)
    out(json.dumps(found, indent=2) if as_json else markdown(repository.project, found, github))
    return 0
