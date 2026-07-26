"""Conservative Arial-compatible story-line width estimation at 15 px."""

# Arial's average lowercase width is about 7.3 px at 15 px. Wide glyphs are
# deliberately overestimated: a false rejection is safer than SVG overflow.
_NARROW = set(" .,;:!|'’ijlI1")
_WIDE = set("MWmw@%ÄÖÜäöü")


def text_width_px(text):
    width = 0.0
    for char in text:
        if char in _NARROW:
            width += 4.0
        elif char in _WIDE:
            width += 11.5
        elif char.isupper() or char.isdigit():
            width += 8.4
        else:
            width += 7.4
    return round(width, 1)


def both_lines_fit(lines, maximum=490):
    return all(text_width_px(line) <= maximum for line in lines)
