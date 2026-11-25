"""The group merge: pull requests of several projects of one group, merged through each
project's lane in the group's order (its dependency order), stopping at the first that does
not merge. Not atomic across repositories: what merged stays merged.

    merge(group, runs, host, out, err, dry_run, allow_gate_change, caller)   exit code

`runs` holds (repository, pull request number) pairs in the group's order. Every pull request
is read first with its own project's `gh pr view`; when any would be refused (closed, a draft,
on another base, from a fork) or cannot be read, each is named and nothing runs: nothing is
fetched, merged or logged. Then each lane runs in its own repository, with its own lock,
gate and lane log (its record names the group). One closing line per pull request says
whether it merged, did not (with the lane's status) or was not tried. Exit codes: 0 when
every one merged (with dry_run: was checked), else the code of the run that did not.
"""

from . import lane

GROUP = "group: "


def label(repository, number):
    return "%s#%d" % (repository.project.name, number)


def refusals(runs, host, gh, err, allow_gate_change=False):
    """Read every pull request; whether any of them would be refused, each named. A pull
    request that changes its gate is looked for only once none is refused otherwise, so a
    refusal of that kind fetches nothing."""
    refused, pulls = False, []
    for repository, number in runs:
        pull, complaint = lane.read_pull(host, gh, repository.top, number)
        if pull is None:
            if complaint.strip():
                err(complaint.rstrip("\n"))
            problem = "gh pr view could not read it."
        else:
            problem = lane.refusal(pull, repository.base)
        if problem:
            refused = True
            err("%s%s refused: %s" % (GROUP, label(repository, number), problem))
        pulls.append(pull)
    if refused or allow_gate_change:
        return refused
    for (repository, number), pull in zip(runs, pulls):
        changed = gate_changes(repository, host, gh, err, number, pull)
        if changed:
            refused = True
            err("%s%s refused: %s" % (GROUP, label(repository, number), changed))
    return refused


def gate_changes(repository, host, gh, err, number, pull):
    """What a pull request changes of its gate, as the lane would refuse it; "" when nothing."""
    run = lane.Run(repository, host, lambda *line: None, err, number, False, False, ())
    run.gh_path = gh
    try:
        run.fetch_base("refs/heads/" + pull["headRefName"])
        head = pull["headRefOid"]
        if not repository.git.ok(repository.top, "cat-file", "-e", head + "^{commit}"):
            return ""  # its lane says so, in turn
        changed = run.gate_files(run.git("rev-parse", run.remote_base).stdout.strip(), head)
    except lane.Stop:
        return "its base and head could not be fetched."
    if not changed:
        return ""
    return "it changes the gate (%s), which would check itself. Review that change, then run again with --allow-gate-change." % ", ".join(changed)


def closing(status, dry_run):
    if status == "merged":
        return "merged"
    if status == "checked" and dry_run:
        return "checked, nothing merged (--dry-run)"
    if status is None:
        return "not tried"
    return "not merged (%s)" % status


def merge(group, runs, host, out, err, dry_run=False, allow_gate_change=False, caller=()):
    gh = host.gh()
    if gh is None:
        err("lane: gh is required but not installed: install it with: brew install gh")
        return 1
    if refusals(runs, host, gh, err, allow_gate_change):
        err(GROUP + "nothing was tried.")
        return 1
    out("%smerging in order: %s; what merges stays merged if a later one fails." % (GROUP, ", ".join(label(*run) for run in runs)))
    done = "checked" if dry_run else "merged"
    statuses, code = [], 0
    for repository, number in runs:
        run = lane.Run(repository, host, out, err, number, dry_run, allow_gate_change, caller, group.name)
        code = run.run()
        statuses.append(run.record["status"])
        if code != 0 or run.record["status"] != done:
            code = code or 1
            break
    statuses += [None] * (len(runs) - len(statuses))
    for (repository, number), status in zip(runs, statuses):
        out("%s%s %s" % (GROUP, label(repository, number), closing(status, dry_run)))
    return code
