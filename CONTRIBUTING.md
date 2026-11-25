# Contributing to signalbox

Thank you for helping. signalbox is small on purpose: the Python standard library only, a
macOS tool installed with Homebrew, and behavior a person can predict from the README.

## Before you write code

- For a bug, open an issue: `signalbox --version`, and the output of `signalbox doctor` and
  `signalbox status` in the repository where it happens, say most of what is needed.
- For a new feature or a change of behavior, open an issue first and describe the problem
  it solves for someone running several Claude Code sessions in one repository.
- Security problems go through [SECURITY.md](SECURITY.md), not public issues.

## Working on the code

```sh
git clone https://github.com/alejandrovdev/signalbox.git
cd signalbox
python3 -m unittest discover -s tests -t .
python3 -m signalbox --help
```

Python 3.11 or later, nothing to install. The tests build temporary repositories with real
git, put a stub `gh` and a stub gate first on `PATH`, and stand in for `ps` and `lsof`: they
need no network, no GitHub account and no terminal of yours.

To try a change without touching an install you use, follow
[A rehearsal apart from a real install](README.md#a-rehearsal-apart-from-a-real-install).

The package is built in layers (see [Development](README.md#development)): an atom knows
nothing of features, and a feature declares what it needs and does nothing when it is off.
Keep a new piece in the lowest layer that can hold it.

## How a change is made

- **Test first.** Write the test that shows the problem or the new rule, see it fail, then
  make it pass. Test through the public functions and assert every observable effect:
  output, exit code, files written, branches and worktrees left.
- **Keep what exists.** A change that touches a command or option others use keeps a test
  proving its old behavior still holds.
- **Docs travel with the code.** A new command, option, message or configuration key
  updates the help text, the README or `docs/`, and [CHANGELOG.md](CHANGELOG.md).
- **No dependencies.** Only the standard library, so Homebrew installs signalbox with its
  Python and nothing else.

## Commits and pull requests

- Commits follow [Conventional Commits](https://www.conventionalcommits.org/): `feat(sweep): ...`,
  `fix(merge-lane): ...`, `docs: ...`, written in English, in the imperative, saying what
  changes for the person using signalbox.
- One pull request per unit of behavior, with its tests and docs.
- Continuous integration runs the tests on macOS with Python 3.11 and 3.15; both must pass.
