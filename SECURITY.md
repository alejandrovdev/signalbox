# Security policy

signalbox runs as Claude Code hooks in every session, deletes merged worktrees and branches,
and merges pull requests, so a flaw in it can matter. Thank you for reporting one privately.

## Reporting a vulnerability

Use GitHub's private reporting: on the repository's **Security** tab, choose **Report a
vulnerability**. Please include the version (`signalbox --version`), what an attacker
controls (a branch or folder name, a pull request, a command a session runs, the
configuration), what they gain, and the steps to reproduce it.

Please do not open a public issue for a vulnerability. You can expect an answer within a
week, and a fix released as soon as it is ready; the changelog credits you unless you ask
otherwise.

## Supported versions

Only the latest release gets security fixes. Upgrade with `brew upgrade signalbox`.

## What signalbox does not guard

The merge guard is guidance, not a security boundary: it reads the commands a session is
about to run and sends a direct merge of a pull request to the lane, and a determined
command gets around it (see [The merge guard](README.md#the-merge-guard)). The gate is the
repository's own command, run as you; review a pull request that changes it before you
allow it.
