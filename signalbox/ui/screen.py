"""The curses renderer: draws a sequence's widget and feeds it the keys pressed.

    layout(widget, number, total, width, height)   the lines to draw, computed without curses
    key_name(code)                                 a key curses read, as the widgets name it
    run(sequence)                                  ask a sequence on the terminal

The drawing is a title, a one-line hint of the keys, the widget's rows and an error line.
Every line is cut to the width and the rows scroll to keep the cursor in view, so any size
of terminal draws without failing. CannotStart says that curses could not take the
terminal; whoever asked falls back to plain questions.
"""

from .sequence import RUNNING
from .widgets import BACKSPACE, CTRL_C, DOWN, ENTER, ESC, LEFT, RIGHT, UP, typed

TITLE, HINT, ROW, FOCUS, ERROR = "title", "hint", "row", "focus", "error"


class CannotStart(Exception):
    """curses could not take the terminal."""


def layout(widget, number, total, width, height):
    """What to draw, as (text, style) lines: at most `height` lines, each shorter than `width`
    (the last column is never written, where curses fails)."""
    room = max(width - 1, 0)
    rows = [(text, FOCUS if focused else ROW) for text, focused in widget.rows()]
    focus = next((index for index, (_, style) in enumerate(rows) if style == FOCUS), 0)
    title = ("%d/%d  %s" % (number, total, widget.title), TITLE)
    error = [("", ROW), (widget.error, ERROR)] if widget.error else []
    head = [title, (widget.hint, HINT), ("", ROW)]
    space = height - len(head) - len(error)
    if space < 1:  # too low for everything: the title, the line under the cursor, the error
        essential = [title] + rows[focus : focus + 1] + ([(widget.error, ERROR)] if widget.error else [])
        lines = essential[: max(height, 0)]
    else:
        start = max(0, min(focus - space // 2, len(rows) - space))
        lines = head + rows[start : start + space] + error
    return [(text[:room], style) for text, style in lines]


def named_keys():
    import curses

    return {
        curses.KEY_UP: UP,
        curses.KEY_DOWN: DOWN,
        curses.KEY_LEFT: LEFT,
        curses.KEY_RIGHT: RIGHT,
        curses.KEY_ENTER: ENTER,
        curses.KEY_BACKSPACE: BACKSPACE,
        10: ENTER,
        13: ENTER,
        27: ESC,
        127: BACKSPACE,
        8: BACKSPACE,
        3: CTRL_C,
    }


def key_name(code):
    """A key as the widgets name it; None for a key they do not take. `code` is what
    curses read: a character, or a number (a key's code from 256 on, else a character's)."""
    named = named_keys()
    if isinstance(code, str):
        if len(code) != 1:
            return None
        if ord(code) < 256 and ord(code) in named:
            return named[ord(code)]
        return code if typed(code) else None
    if code in named:
        return named[code]
    if 0 <= code < 256 and typed(chr(code)):
        return chr(code)
    return None


def draw(window, widget, number, total):
    import curses

    styles = {TITLE: curses.A_BOLD, HINT: curses.A_DIM, ROW: curses.A_NORMAL, FOCUS: curses.A_REVERSE, ERROR: curses.A_BOLD}
    height, width = window.getmaxyx()
    window.erase()
    for row, (text, style) in enumerate(layout(widget, number, total, width, height)):
        try:
            window.addstr(row, 0, text, styles[style])
        except curses.error:
            pass  # a terminal that shrank while drawing: the next key draws again
    window.refresh()


def drive(window, sequence):
    """Draw and read keys until the sequence ends."""
    import curses

    try:
        curses.curs_set(0)
    except curses.error:
        pass  # a terminal that cannot hide its cursor
    try:
        curses.set_escdelay(25)  # Esc is a key here, not the start of another
    except (AttributeError, curses.error):
        pass
    window.keypad(True)
    try:
        while sequence.state == RUNNING:
            number, total = sequence.step()
            draw(window, sequence.widget, number, total)
            try:
                code = window.get_wch()
            except curses.error:  # no key after all, such as a read a signal interrupted
                continue
            name = key_name(code)
            if name is not None:
                sequence = sequence.press(name)
    except KeyboardInterrupt:
        sequence = sequence.quit()
    return sequence


def run(sequence):
    """Ask a sequence on the terminal; CannotStart when curses cannot take it."""
    try:
        import curses
    except ImportError as error:
        raise CannotStart(str(error))
    started = []

    def inside(window):
        started.append(True)
        try:
            # curses.wrapper starts colors, which would paint white on black; keep the
            # terminal's own colors, light or dark.
            curses.use_default_colors()
        except curses.error:
            pass
        return drive(window, sequence)

    try:
        return curses.wrapper(inside)
    except curses.error as error:
        if started:
            raise
        raise CannotStart(str(error))
