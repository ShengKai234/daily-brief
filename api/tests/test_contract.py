import runpy
from pathlib import Path

from conftest import make_settings
from fastapi.openapi.utils import get_openapi

from app.contract import SPEC
from app.main import create_app


def test_packaged_contract_matches_authoritative_files():
    exporter = runpy.run_path(
        str(Path(__file__).resolve().parents[1] / "scripts/export_contract.py")
    )
    assert SPEC == exporter["exported_contract"]()


def test_every_contract_operation_has_a_route():
    app = create_app(make_settings("postgresql://postgres@127.0.0.1:1/unused"))
    expected = {
        operation["operationId"]
        for methods in SPEC["paths"].values()
        for method, operation in methods.items()
        if method in {"get", "post", "put", "patch", "delete"}
    }
    actual = get_openapi(title=app.title, version=app.version, routes=app.routes)
    assert expected == {
        operation["operationId"]
        for methods in actual["paths"].values()
        for operation in methods.values()
    }
