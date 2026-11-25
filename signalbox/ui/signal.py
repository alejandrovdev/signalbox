"""The railway signal beside the setup screens: a color-light signal drawn in large pixels, a
dark mast on a base with a ladder, and a black plate with a white border holding three lamps
behind their hoods, red on top, amber, then green. The lit lamp glows, its light breathing slowly into the dark; the others
stay dim. Left alone it changes aspect as a signal does once a train has gone by: red, then
amber, then green, then red again for the next one, one lamp fading out as the next warms
up. A step may hold one aspect instead.

    COLUMNS, ROWS            the cells it covers, two pixels high each
    TICK                     the seconds from one picture to the next
    ASPECTS                  RED, AMBER, GREEN: the lamps from top to bottom
    aspect(tick)             the aspect shown at a moment, left alone
    lights(tick, held)       how lit each lamp is at a moment, from 0 to 1
    pixels(tick, held)       the picture at a moment: HEIGHT rows of WIDTH colors

`held` is an aspect to hold, or None to change aspect. Colors are numbers of the 256-color
terminal palette; NIGHT is the dark the light does not reach. The same moment always draws
the same picture; nothing here draws or knows curses.
"""

import math

COLUMNS, ROWS = 30, 17
WIDTH, HEIGHT = COLUMNS, 2 * ROWS  # in pixels
TICK = 0.12

RED, AMBER, GREEN = "red", "amber", "green"
ASPECTS = (RED, AMBER, GREEN)
NIGHT = 233

LIT = {  # a lit lens, from its rim to its core
    RED: (88, 124, 160, 196, 203, 217),
    AMBER: (130, 166, 172, 208, 214, 222),
    GREEN: (22, 28, 34, 41, 83, 157),
}
DIM = {RED: 52, AMBER: 58, GREEN: 22}  # an unlit lens
GLINT = 240  # where an unlit lens catches what light there is
GLOW = {  # from the dark to the nearest the light reaches
    RED: (NIGHT, 234, 88, 124, 160, 196),
    AMBER: (NIGHT, 234, 94, 130, 172, 214),
    GREEN: (NIGHT, 234, 234, 22, 28, 34),
}
REACH, STEP = 20, 2.3  # pixels a lamp's light reaches at full strength, pixels per level
BREATH = 14  # ticks for the glow to grow and fall back

PLATE = (7, 1, 16, 26)  # the plate behind the lamps: left, top, width and height, in pixels
CENTERS = {RED: (15.0, 6.0), AMBER: (15.0, 13.0), GREEN: (15.0, 20.0)}  # of the lenses
LAMPS = {aspect: (int(x) - 1, int(y) - 1) for aspect, (x, y) in CENTERS.items()}  # a pixel at each lens's middle
LENS, HOOD = 2.95, 4.0  # radii, in pixels
INK = {
    "K": 16,  # the plate
    "W": 250,  # the plate's white border
    "w": 246,  # the border, shade
    "V": 238,  # hood
    "v": 242,  # hood, lit rim
    "M": 237,  # mast
    "m": 240,  # mast, lit edge
    "D": 235,  # mast, shade
    "L": 238,  # ladder
    "P": 252,  # the signal's number plate
    "p": 245,  # the number plate, shade
    "B": 240,  # base, top
    "b": 237,  # base
}
MAST = (13, 27, 31)  # its left column, top and bottom rows; four pixels wide
LADDER = (18, 20)  # the columns of its rails

HOLD = {RED: 32, AMBER: 24, GREEN: 44}  # ticks each aspect shows, left alone
FADE = 3  # ticks a change of aspect takes
CYCLE = sum(HOLD.values())


def noise(x, y):
    """A fixed number from 0 to 1 for a pair of integers: the texture of the pixels."""
    n = (x * 374761393 + y * 668265263) & 0xFFFFFFFF
    n = ((n ^ (n >> 13)) * 1274126177) & 0xFFFFFFFF
    return (n ^ (n >> 16)) / 4294967296


def aspect(tick):
    """The aspect shown at a moment when none is held."""
    at = tick % CYCLE
    for each in ASPECTS:
        if at < HOLD[each]:
            return each
        at -= HOLD[each]
    return RED  # not reached: the holds add up to the cycle


def lights(tick, held=None):
    """How lit each lamp is: 1 for the aspect shown, 0 for the others, and in between for
    the two lamps of a change of aspect."""
    if held is not None:
        return {each: 1 if each == held else 0 for each in ASPECTS}
    at = tick % CYCLE
    for index, each in enumerate(ASPECTS):
        if at < HOLD[each]:
            levels = {other: 0 for other in ASPECTS}
            if at < FADE:
                warmed = (at + 1) / (FADE + 1)
                levels[each] = warmed
                levels[ASPECTS[index - 1]] = 1 - warmed
            else:
                levels[each] = 1
            return levels
        at -= HOLD[each]
    return {each: 1 if each == RED else 0 for each in ASPECTS}  # not reached


def distance(x, y, center):
    return math.hypot(x + 0.5 - center[0], y + 0.5 - center[1])


def glow(levels, breath, x, y):
    """(the color the lamps' light gives a pixel, its level): NIGHT and 0 where none reaches."""
    color, best = NIGHT, 0
    for each in ASPECTS:
        if not levels[each]:
            continue
        reach = REACH * levels[each] * breath
        level = max(0, min(len(GLOW[each]) - 1, int((reach - distance(x, y, CENTERS[each])) // STEP)))
        if level and noise(x, y) < 0.2:
            level -= 1  # a fixed dither, so the glow has the grain of blocks
        if level > best:
            color, best = GLOW[each][level], level
    return color, best


def pixels(tick, held=None):
    """The picture at a moment, as HEIGHT rows of WIDTH palette colors."""
    levels = lights(tick, held)
    breath = 0.85 + 0.15 * math.sin(tick * 2 * math.pi / BREATH)
    grid = [[glow(levels, breath, x, y)[0] for x in range(WIDTH)] for y in range(HEIGHT)]

    def put(x, y, ink):
        if 0 <= x < WIDTH and 0 <= y < HEIGHT:
            grid[y][x] = INK[ink] if isinstance(ink, str) else ink

    for y in range(MAST[1], MAST[2] + 1):  # the mast, its number plate and the ladder beside it
        for x, ink in zip(range(MAST[0], MAST[0] + 4), "mMMD"):
            put(x, y, ink)
        put(LADDER[0], y, "L")
        put(LADDER[1], y, "L")
        if y % 2:
            put(LADDER[0] + 1, y, "L")
    for x, y, ink in ((14, 28, "P"), (15, 28, "P"), (14, 29, "p"), (15, 29, "p")):
        put(x, y, ink)
    for x in range(MAST[0] - 2, MAST[0] + 6):
        put(x, MAST[2] + 1, "B")
    for x in range(MAST[0] - 3, MAST[0] + 7):
        put(x, MAST[2] + 2, "b")

    left, top, width, height = PLATE
    right, bottom = left + width - 1, top + height - 1
    for y in range(top, bottom + 1):  # the plate, black inside a white border with its corners rounded off
        for x in range(left, right + 1):
            corner = x in (left, right) and y in (top, bottom)
            if corner:
                continue
            if x in (left, right) or y in (top, bottom) or (x in (left + 1, right - 1) and y in (top + 1, bottom - 1)):
                put(x, y, "W" if x < right - 1 and y < bottom - 1 else "w")
                continue
            put(x, y, "K")

    for each in ASPECTS:  # each lens under its hood
        center = CENTERS[each]
        for y in range(int(center[1] - HOOD), int(center[1] + HOOD) + 1):
            for x in range(int(center[0] - HOOD), int(center[0] + HOOD) + 1):
                away = distance(x, y, center)
                if away <= LENS:
                    put(x, y, lens(each, levels[each], away, x + 0.5 - center[0], y + 0.5 - center[1]))
                elif away <= HOOD and y + 0.5 <= center[1] + 0.6:
                    if levels[each] > 0.5 and away < 3.5:
                        put(x, y, GLOW[each][4])  # the light on the hood's underside
                    else:
                        put(x, y, "v" if x + 0.5 < center[0] and y + 0.5 < center[1] - 1 else "V")
    return grid


def lens(each, level, away, across, down):
    """The color of a lens's pixel: shaded from the core out when lit, dim with a glint when
    not."""
    if not level:
        return GLINT if (across, down) == (-1.5, -1.5) else DIM[each]
    ramp = LIT[each]
    shade = round(level * (len(ramp) - 1) * (1.25 - away / (LENS + 1.2)))
    return ramp[max(0, min(len(ramp) - 1, shade))]
