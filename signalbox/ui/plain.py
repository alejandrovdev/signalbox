"""The plain questions: a sequence asked as numbered questions read with `input`, for when
the terminal cannot draw (input or output is not a terminal, or curses cannot start).

The widgets are the same, and so is what they accept. A typed "<" goes back and "q" quits
(where the answer is no text). When the input ends before an answer, `Missing` names the
flag that gives it on the command line.
"""

from .sequence import RUNNING

HOW = "Type the answer and press Enter: < goes back, q or Ctrl-C quits without changing anything."


class Missing(Exception):
    """An answer the input ended before: the flag that gives it."""

    def __init__(self, flag, title=""):
        self.flag = flag
        self.title = title
        about = ' "%s"' % title if title else ""
        super().__init__("nothing answered%s: give %s" % (about, flag))


def run(sequence, read=input, write=print):
    """Ask a sequence until it ends; the sequence, done or quit."""
    if sequence.state == RUNNING:
        write(HOW)
    while sequence.state == RUNNING:
        widget = sequence.widget
        number, total = sequence.step()
        write("")
        write("%d/%d. %s" % (number, total, widget.title))
        for line in widget.plain_lines():
            write("   " + line)
        if widget.error:
            write("   " + widget.error)
        try:
            text = read(widget.prompt)
        except EOFError:
            raise Missing(sequence.flag, widget.title)
        except KeyboardInterrupt:
            write("")
            return sequence.quit()
        sequence = sequence.answer(text)
    return sequence
