"""The whole screen of a step, composed on a canvas without curses.

    View, view_of(sequence, version)     what a step shows, read from a sequence
    compose(view, width, height, tick)   the screen, or None when the terminal is too small

Two layouts, chosen by size, with everything on fixed rows so a screen does not jump from
one step to the next. Wide (WIDE columns and rows or more): the signal and the list of
steps on the left; the name in large pixels, a tagline, the answers so far and the step's
panels on the right. Roomy (ROOMY or more): the name, a line of steps and the panels.
Smaller terminals get None, and with it the plain layout. Both have a status bar on top
(the name, the step, a progress bar, the version) and the step's keys as chips at the
bottom. The signal changes aspect as a signal does, except where a step holds one: green
on the summary, red while a step shows an error.

The answers so far are the sequence's own, or what its recap makes of them: (label, value)
pairs, a value of None left out.

A choice or a checklist is a panel of aligned options, with a bar under the cursor and a
pointer that pulses, and under it a panel about the option the cursor is on: what it does,
what it needs, what needs it and the facts the checklist gives about it. A text field is an input line with a blinking caret; a
summary shows labels and values, then the question; the prerequisites get a mark each.
Colors are numbers of the 256-color palette; -1 is the terminal's own: a railway signal's,
green for the accents, amber for warnings, red for errors, and grays for text and borders.
"""

import re
import textwrap
from dataclasses import dataclass, field

from . import signal, wordmark
from .canvas import Canvas, Style
from .widgets import Checklist, Choice, Confirm, Summary, TextField

WIDE = (112, 32)  # columns, rows
ROOMY = (80, 24)
DONE, NOW, LATER = "done", "now", "later"
NAME = "signalbox"
TAGLINE = "One train per track for your Claude Code sessions."

ACCENT, BRIGHT, BORDER, LABEL = 41, 84, 240, 108
MUTED, FAINT, GOOD = 245, 240, 77
AMBER, RED = 214, 203
CURSOR_BG, CURSOR_FG, CURSOR_NOTE, ON_ACCENT = 237, 255, 250, 16
PULSE = (84, 41, 35, 41)  # the pointer's color, tick after tick

TEXT = Style()
STRONG = Style(bold=True)
MUTED_TEXT = Style(MUTED)
FAINT_TEXT = Style(FAINT)
ACCENT_TEXT = Style(ACCENT, bold=True)
TITLE = Style(BRIGHT, bold=True)
FRAME = Style(BORDER)
LABEL_TEXT = Style(LABEL)
GOOD_TEXT = Style(GOOD, bold=True)
BAD_TEXT = Style(RED, bold=True)
WARNING_TEXT = Style(AMBER, bold=True)
KEY = Style(ON_ACCENT, ACCENT, bold=True)

# The rows things sit on, in both layouts.
NAME_ROW = 2
WIDE_TAGLINE, WIDE_ANSWERS, WIDE_BODY = 8, 10, 13
ROOMY_TAGLINE, ROOMY_STEPS, ROOMY_BODY = 8, 9, 11
SIDE = 2 + signal.COLUMNS + 4  # where the wide layout's right column starts
WIDE_TALL, ROOMY_TALL = 34, 26  # the rows each layout is drawn for: a taller terminal centers it


@dataclass(frozen=True)
class View:
    widget: object
    number: int = 1
    total: int = 1
    steps: tuple = ()  # (title, DONE, NOW or LATER), in order
    answers: dict = field(default_factory=dict)  # label: value
    version: str = ""


def view_of(sequence, version=""):
    number, total = sequence.step()
    steps = []
    for index in sequence.shown:
        widget = sequence.asked(index, sequence.answers)
        if widget is not None:
            steps.append((widget.title, DONE))
    steps.append((sequence.widget.title, NOW))
    for index in range(sequence.index + 1, len(sequence.screens)):
        widget = sequence.asked(index, sequence.answers)
        if widget is not None:
            steps.append((widget.title, LATER))
    answers = dict(sequence.recap(sequence.answers)) if sequence.recap else dict(sequence.answers)
    return View(sequence.widget, number, total, tuple(steps), answers, version)


def compose(view, width, height, tick):
    if width >= WIDE[0] and height >= WIDE[1]:
        return wide(view, width, height, tick)
    if width >= ROOMY[0] and height >= ROOMY[1]:
        return roomy(view, width, height, tick)
    return None


def wide(view, width, height, tick):
    canvas = Canvas(width, height)
    status_bar(canvas, view)
    top = margin(height, WIDE_TALL)
    canvas.pixels(top + NAME_ROW, 2, see_through(signal.pixels(tick, held(view.widget))))
    step_list(canvas, top + NAME_ROW + signal.ROWS + 1, 3, height - 3, view.steps)
    span = width - 1 - SIDE - 1
    name(canvas, top + NAME_ROW, SIDE, span, tick)
    canvas.text(top + WIDE_TAGLINE, SIDE, TAGLINE, MUTED_TEXT, width=span)
    answer_grid(canvas, top + WIDE_ANSWERS, SIDE, span, view.answers)
    body(canvas, view.widget, top + WIDE_BODY, SIDE, height - 3, span, tick)
    keys(canvas, height - 1, 1, width - 3, view.widget.hint)
    return canvas


def roomy(view, width, height, tick):
    canvas = Canvas(width, height)
    status_bar(canvas, view)
    span = width - 1 - 2 - 1
    top = margin(height, ROOMY_TALL)
    name(canvas, top + NAME_ROW, 2, span, tick)
    canvas.text(top + ROOMY_TAGLINE, 2, TAGLINE, MUTED_TEXT, width=span)
    step_line(canvas, top + ROOMY_STEPS, 2, span, view.steps)
    body(canvas, view.widget, top + ROOMY_BODY, 2, height - 3, span, tick)
    keys(canvas, height - 1, 1, width - 3, view.widget.hint)
    return canvas


def margin(height, tall):
    """Rows to leave above a layout drawn for `tall` rows, so it sits in the middle."""
    return max(0, (height - tall) // 2)


def cut(text, width):
    """Text cut to a width, with an ellipsis when something was left out."""
    return text if len(text) <= width else text[: max(width - 1, 0)] + "…"


def see_through(grid):
    """The signal with its night left out: the terminal's background shows instead."""
    return [[None if color == signal.NIGHT else color for color in row] for row in grid]


def held(widget):
    """The aspect a step holds the signal at: red on an error, green on the summary; None
    lets it change."""
    if widget.error:
        return signal.RED
    if isinstance(widget, Confirm):
        return signal.GREEN
    return None


def status_bar(canvas, view):
    track = 12
    filled = round(track * view.number / max(view.total, 1))
    count = "%d/%d " % (view.number, view.total)
    version = "   v%s" % view.version if view.version else ""
    start = canvas.width - 2 - len(count) - track - len(version)
    column = canvas.text(0, 1, "◆ " + NAME, ACCENT_TEXT)
    column = canvas.text(0, column, "  ·  ", FAINT_TEXT)
    canvas.text(0, column, view.widget.title, STRONG, width=start - 2 - column)
    column = canvas.text(0, start, count, MUTED_TEXT)
    column = canvas.text(0, column, "━" * filled, Style(ACCENT))
    column = canvas.text(0, column, "━" * (track - filled), FAINT_TEXT)
    canvas.text(0, column, version, MUTED_TEXT)


def name(canvas, top, left, width, tick):
    if wordmark.width(NAME) <= width:
        canvas.pixels(top, left, wordmark.pixels(NAME, tick))
    else:
        canvas.text(top + 2, left, NAME, TITLE, width=width)


STEP_MARKS = {DONE: ("●", Style(GOOD), MUTED_TEXT), NOW: ("◉", ACCENT_TEXT, STRONG), LATER: ("○", FAINT_TEXT, FAINT_TEXT)}


def step_list(canvas, top, left, bottom, steps):
    """The steps down the left column, when they fit under the signal."""
    if top + len(steps) > bottom:
        return
    canvas.text(top, left, "STEPS", LABEL_TEXT)
    for offset, (title, state) in enumerate(steps):
        mark, mark_style, title_style = STEP_MARKS[state]
        column = canvas.text(top + 1 + offset, left, mark + " ", mark_style)
        canvas.text(top + 1 + offset, column, title, title_style, width=signal.COLUMNS - 3)


def step_line(canvas, row, left, width, steps):
    """The steps on one line; only the marks of the others when the titles do not fit."""
    parts = [(STEP_MARKS[state][0] + " " + title, state) for title, state in steps]
    if sum(len(text) + 3 for text, _ in parts) > width:
        parts = [(STEP_MARKS[state][0] + (" " + title if state == NOW else ""), state) for title, state in steps]
    column = left
    for number, (text, state) in enumerate(parts):
        if number:
            column = canvas.text(row, column, " › ", FAINT_TEXT)
        style = Style(GOOD) if state == DONE else STEP_MARKS[state][1]
        column = canvas.text(row, column, text, style, width=left + width - column)


def shown_answer(value):
    if isinstance(value, str):
        return value or None
    if isinstance(value, (tuple, list, frozenset, set)):
        return ", ".join(value) if value else "none"
    return None


def answer_grid(canvas, top, left, width, answers):
    """The answers so far as labels and values, two to a row, on two rows at most."""
    pairs = [(key.replace("-", " ").replace("_", " ").upper(), shown_answer(value)) for key, value in answers.items()]
    pairs = [pair for pair in pairs if pair[1]][-4:]
    if not pairs:
        canvas.text(top, left, "Nothing chosen yet: your answers show here as you go.", FAINT_TEXT, width=width)
        return
    half = width // 2
    for side in (0, 1):
        below = pairs[side::2]
        label_width = max([len(label) for label, _ in below] or [0])
        for number, (label, value) in enumerate(below):
            row, column = top + number, left + side * half
            canvas.text(row, column, label, LABEL_TEXT)
            canvas.text(row, column + label_width + 2, cut(value, half - label_width - 4), TEXT)


def body(canvas, widget, top, left, bottom, width, tick):
    if widget.error:
        canvas.text(bottom + 1, left, "✗ " + widget.error, BAD_TEXT, width=width)
    if isinstance(widget, (Choice, Checklist)):
        options(canvas, widget, top, left, bottom, width, tick)
    elif isinstance(widget, TextField):
        text_field(canvas, widget, top, left, bottom, width, tick)
    elif isinstance(widget, Confirm):
        confirm(canvas, widget, top, left, bottom, width)
    elif isinstance(widget, Summary):
        summary(canvas, widget, top, left, bottom, width)
    else:
        lines = [text for text, _ in widget.rows()]
        panel(canvas, widget.title, [[(text, TEXT)] for text in lines], top, left, bottom, width)


def panel(canvas, title, lines, top, left, bottom, width):
    """A titled box of lines of (text, style) runs; the lines that do not fit are left out."""
    height = min(len(lines) + 2, bottom - top + 1)
    canvas.box(top, left, height, width, FRAME, "◆ " + title, TITLE)
    for offset, runs in enumerate(lines[: height - 2]):
        column = left + 2
        for text, style in runs:
            column = canvas.text(top + 1 + offset, column, cut(text, left + width - 2 - column), style)
    return top + height


def marker(widget, item):
    if isinstance(widget, Checklist):
        return ("✓", GOOD_TEXT) if item.value in widget.checked else ("○", FAINT_TEXT)
    return ("◉", ACCENT_TEXT) if item.value == widget.current else ("○", FAINT_TEXT)


def on_cursor(style):
    return Style(style.fg, CURSOR_BG, style.bold)


def options(canvas, widget, top, left, bottom, width, tick):
    label_width = max(len(item.label) for item in widget.items)
    note_room = width - 4 - 4 - label_width - 2
    focused = widget.items[widget.cursor]
    about = details(widget, width - 4) if isinstance(widget, Checklist) or len(focused.note) > note_room else []
    room = bottom - top + 1
    want = len(widget.items) + 2
    about_height = min(len(about) + 2, 9) if about else 0
    if want + 1 + about_height <= room:
        list_height = want
    elif min(want, 7) + 1 + about_height <= room:
        list_height = room - 1 - about_height
    else:
        list_height, about_height = min(want, room - 1), 0
    option_panel(canvas, widget, top, left, list_height, width, tick)
    if about_height:
        panel(canvas, focused.label, about, top + list_height + 1, left, top + list_height + about_height, width)
    elif about:
        canvas.text(top + list_height, left + 1, cut("ℹ " + focused.note, width - 1), MUTED_TEXT)


def option_panel(canvas, widget, top, left, height, width, tick):
    items = widget.items
    inner = height - 2
    start = max(0, min(widget.cursor - inner // 2, len(items) - inner))
    shown = items[start : start + inner]
    hidden = [text % count for count, text in ((start, "↑ %d more"), (len(items) - start - len(shown), "↓ %d more")) if count]
    note = "  ".join(hidden)
    if not note and isinstance(widget, Checklist):
        note = "%d of %d on" % (len(widget.checked), len(items))
    canvas.box(top, left, height, width, FRAME, "◆ " + widget.title, TITLE, note, MUTED_TEXT)
    label_width = max(len(item.label) for item in items)
    for offset, item in enumerate(shown):
        row, here = top + 1 + offset, start + offset == widget.cursor
        if here:
            canvas.fill(row, left + 1, width - 2, Style(bg=CURSOR_BG))
        pointer = ("▸ ", Style(PULSE[tick % len(PULSE)], CURSOR_BG, bold=True)) if here else ("  ", TEXT)
        mark, mark_style = marker(widget, item)
        runs = [
            pointer,
            (mark + " ", on_cursor(mark_style) if here else mark_style),
            (item.label.ljust(label_width) + "  ", Style(CURSOR_FG, CURSOR_BG, bold=True) if here else TEXT),
            (item.note, Style(CURSOR_NOTE, CURSOR_BG) if here else MUTED_TEXT),
        ]
        column = left + 2
        for text, style in runs:
            column = canvas.text(row, column, cut(text, left + width - 2 - column), style)


def details(widget, width):
    """What the panel under the options says about the one the cursor is on."""
    item = widget.items[widget.cursor]
    lines = [[(text, TEXT)] for text in textwrap.wrap(item.note, max(width, 1))] or [[("No more to say about it.", FAINT_TEXT)]]
    if isinstance(widget, Checklist):
        labels = {each.value: each.label for each in widget.items}
        needs = [labels.get(value, value) for value in widget.needs.get(item.value, ())]
        needed_by = [each.label for each in widget.items if item.value in widget.needs.get(each.value, ())]
        lines.append([])
        lines.append([("NEEDS      ", LABEL_TEXT), (", ".join(needs) or "nothing else", TEXT if needs else FAINT_TEXT)])
        lines.append([("NEEDED BY  ", LABEL_TEXT), (", ".join(needed_by) or "nothing", TEXT if needed_by else FAINT_TEXT)])
        for label, text in widget.facts.get(item.value, ()):
            lines.append([(label.upper().ljust(11), LABEL_TEXT), (text, TEXT)])
        why = widget.why.get(item.value)
        if why:
            lines.append([(why, Style(ACCENT))])
    elif item.value == widget.current:
        lines.append([("Chosen before.", FAINT_TEXT)])
    return lines


def text_field(canvas, widget, top, left, bottom, width, tick):
    canvas.box(top, left, 3, width, FRAME, "◆ " + widget.title, TITLE)
    column = canvas.text(top + 1, left + 2, "❯ ", ACCENT_TEXT)
    room = left + width - 3 - column
    shown = widget.value[-(room - 1) :] if room > 1 else ""
    column = canvas.text(top + 1, column, shown, STRONG)
    canvas.text(top + 1, column, "▌" if tick % 8 < 5 else " ", Style(ACCENT))
    for offset, line in enumerate(textwrap.wrap(widget.note, max(width - 2, 1))[: max(bottom - top - 3, 0)]):
        canvas.text(top + 4 + offset, left + 1, line, MUTED_TEXT)


def labelled(lines, width):
    """Lines as runs: "Label: value" lines become a label column and wrapped values."""
    pairs = [line.split(": ", 1) if ": " in line[:30] else None for line in lines]
    label_width = max([len(pair[0]) for pair in pairs if pair] or [0])
    runs = []
    for line, pair in zip(lines, pairs):
        if pair is None:
            runs.extend([[(text, TEXT)] for text in textwrap.wrap(line, max(width, 1))] or [[]])
            continue
        label, value = pair
        wrapped = textwrap.wrap(value, max(width - label_width - 2, 1)) or [""]
        runs.append([(label.upper().ljust(label_width + 2), LABEL_TEXT), (wrapped[0], TEXT)])
        runs.extend([[(" " * (label_width + 2), TEXT), (rest, TEXT)] for rest in wrapped[1:]])
    return runs


def confirm(canvas, widget, top, left, bottom, width):
    end = panel(canvas, widget.title, labelled(widget.body, width - 4), top, left, bottom - 2, width)
    row = end + 1
    column = canvas.text(row, left + 1, widget.question, TITLE, width=width - 1)
    column = canvas.text(row, column + 3, " y ", KEY)
    column = canvas.text(row, column, " Yes", STRONG)
    column = canvas.text(row, column + 3, " n ", KEY)
    canvas.text(row, column, " No", STRONG)


CHECK = re.compile(r"^(\S+)\s{2,}(.*)$")


def marked(mark, mark_style, text, style, width):
    """A mark, then text wrapped under itself."""
    wrapped = textwrap.wrap(text, max(width - 2, 1)) or [""]
    return [[(mark, mark_style), (wrapped[0], style)]] + [[("  ", style), (rest, style)] for rest in wrapped[1:]]


def summary(canvas, widget, top, left, bottom, width):
    """Checks get a mark each, wrapped rather than cut; a "Warning:" paragraph stands out."""
    lines = []
    for line in widget.body:
        found = CHECK.match(line)
        if found and found.group(1) == "ok":
            lines.extend(marked("✓ ", GOOD_TEXT, found.group(2), TEXT, width - 4))
        elif found:
            lines.extend(marked("✗ ", BAD_TEXT, line, Style(RED), width - 4))
        elif line.startswith("Warning:"):
            lines.extend(marked("⚠ ", WARNING_TEXT, line, Style(AMBER), width - 4))
        elif line:
            lines.extend([[(text, MUTED_TEXT)] for text in textwrap.wrap(line, max(width - 4, 1))])
        else:
            lines.append([])
    panel(canvas, widget.title, lines, top, left, bottom, width)


def keys(canvas, row, left, width, hint):
    """The keys of the hint ("key: what   key: what") as chips, as many as fit."""
    column = left
    for part in hint.split("   "):
        key, _, action = part.partition(": ")
        need = len(key) + 2 + (len(action) + 1 if action else 0) + 2
        if column + need > left + width:
            break
        if action:
            column = canvas.text(row, column, " %s " % key, KEY)
            column = canvas.text(row, column, " " + action, MUTED_TEXT) + 2
        else:
            column = canvas.text(row, column, key, MUTED_TEXT) + 2
