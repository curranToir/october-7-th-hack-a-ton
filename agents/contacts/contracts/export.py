"""Run from the repository root with `python -m agents.contacts.contracts.export`."""
import argparse
import json
from pathlib import Path

from apps.orchestrator.sales.models import ContactReport, ContactTask


def main() -> None:
    parser = argparse.ArgumentParser(description="Export Python contact worker contracts")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    for name, model in (("task", ContactTask), ("report", ContactReport)):
        path = Path(__file__).parent / f"{name}.schema.json"
        content = json.dumps(model.model_json_schema(), indent=2) + "\n"
        if args.check:
            if path.read_text() != content:
                raise SystemExit(f"Regenerate stale contact contract: {path}")
        else:
            path.write_text(content)


if __name__ == "__main__":
    main()
