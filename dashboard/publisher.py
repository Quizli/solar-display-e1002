import logging
import os
import tempfile
from pathlib import Path

from renderer.src.render import render_dashboard

LOGGER = logging.getLogger(__name__)


def validate_svg(svg):
    if not svg.strip():
        raise ValueError("rendered SVG is empty")
    if "<svg" not in svg:
        raise ValueError("rendered output does not contain <svg")
    if "{{" in svg or "}}" in svg:
        raise ValueError("rendered SVG contains unresolved placeholders")


def publish_view(view, output_path):
    if view["freshness"] == "missing":
        raise ValueError("no solar data available; preserving the last good SVG")
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    svg = render_dashboard(view["display"])
    validate_svg(svg)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=str(output.parent),
                                         prefix=".dashboard-", suffix=".svg.tmp", delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(svg)
            handle.flush()
            os.fsync(handle.fileno())
        validate_svg(temporary.read_text(encoding="utf-8"))
        os.replace(str(temporary), str(output))
    except Exception:
        if temporary is not None:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass
        LOGGER.exception("dashboard publishing failed; last good SVG was preserved")
        raise
    return output
