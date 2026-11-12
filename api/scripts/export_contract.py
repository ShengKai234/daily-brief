"""Run with `uv run python scripts/export_contract.py` after contract approval."""

import json
from pathlib import Path

import yaml


def exported_contract():
    contracts = Path(__file__).resolve().parents[2] / "contracts"
    original = yaml.safe_load((contracts / "openapi.yaml").read_text())

    def resolve(node):
        if isinstance(node, dict):
            if node.get("$ref") == "./brief-result.schema.json":
                return {"$ref": "#/components/schemas/BriefResult"}
            return {key: resolve(value) for key, value in node.items()}
        if isinstance(node, list):
            return [resolve(value) for value in node]
        return node

    spec = resolve(original)

    def bundle_brief(node):
        if isinstance(node, dict):
            if "$ref" in node and node["$ref"].startswith("#/"):
                return {"$ref": "#/components/schemas/BriefResult/" + node["$ref"][2:]}
            return {key: bundle_brief(value) for key, value in node.items() if key != "$id"}
        if isinstance(node, list):
            return [bundle_brief(value) for value in node]
        return node

    spec["components"]["schemas"]["BriefResult"] = bundle_brief(
        json.loads((contracts / "brief-result.schema.json").read_text())
    )
    return spec


if __name__ == "__main__":
    target = Path(__file__).resolve().parents[1] / "app/openapi.json"
    target.write_text(json.dumps(exported_contract(), indent=2) + "\n")
