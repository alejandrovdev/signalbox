"""The merge guard: finds, in a command a session is about to run, a merge of a pull request
that skips the lane: `gh pr merge`, or a GitHub API call with PUT on `pulls/<number>/merge`.

    direct_merge(command)   the pull request number it names, "<number>" when it names none,
                            or None when the command merges nothing
    refusal(number)         what the session is told

It reads commands as the shell splits them, so `gh pr merge` inside a quoted argument (a
commit message, a search pattern, an `echo`) passes, and it finds one after `&&`, `;`, `|`,
a newline, `env` or variable assignments, `timeout`, and inside `bash -c '...'`, also with
`gh`'s own `-R`/`--repo` before the subcommand. Asking is not merging: `gh pr merge --help`
and a GET of `pulls/<number>/merge` pass. It is guidance, not a security boundary: `xargs`,
`eval`, a GraphQL mutation, a `gh` alias or an interpreter get around it.
"""

import re

from .. import names

PREFIXES = {"!", "{", "}", "if", "then", "else", "elif", "do", "while", "until", "time", "command", "exec", "nohup"}
SHELLS = {"bash", "sh", "zsh"}
ENV_VALUE_OPTIONS = {"-u", "--unset", "-C", "--chdir", "-P"}
MERGE_VALUE_OPTIONS = {
    "-A",
    "--author-email",
    "-b",
    "--body",
    "-F",
    "--body-file",
    "--match-head-commit",
    "-t",
    "--subject",
    "-R",
    "--repo",
}
GH_VALUE_OPTIONS = {"-R", "--repo", "--hostname"}  # gh's own options, wherever they stand
TIMEOUT_VALUE_OPTIONS = {"-s", "--signal", "-k", "--kill-after"}
MERGE_PATH = re.compile(r"(?:^|/)pulls/(\d+)/merge(?:[/?#]|$)")
ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
DEPTH = 5


def refusal(number):
    return "Merge through the lane: %s. A direct gh pr merge skips the check of the merged result." % names.command(
        "merge", number
    )


def asks_for_put(args):
    """Whether an API call's arguments ask for PUT, the method that merges; a GET only asks."""
    for index, arg in enumerate(args):
        if re.fullmatch(r"(-X|--method=|--request=)PUT", arg, re.I):
            return True
        if arg in ("-X", "--method", "--request") and index + 1 < len(args) and args[index + 1].upper() == "PUT":
            return True
    return False


def simple_commands(line):
    """A command line split into simple commands, each a list of words with quotes removed."""
    commands, words, word = [], [], None
    index = 0

    def end_word():
        nonlocal word
        if word is not None:
            words.append(word)
        word = None

    def end_command():
        nonlocal words
        end_word()
        if words:
            commands.append(words)
        words = []

    while index < len(line):
        char = line[index]
        if char in " \t":
            end_word()
        elif char in "\n;&|()`" or (char == "$" and line[index + 1 : index + 2] == "("):
            end_command()
        elif char == "#" and word is None:
            while index + 1 < len(line) and line[index + 1] != "\n":
                index += 1
        elif char == "'":
            end = line.find("'", index + 1)
            stop = len(line) if end == -1 else end
            word = (word or "") + line[index + 1 : stop]
            index = stop
        elif char == '"':
            word = word or ""
            index += 1
            while index < len(line) and line[index] != '"':
                following = line[index + 1 : index + 2]
                if line[index] == "\\" and following == "\n":
                    index += 1
                elif line[index] == "\\" and following and following in '"\\$`':
                    index += 1
                    word += line[index]
                else:
                    word += line[index]
                index += 1
        elif char == "\\":
            following = line[index + 1 : index + 2]
            if following != "\n":
                word = (word or "") + following
            index += 1
        else:
            word = (word or "") + char
        index += 1
    end_command()
    return commands


def command_name(word):
    return word[word.rfind("/") + 1 :]


def merge_in_words(words, depth):
    index = 0
    while index < len(words):
        if words[index] in PREFIXES or ASSIGNMENT.match(words[index]):
            index += 1
        elif command_name(words[index]) == "env":
            index += 1
            while index < len(words):
                if words[index] in ENV_VALUE_OPTIONS:
                    index += 1
                elif not words[index].startswith("-") and not ASSIGNMENT.match(words[index]):
                    break
                index += 1
        else:
            break
    if index >= len(words):
        return None
    name = command_name(words[index])
    args = words[index + 1 :]

    if name in SHELLS:
        option = next((at for at, arg in enumerate(args) if re.fullmatch(r"-[a-zA-Z]*c[a-zA-Z]*", arg)), None)
        if option is None or option + 1 >= len(args):
            return None
        return direct_merge(args[option + 1], depth + 1) if depth < DEPTH else None
    if name in ("timeout", "gtimeout"):
        at = 0
        while at < len(args) and args[at].startswith("-"):
            at += 2 if args[at] in TIMEOUT_VALUE_OPTIONS else 1
        # After the options comes the duration, then the command it runs.
        return merge_in_words(args[at + 1 :], depth + 1) if depth < DEPTH else None
    if name in ("curl", "wget"):
        url = next((arg for arg in args if "api.github.com/" in arg and MERGE_PATH.search(arg)), None)
        return MERGE_PATH.search(url).group(1) if url and asks_for_put(args) else None
    if name != "gh":
        return None

    positional, at = [], 0
    while at < len(args):
        if args[at] in GH_VALUE_OPTIONS:
            at += 1
        elif not args[at].startswith("-"):
            positional.append(args[at])
        at += 1
    if positional[:1] == ["api"]:
        path = next((arg for arg in positional if MERGE_PATH.search(arg)), None)
        return MERGE_PATH.search(path).group(1) if path and asks_for_put(args) else None
    if positional[:2] != ["pr", "merge"]:
        return None
    rest = args[args.index("merge") + 1 :]
    if "--help" in rest or "-h" in rest:
        return None
    at = 0
    while at < len(rest):
        if rest[at] in MERGE_VALUE_OPTIONS:
            at += 1
        elif not rest[at].startswith("-"):
            number = re.fullmatch(r"#?(\d+)", rest[at]) or re.search(r"/pull/(\d+)(?:[/?#]|$)", rest[at])
            return number.group(1) if number else "<number>"
        at += 1
    return "<number>"


def direct_merge(command, depth=0):
    for words in simple_commands(command):
        number = merge_in_words(words, depth)
        if number is not None:
            return number
    return None
