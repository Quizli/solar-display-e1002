"""Conservative advances for 15 px Arial-compatible SVG text."""

# Upper envelope of the original estimator and Liberation Sans Regular advances,
# rounded upward to a quarter pixel. Unlisted glyphs reserve a full em.
_ADVANCES = {
    char: width
    for width, chars in (
        (4.0, "'ijl|’"),
        (4.25, " !,.:;I"),
        (7.5, '"()*-/[\\]^`cfkrstvxyz{}°'),
        (8.5, "#$0123456789?JL_abdeghnopquéèà²³–"),
        (9.0, "+<=>~×"),
        (9.25, "FTZ"),
        (10.25, "&ABEKPSVXYÉΔ"),
        (11.0, "CDHNRU"),
        (11.5, "wäöüÄÜ"),
        (11.75, "GOQÖ"),
        (12.5, "Mm"),
        (13.5, "%"),
        (14.25, "W"),
        (15.0, "—"),
        (15.25, "@"),
    )
    for char in chars
}


def text_width_px(text):
    return sum(_ADVANCES.get(char, 15) for char in text)


def both_lines_fit(lines, maximum=490):
    return all(text_width_px(line) <= maximum for line in lines)
