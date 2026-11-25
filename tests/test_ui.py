"""Tests of the terminal interface: its widgets and the sequence of screens as state
machines driven by keys, the layout the curses renderer draws, and the plain questions used
without a terminal. No test starts curses."""

import curses
import unittest
from unittest import mock

import signalbox
from signalbox import ui
from signalbox.ui import plain, scene, screen, signal
from signalbox.ui.canvas import Style
from signalbox.ui.sequence import DONE, QUIT, RUNNING, Screen, Sequence
from signalbox.ui.widgets import (
    BACKSPACE,
    CTRL_C,
    DOWN,
    ENTER,
    ESC,
    LEFT,
    SPACE,
    UP,
    Checklist,
    Choice,
    Confirm,
    Item,
    Summary,
    TextField,
)

TERMINALS = (Item("warp", "Warp", "installed"), Item("other", "Other", "not installed"))
FEATURES = (
    Item("server", "server", "Sessions the app creates"),
    Item("ready", "ready", "A session always ready"),
    Item("tabs", "tabs", "A tab per session"),
    Item("view", "view", "A read-only tab"),
)
NEEDS = {"tabs": ("ready",), "view": ("server", "tabs")}


def pressed(widget, *keys):
    for key in keys:
        widget = widget.press(key)
    return widget


def texts(widget):
    return [text for text, _ in widget.rows()]


class ChoiceList(unittest.TestCase):
    def test_moves_and_chooses_the_item_under_the_cursor(self):
        chosen = pressed(Choice.of("Terminal", TERMINALS), DOWN, ENTER)

        self.assertTrue(chosen.done)
        self.assertEqual(chosen.value, "other")

    def test_the_cursor_stays_within_the_list(self):
        self.assertEqual(pressed(Choice.of("Terminal", TERMINALS), UP, ENTER).value, "warp")
        self.assertEqual(pressed(Choice.of("Terminal", TERMINALS), DOWN, DOWN, DOWN, ENTER).value, "other")

    def test_starts_on_the_current_value_and_marks_it(self):
        choice = Choice.of("Terminal", TERMINALS, current="other")

        self.assertEqual([focused for _, focused in choice.rows()], [False, True])
        self.assertIn("current", texts(choice)[1])
        self.assertNotIn("current", texts(choice)[0])
        self.assertEqual(pressed(choice, ENTER).value, "other")

    def test_without_a_current_value_none_is_marked(self):
        choice = Choice.of("Mode", (Item("plan", "plan"), Item("auto", "auto")))

        self.assertFalse(any("current" in text for text in texts(choice)))

    def test_each_line_shows_the_note_of_its_item(self):
        self.assertEqual(len(texts(Choice.of("Terminal", TERMINALS))), 2)
        self.assertIn("not installed", texts(Choice.of("Terminal", TERMINALS))[1])

    def test_a_plain_answer_is_a_number_or_nothing_for_the_current_value(self):
        choice = Choice.of("Terminal", TERMINALS, current="other")

        self.assertEqual(choice.answer("1").value, "warp")
        self.assertEqual(choice.answer("").value, "other")
        self.assertFalse(choice.answer("3").done)
        self.assertTrue(choice.answer("3").error)
        self.assertTrue(Choice.of("Terminal", TERMINALS).answer("").error, "nothing is chosen for the person")


class FeatureChecklist(unittest.TestCase):
    def checklist(self, checked=()):
        return Checklist.of("Features", FEATURES, NEEDS, checked)

    def test_turning_a_feature_on_turns_on_what_it_needs_and_says_why(self):
        checklist = pressed(self.checklist(), DOWN, DOWN, DOWN, SPACE)  # view

        self.assertEqual(checklist.checked, {"server", "ready", "tabs", "view"})
        lines = texts(checklist)
        self.assertIn("on because view needs it", lines[0])
        self.assertIn("on because tabs needs it", lines[1], "what a pulled feature needs comes too")
        self.assertIn("on because view needs it", lines[2])
        self.assertNotIn("because", lines[3], "the feature turned on by hand needs no reason")

    def test_turning_one_off_turns_off_what_needs_it_and_says_why(self):
        checklist = pressed(self.checklist(("server", "ready", "tabs", "view")), DOWN, SPACE)  # ready

        self.assertEqual(checklist.checked, {"server"})
        lines = texts(checklist)
        self.assertIn("off because it needs ready", lines[2])
        self.assertIn("off because it needs tabs", lines[3])
        self.assertNotIn("because", lines[0])

    def test_each_line_shows_whether_it_is_on_and_the_features_summary(self):
        lines = texts(self.checklist(("server",)))

        self.assertTrue(lines[0].startswith("[x] server"))
        self.assertTrue(lines[1].startswith("[ ] ready"))
        self.assertIn("Sessions the app creates", lines[0])
        self.assertIn("A read-only tab", lines[3])

    def test_enter_accepts_the_features_on_in_the_order_of_the_list(self):
        accepted = pressed(self.checklist(("tabs", "ready", "server")), ENTER)

        self.assertTrue(accepted.done)
        self.assertEqual(accepted.value, ("server", "ready", "tabs"))

    def test_nothing_on_is_refused(self):
        refused = pressed(self.checklist(), ENTER)

        self.assertFalse(refused.done)
        self.assertTrue(refused.error)

    def test_a_plain_answer_toggles_the_numbers_given_or_accepts_with_nothing(self):
        checklist = self.checklist(("server",))

        toggled = checklist.answer("3, 1")
        self.assertFalse(toggled.done)
        self.assertEqual(toggled.checked, {"ready", "tabs"})
        self.assertEqual(checklist.answer("").value, ("server",))
        self.assertTrue(checklist.answer("9").error)


class TextLine(unittest.TestCase):
    def field(self, value=""):
        return TextField("Name", value, lambda text: "" if text.islower() else "lowercase letters only")

    def test_types_and_erases(self):
        self.assertEqual(pressed(self.field(), "s", "h", "x", BACKSPACE, "o", "p").value, "shop")

    def test_enter_validates_and_shows_the_error(self):
        refused = pressed(self.field(), "S", ENTER)

        self.assertFalse(refused.done)
        self.assertEqual(refused.error, "lowercase letters only")
        self.assertTrue(pressed(refused, BACKSPACE, "s", ENTER).done)
        self.assertEqual(pressed(refused, BACKSPACE, "s").error, "", "typing clears the error")

    def test_starts_from_a_value_given(self):
        self.assertEqual(pressed(self.field("shop"), ENTER).value, "shop")

    def test_a_plain_answer_replaces_the_value_and_nothing_keeps_it(self):
        self.assertEqual(self.field("shop").answer("blog").value, "blog")
        self.assertEqual(self.field("shop").answer("").value, "shop")
        self.assertEqual(self.field("shop").answer("Blog").error, "lowercase letters only")


class YesOrNo(unittest.TestCase):
    def test_y_is_yes_and_n_is_no(self):
        self.assertIs(pressed(Confirm("Save?"), "y").value, True)
        self.assertIs(pressed(Confirm("Save?"), "n").value, False)
        self.assertTrue(pressed(Confirm("Save?"), "N").done)

    def test_enter_alone_answers_nothing(self):
        waiting = pressed(Confirm("Save?"), ENTER)

        self.assertFalse(waiting.done)
        self.assertTrue(waiting.error)

    def test_shows_its_body_and_its_question(self):
        lines = texts(Confirm("Summary", ("name: shop", "mode: plan"), "Save and apply?"))

        self.assertEqual(lines[:2], ["name: shop", "mode: plan"])
        self.assertIn("Save and apply?", lines[-1])

    def test_a_plain_answer_is_yes_or_no(self):
        self.assertIs(Confirm("Save?").answer("yes").value, True)
        self.assertIs(Confirm("Save?").answer("no").value, False)
        self.assertTrue(Confirm("Save?").answer("maybe").error)


class SummaryScreen(unittest.TestCase):
    def test_shows_its_lines_and_enter_continues(self):
        summary = Summary("Prerequisites", ("ok  git", "missing  tmux"))

        self.assertEqual(texts(summary), ["ok  git", "missing  tmux"])
        self.assertTrue(pressed(summary, ENTER).done)
        self.assertTrue(summary.answer("").done)


def screens(terminal_needed=lambda answers: True):
    return [
        Screen("name", "--name", lambda answers: TextField("Name", answers.get("name", ""), lambda text: "")),
        Screen(
            "terminal",
            "--terminal",
            lambda answers: Choice.of("Terminal", TERMINALS, answers.get("terminal")) if terminal_needed(answers) else None,
        ),
        Screen("mode", "--permission-mode", lambda answers: Choice.of("Mode", (Item("plan", "plan"), Item("auto", "auto")), answers.get("mode"))),
        Screen("confirm", "--yes", lambda answers: Confirm("Summary", ("name: %s" % answers.get("name"),), "Save?")),
    ]


def driven(sequence, *keys):
    for key in keys:
        sequence = sequence.press(key)
    return sequence


class Screens(unittest.TestCase):
    def test_enter_moves_to_the_next_screen_and_records_each_answer(self):
        finished = driven(Sequence.start(screens()), "s", "h", "o", "p", ENTER, DOWN, ENTER, ENTER, "y")

        self.assertEqual(finished.state, DONE)
        self.assertEqual(finished.answers, {"name": "shop", "terminal": "other", "mode": "plan", "confirm": True})

    def test_back_returns_to_the_previous_screen_with_its_answer_chosen(self):
        for back in (ESC, LEFT):
            at_mode = driven(Sequence.start(screens()), "s", ENTER, DOWN, ENTER)
            again = at_mode.press(back)

            self.assertEqual(again.widget.title, "Terminal")
            self.assertEqual(again.widget.cursor, 1, "the answer given before is where the cursor is")
            self.assertEqual(driven(again, ENTER).widget.title, "Mode")

    def test_back_on_the_first_screen_stays_there(self):
        sequence = Sequence.start(screens()).press(ESC)

        self.assertEqual(sequence.state, RUNNING)
        self.assertEqual(sequence.widget.title, "Name")

    def test_q_and_ctrl_c_quit(self):
        at_terminal = driven(Sequence.start(screens()), "s", ENTER)

        self.assertEqual(at_terminal.press("q").state, QUIT)
        self.assertEqual(at_terminal.press(CTRL_C).state, QUIT)

    def test_q_in_a_text_field_is_text_and_ctrl_c_still_quits(self):
        sequence = Sequence.start(screens())

        self.assertEqual(driven(sequence, "q").widget.value, "q")
        self.assertEqual(driven(sequence, "q", CTRL_C).state, QUIT)

    def test_no_at_the_confirmation_quits(self):
        finished = driven(Sequence.start(screens()), "s", ENTER, ENTER, ENTER, "n")

        self.assertEqual(finished.state, QUIT)

    def test_an_answer_given_beforehand_is_never_asked(self):
        sequence = Sequence.start(screens(), {"terminal": "warp"}, given={"terminal"})

        at_mode = driven(sequence, "s", ENTER)
        self.assertEqual(at_mode.widget.title, "Mode")
        self.assertEqual(at_mode.press(ESC).widget.title, "Name", "going back skips it too")
        self.assertEqual(driven(at_mode, ENTER, "y").answers["terminal"], "warp")

    def test_a_screen_that_does_not_apply_is_skipped(self):
        sequence = Sequence.start(screens(terminal_needed=lambda answers: answers.get("name") == "tabs"))

        self.assertEqual(driven(sequence, "s", ENTER).widget.title, "Mode")
        self.assertEqual(driven(sequence, "t", "a", "b", "s", ENTER).widget.title, "Terminal")

    def test_every_answer_given_is_done_without_a_screen(self):
        answers = {"name": "shop", "terminal": "warp", "mode": "plan", "confirm": True}

        sequence = Sequence.start(screens(), answers, given=answers)

        self.assertEqual(sequence.state, DONE)
        self.assertEqual(sequence.answers, answers)

    def test_tells_the_flag_of_the_screen_it_is_on_and_how_far_it_is(self):
        sequence = driven(Sequence.start(screens()), "s", ENTER)

        self.assertEqual(sequence.flag, "--terminal")
        self.assertEqual(sequence.step(), (2, 4))

    def test_a_finished_sequence_takes_no_more_keys(self):
        finished = driven(Sequence.start(screens()), CTRL_C)

        self.assertIs(finished.press(ENTER), finished)


class Layout(unittest.TestCase):
    """What the curses renderer draws, computed without curses."""

    def long_checklist(self):
        items = tuple(Item("f%d" % n, "feature-%d" % n, "a summary that goes on " * 6) for n in range(30))
        return Checklist.of("Features of a project with a long title " * 3, items, {}, ())

    def test_fits_80_by_24_with_a_title_a_hint_the_items_and_the_error(self):
        widget = pressed(Checklist.of("Features", FEATURES, NEEDS, ()), ENTER)  # an error to show

        lines = screen.layout(widget, 2, 5, 80, 24)

        self.assertLessEqual(len(lines), 24)
        self.assertTrue(all(len(text) < 80 for text, _ in lines))
        self.assertEqual(lines[0], ("2/5  Features", screen.TITLE))
        self.assertEqual(lines[1][1], screen.HINT)
        self.assertIn(("[ ] server  Sessions the app creates", screen.FOCUS), lines)
        self.assertEqual(lines[-1], (widget.error, screen.ERROR))

    def test_truncates_at_any_size_down_to_one_by_one(self):
        widget = self.long_checklist()
        for width in range(1, 82, 3):
            for height in range(1, 26):
                lines = screen.layout(widget, 1, 1, width, height)
                self.assertLessEqual(len(lines), height, (width, height))
                self.assertTrue(all(len(text) <= max(width - 1, 0) for text, _ in lines), (width, height))

    def test_keeps_the_line_under_the_cursor_in_view(self):
        widget = pressed(self.long_checklist(), *([DOWN] * 25))

        lines = screen.layout(widget, 1, 1, 80, 12)

        self.assertEqual(len([text for text, style in lines if style == screen.FOCUS]), 1)
        self.assertIn("feature-25", next(text for text, style in lines if style == screen.FOCUS))

    def test_curses_keys_become_the_names_the_widgets_take(self):
        self.assertEqual(screen.key_name(curses.KEY_UP), UP)
        self.assertEqual(screen.key_name(curses.KEY_DOWN), DOWN)
        self.assertEqual(screen.key_name(curses.KEY_LEFT), LEFT)
        self.assertEqual(screen.key_name(10), ENTER)
        self.assertEqual(screen.key_name(curses.KEY_ENTER), ENTER)
        self.assertEqual(screen.key_name(27), ESC)
        self.assertEqual(screen.key_name(127), BACKSPACE)
        self.assertEqual(screen.key_name(curses.KEY_BACKSPACE), BACKSPACE)
        self.assertEqual(screen.key_name(3), CTRL_C)
        self.assertEqual(screen.key_name(ord(" ")), SPACE)
        self.assertEqual(screen.key_name(ord("q")), "q")
        self.assertIsNone(screen.key_name(curses.KEY_RESIZE), "a key's code is no character")

    def test_a_character_read_as_text_is_itself_or_the_key_it_stands_for(self):
        self.assertEqual(screen.key_name("q"), "q")
        self.assertEqual(screen.key_name("ƚ"), "ƚ", "a character past 255 is typed, not a key")
        self.assertEqual(screen.key_name("\n"), ENTER)
        self.assertEqual(screen.key_name("\x1b"), ESC)
        self.assertEqual(screen.key_name("\x7f"), BACKSPACE)
        self.assertIsNone(screen.key_name("\x01"))


class Colors(unittest.TestCase):
    """How much color the screens use, decided from what the terminal takes."""

    def test_256_colors_draw_everything_8_only_the_text_and_none_without_colors(self):
        self.assertEqual(screen.palette(True, 256, False), screen.FULL)
        self.assertEqual(screen.palette(True, 8, False), screen.BASIC)
        self.assertEqual(screen.palette(False, 256, False), screen.PLAIN)

    def test_no_color_turns_every_color_off(self):
        self.assertEqual(screen.palette(True, 256, True), screen.PLAIN)
        self.assertTrue(screen.no_color({"NO_COLOR": "1"}))
        self.assertFalse(screen.no_color({"NO_COLOR": ""}), "an empty NO_COLOR asks nothing")
        self.assertFalse(screen.no_color({}))

    def test_half_blocks_need_a_terminal_that_writes_utf_8(self):
        self.assertTrue(screen.draws_blocks("UTF-8"))
        self.assertTrue(screen.draws_blocks("utf8"))
        self.assertFalse(screen.draws_blocks("US-ASCII"))
        self.assertFalse(screen.draws_blocks("no-such-encoding"))
        self.assertFalse(screen.draws_blocks(None))


class FakeCurses:
    A_NORMAL, A_REVERSE, A_DIM, A_BOLD = 0, 1 << 18, 1 << 20, 1 << 21
    COLOR_RED, COLOR_GREEN = 1, 2

    def __init__(self):
        self.pairs = {}

    def init_pair(self, number, foreground, background):
        self.pairs[number] = (foreground, background)

    def color_pair(self, number):
        return number << 8

    def colors_of(self, attribute):
        return self.pairs.get((attribute >> 8) & 0xFF)


class Painting(unittest.TestCase):
    """The attributes each kind of line and each picture cell is drawn with."""

    def test_without_colors_the_lines_keep_their_plain_attributes(self):
        fake = FakeCurses()
        painter = screen.Painter(fake, screen.PLAIN, rich=False, limit=255)

        self.assertEqual(painter.style(screen.TITLE), fake.A_BOLD)
        self.assertEqual(painter.style(screen.HINT), fake.A_DIM)
        self.assertEqual(painter.style(screen.ROW), fake.A_NORMAL)
        self.assertEqual(painter.style(screen.FOCUS), fake.A_REVERSE)
        self.assertEqual(painter.style(screen.ERROR), fake.A_BOLD)
        self.assertEqual(fake.pairs, {})

    def test_with_256_colors_the_title_is_green_the_cursor_a_green_bar_and_the_error_red(self):
        fake = FakeCurses()
        painter = screen.Painter(fake, screen.FULL, rich=True, limit=255)

        title = painter.style(screen.TITLE)
        self.assertEqual(fake.colors_of(title), (screen.GREEN_TEXT, -1))
        self.assertTrue(title & fake.A_BOLD)
        self.assertEqual(fake.colors_of(painter.style(screen.FOCUS)), (screen.ON_GREEN, scene.ACCENT))
        self.assertEqual(fake.colors_of(painter.style(screen.ERROR)), (screen.RED_TEXT, -1))
        self.assertEqual(painter.style(screen.HINT), fake.A_DIM, "the hint keeps the terminal's own color")

    def test_with_8_colors_the_title_is_green_and_the_cursor_stays_reversed(self):
        fake = FakeCurses()
        painter = screen.Painter(fake, screen.BASIC, rich=False, limit=255)

        self.assertEqual(fake.colors_of(painter.style(screen.TITLE)), (fake.COLOR_GREEN, -1))
        self.assertEqual(painter.style(screen.FOCUS), fake.A_REVERSE)

    def test_reuses_a_pair_and_falls_back_to_the_default_colors_past_the_limit(self):
        fake = FakeCurses()
        painter = screen.Painter(fake, screen.FULL, rich=True, limit=2)

        first = painter.pair(10, 20)
        self.assertEqual(painter.pair(10, 20), first)
        painter.pair(30, 40)
        self.assertEqual(painter.pair(50, 60), fake.A_NORMAL, "both pairs are in this frame")
        self.assertEqual(len(fake.pairs), 2)

    def test_a_new_frame_makes_the_pair_unused_longest_again(self):
        fake = FakeCurses()
        painter = screen.Painter(fake, screen.FULL, rich=True, limit=2)
        painter.pair(10, 20)
        painter.pair(30, 40)
        painter.pair(10, 20)

        painter.begin()
        again = painter.pair(50, 60)

        self.assertEqual(fake.colors_of(again), (50, 60))
        self.assertEqual(fake.pairs, {1: (10, 20), 2: (50, 60)})
        self.assertEqual(fake.colors_of(painter.pair(10, 20)), (10, 20))

    def test_a_cell_takes_its_colors_and_its_weight(self):
        fake = FakeCurses()
        painter = screen.Painter(fake, screen.FULL, rich=True, limit=255)

        bold = painter.cell(Style(214, 16, bold=True))

        self.assertEqual(fake.colors_of(bold), (214, 16))
        self.assertTrue(bold & fake.A_BOLD)
        self.assertEqual(painter.cell(Style()), fake.A_NORMAL)
        self.assertEqual(painter.cell(Style(dim=True)), fake.A_DIM)

    def test_the_pair_limit_is_what_an_attribute_carries(self):
        class Few:
            COLOR_PAIRS = 64

        class Many:
            COLOR_PAIRS = 32767

        self.assertEqual(screen.pair_limit(Few), 63)
        self.assertEqual(screen.pair_limit(Many), 255)


class FakeWindow:
    def __init__(self, height, width, keys=()):
        self.size = (height, width)
        self.keys = list(keys)
        self.cells = {}
        self.timeouts = []
        self.refreshed = 0

    def getmaxyx(self):
        return self.size

    def erase(self):
        self.cells = {}

    def addstr(self, row, column, text, attribute=0):
        for offset, character in enumerate(text):
            self.cells[(row, column + offset)] = (character, attribute)

    def refresh(self):
        self.refreshed += 1

    def keypad(self, on):
        pass

    def timeout(self, milliseconds):
        self.timeouts.append(milliseconds)

    def get_wch(self):
        key = self.keys.pop(0)
        if isinstance(key, Exception):
            raise key
        return key

    def line(self, row):
        width = self.size[1]
        return "".join(self.cells.get((row, column), (" ", None))[0] for column in range(width))


class FakePainter:
    def __init__(self, rich):
        self.rich = rich
        self.frames = 0

    def begin(self):
        self.frames += 1

    def style(self, style):
        return ("style", style)

    def cell(self, style):
        return ("cell", style)


class Drawing(unittest.TestCase):
    """What lands on the window, with a painter that names each attribute."""

    def test_a_large_terminal_with_256_colors_gets_the_whole_screen(self):
        window = FakeWindow(40, 140)
        painter = FakePainter(rich=True)

        screen.draw(window, Sequence.start(screens()), painter, 5)

        self.assertIn("◆ signalbox", window.line(0))
        self.assertIn("v" + signalbox.__version__, window.line(0))
        self.assertTrue(all(attribute[0] == "cell" for _, attribute in window.cells.values()))
        self.assertEqual((painter.frames, window.refreshed), (1, 1))

    def test_a_small_terminal_gets_the_plain_lines(self):
        window = FakeWindow(20, 70)

        screen.draw(window, Sequence.start(screens()), FakePainter(rich=True), 5)

        self.assertEqual(window.cells[(0, 0)], ("1", ("style", screen.TITLE)))
        self.assertTrue(window.line(0).startswith("1/4  Name"))

    def test_without_whole_screens_any_size_gets_the_plain_lines(self):
        window = FakeWindow(40, 140)

        screen.draw(window, Sequence.start(screens()), FakePainter(rich=False), 5)

        self.assertTrue(window.line(0).startswith("1/4  Name"))
        self.assertNotIn("signalbox", "".join(window.line(row) for row in range(40)))

    def test_whole_screens_are_drawn_again_each_tick_without_a_key(self):
        window = FakeWindow(40, 140, keys=[curses.error("no input"), curses.error("no input"), 3])

        ended = screen.drive(window, Sequence.start(screens()), FakePainter(rich=True), clock=lambda: 12.0)

        self.assertEqual(ended.state, QUIT)
        self.assertEqual(window.timeouts, [round(signal.TICK * 1000)])
        self.assertEqual(window.refreshed, 3)

    def test_the_plain_layout_waits_for_a_key_as_before(self):
        window = FakeWindow(40, 140, keys=[3])

        ended = screen.drive(window, Sequence.start(screens()), FakePainter(rich=False))

        self.assertEqual(ended.state, QUIT)
        self.assertEqual(window.timeouts, [])
        self.assertEqual(window.refreshed, 1)



def scripted(*lines):
    """What `input` returns, one line per call; the end of the input after the last."""
    remaining = list(lines)
    prompts = []

    def read(prompt=""):
        prompts.append(prompt)
        if not remaining:
            raise EOFError
        answer = remaining.pop(0)
        if isinstance(answer, BaseException):
            raise answer
        return answer

    read.prompts = prompts
    return read


class PlainQuestions(unittest.TestCase):
    def run_plain(self, *lines, sequence=None):
        self.written = []
        self.read = scripted(*lines)
        return plain.run(sequence or Sequence.start(screens()), self.read, self.written.append)

    def test_asks_numbered_questions_with_the_same_validation(self):
        validated = [
            Screen("name", "--name", lambda answers: TextField("Name", "", lambda text: "" if text.islower() else "lowercase only")),
        ] + screens()[1:]

        finished = self.run_plain("Shop", "shop", "2", "", "1", "y", sequence=Sequence.start(validated))

        self.assertEqual(finished.state, DONE)
        self.assertEqual(finished.answers, {"name": "shop", "terminal": "other", "mode": "plan", "confirm": True})
        self.assertIn("1/4. Name", self.written)
        self.assertIn("   lowercase only", self.written, "a refused answer is said and asked again")
        self.assertIn("2/4. Terminal", self.written)
        self.assertIn("   2) Other  not installed", self.written)

    def test_less_than_goes_back_and_q_quits(self):
        back = self.run_plain("shop", "<", "blog", "q")

        self.assertEqual(back.state, QUIT)
        self.assertEqual(back.answers["name"], "blog")
        self.assertEqual(self.written.count("1/4. Name"), 2)

    def test_no_at_the_confirmation_quits(self):
        self.assertEqual(self.run_plain("shop", "1", "1", "no").state, QUIT)

    def test_the_end_of_the_input_names_the_flag_of_the_question_left(self):
        with self.assertRaises(ui.Missing) as caught:
            self.run_plain("shop", "1")

        self.assertEqual(caught.exception.flag, "--permission-mode")
        self.assertIn("--permission-mode", str(caught.exception))

    def test_ctrl_c_quits(self):
        self.assertEqual(self.run_plain("shop", KeyboardInterrupt()).state, QUIT)


class Console(unittest.TestCase):
    def test_without_a_terminal_it_asks_plain_questions(self):
        written = []
        console = ui.Console(read=scripted("shop", "1", "1", "y"), write=written.append, tty=False)

        with mock.patch.object(screen, "run") as drawn:
            finished = console.ask(Sequence.start(screens()))

        drawn.assert_not_called()
        self.assertEqual(finished.state, DONE)
        self.assertFalse(console.terminal)

    def test_in_a_terminal_it_draws_the_screens(self):
        console = ui.Console(read=scripted(), write=[].append, tty=True)
        sequence = Sequence.start(screens())

        with mock.patch.object(screen, "run", return_value=sequence.press(CTRL_C)) as drawn:
            self.assertEqual(console.ask(sequence).state, QUIT)

        drawn.assert_called_once_with(sequence)
        self.assertTrue(console.terminal)

    def test_falls_back_to_plain_questions_when_curses_cannot_start(self):
        console = ui.Console(read=scripted("shop", "1", "1", "y"), write=[].append, tty=True)

        with mock.patch.object(screen, "run", side_effect=screen.CannotStart("no terminfo")):
            self.assertEqual(console.ask(Sequence.start(screens())).state, DONE)

    def test_a_terminal_is_one_where_both_input_and_output_are(self):
        class Stream:
            def __init__(self, tty):
                self.tty = tty

            def isatty(self):
                return self.tty

        self.assertTrue(ui.Console(stdin=Stream(True), stdout=Stream(True)).terminal)
        self.assertFalse(ui.Console(stdin=Stream(True), stdout=Stream(False)).terminal)
        self.assertFalse(ui.Console(stdin=Stream(False), stdout=Stream(True)).terminal)


if __name__ == "__main__":
    unittest.main()
