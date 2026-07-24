from pathlib import Path
import shutil
import sys


def main() -> None:
    renderer_dir = Path(__file__).resolve().parent.parent
    output_dir = renderer_dir / "output"

    # Find the frozen reference SVG in renderer/
    svg_files = [
        path
        for path in renderer_dir.glob("*.svg")
        if path.name != "dashboard.svg"
    ]

    if len(svg_files) != 1:
        print(
            f"Error: Expected exactly one reference SVG in {renderer_dir}, "
            f"found {len(svg_files)}.",
            file=sys.stderr,
        )
        sys.exit(1)

    source_svg = svg_files[0]
    output_dir.mkdir(exist_ok=True)

    output_svg = output_dir / "dashboard.svg"

    shutil.copyfile(source_svg, output_svg)

    print(f"Reference: {source_svg.name}")
    print(f"Generated: {output_svg}")


if __name__ == "__main__":
    main()
