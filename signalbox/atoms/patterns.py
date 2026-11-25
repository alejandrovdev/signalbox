"""Path patterns, as the configuration writes them: `*` and `?` stay within one folder, `**`
crosses folders. A pattern with no slash matches a name anywhere (`node_modules`,
`*.tsbuildinfo`); one with a slash matches the path from the top (`docs/features/*.md`).
"""

import re
from functools import lru_cache


@lru_cache(maxsize=256)
def compiled(pattern):
    parts, index = [], 0
    while index < len(pattern):
        char = pattern[index]
        if pattern.startswith("**", index):
            parts.append(".*")
            index += 2
            continue
        parts.append("[^/]*" if char == "*" else "[^/]" if char == "?" else re.escape(char))
        index += 1
    return re.compile("".join(parts))


def matches(path, pattern):
    path = path.rstrip("/")
    if "/" not in pattern.rstrip("/"):
        return any(compiled(pattern).fullmatch(name) for name in path.split("/"))
    return compiled(pattern.strip("/")).fullmatch(path) is not None


def any_match(path, patterns):
    return any(matches(path, pattern) for pattern in patterns)
