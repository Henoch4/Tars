"""I7 regression: manifest.json validates and matches the live routes.

Pins the doctor gate in-process: schema shape, no placeholder
descriptions, no mojibake, and bidirectional route agreement so the
manifest can never drift from what /manifest actually serves to
okx.ai registration.
"""
import json
import pathlib

from scripts.check_manifest import check_manifest


def _manifest():
    return json.loads(pathlib.Path("manifest.json").read_text(encoding="utf-8"))


class TestManifestDoctor:
    def test_doctor_reports_healthy(self):
        assert check_manifest() == []

    def test_required_keys_present(self):
        m = _manifest()
        assert {"name", "version", "endpoints", "capabilities"} <= set(m)

    def test_every_endpoint_documented(self):
        for ep in _manifest()["endpoints"]:
            assert ep["path"] and ep["method"] and ep["description"]
            assert ep["description"] != "unknown endpoint"
            assert not ep["description"].endswith(" endpoint") or \
                " " not in ep["description"].removesuffix(" endpoint")

    def test_manifest_matches_live_routes(self):
        from src.main import app
        live = set()
        for route in app.routes:
            methods = sorted(getattr(route, "methods", None) or ())
            if not methods or getattr(route, "include_in_schema", True) is False:
                continue
            for meth in methods:
                if meth != "HEAD":
                    live.add((route.path, meth))
        manifest_keys = {(e["path"], e["method"]) for e in _manifest()["endpoints"]}
        assert manifest_keys == live

    def test_metrics_endpoint_registered(self):
        keys = {(e["path"], e["method"]) for e in _manifest()["endpoints"]}
        assert ("/api/v1/metrics", "GET") in keys
        assert ("/health", "GET") in keys
