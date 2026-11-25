"""JSON-lines logs: one JSON object a line, appended. Writing one never fails its caller.

    read(path)           (the objects in file order, how many lines were skipped)
    append(path, record) add one line; False when it could not be written
"""

import json
from pathlib import Path


def read(path):
    """The JSON objects of a log, in file order. A missing log has none; a line that is not a
    JSON object is skipped and counted."""
    try:
        text = Path(path).read_text(encoding="utf8")
    except FileNotFoundError:
        return [], 0
    except (OSError, UnicodeDecodeError):
        return [], 0
    records, skipped = [], 0
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            parsed = json.loads(line)
        except ValueError:
            parsed = None
        if isinstance(parsed, dict):
            records.append(parsed)
        else:
            skipped += 1
    return records, skipped


def append(path, record):
    """Append one record as a line; whether it was written. Best effort: never raises."""
    try:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
        with open(path, "a", encoding="utf8") as handle:
            handle.write(line)
        return True
    except (OSError, TypeError, ValueError):
        return False
