from pathlib import Path
import json


def main() -> None:
    renderer_dir = Path(__file__).resolve().parent.parent

    template_path = renderer_dir / "template" / "dashboard_template.svg"
    data_path = renderer_dir / "data" / "sample_data.json"
    output_dir = renderer_dir / "output"
    output_path = output_dir / "dashboard.svg"

    template = template_path.read_text(encoding="utf-8")
    data = json.loads(data_path.read_text(encoding="utf-8"))

    rendered = template
    for key, value in data.items():
        rendered = rendered.replace(f"{{{{{key}}}}}", str(value))

    output_dir.mkdir(exist_ok=True)
    output_path.write_text(rendered, encoding="utf-8")

    print(f"Template:  {template_path}")
    print(f"Data:      {data_path}")
    print(f"Generated: {output_path}")


if __name__ == "__main__":
    main()
