"""Running git in a folder: its output, or an error that says what failed."""


class GitError(Exception):
    def __init__(self, args, result):
        self.result = result
        detail = (result.stderr or "").strip()
        super().__init__("git %s exited %s%s" % (" ".join(args), result.returncode, ": " + detail if detail else ""))


class Git:
    """`run(words, cwd=None, input=None, extra=None)` is how a program runs: the host's."""

    def __init__(self, path, run):
        self.path = path
        self.runner = run

    def run(self, folder, *args, accepted=(0,), input=None, extra=None):
        """The result of `git -C <folder> <args>`; GitError on an exit code not accepted."""
        result = self.runner([self.path, "-C", str(folder), *args], input=input, extra=extra)
        if result.returncode not in accepted:
            raise GitError(args, result)
        return result

    def out(self, folder, *args, **options):
        return self.run(folder, *args, **options).stdout

    def line(self, folder, *args):
        return self.out(folder, *args).strip()

    def ok(self, folder, *args):
        """Whether git exits 0, without raising."""
        return self.runner([self.path, "-C", str(folder), *args]).returncode == 0


def paths(stdout):
    """The paths git prints with -z."""
    return [path for path in stdout.split("\0") if path]
