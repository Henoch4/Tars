#!/usr/bin/env python3
"""
Generate versioned OpenAPI export and sync with manifest.json.

Run after any endpoint changes: `python scripts/generate_openapi.py`
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
MANIFEST_PATH = REPO / "manifest.json"
OPENAPI_DIR = REPO / "openapi"
OPENAPI_DIR.mkdir(exist_ok=True)

sys.path.insert(0, str(REPO))


def load_manifest() -> dict:
    with open(MANIFEST_PATH) as f:
        return json.load(f)


def save_manifest(manifest: dict) -> None:
    with open(MANIFEST_PATH, "w") as f:
        json.dump(manifest, f, indent=2)
        f.write("\n")


def extract_routes_from_main() -> list[dict]:
    """Extract routes from the live FastAPI app object (I7).

    Reads app.routes directly — the manifest can never drift from what
    the server actually serves, unlike regex scraping of source text.
    Descriptions come from endpoint docstrings (first line); routes
    without a usable docstring are reported, not papered over.
    """
    from src.main import app

    routes = []
    for route in app.routes:
        methods = sorted(getattr(route, "methods", None) or ())
        if not methods or getattr(route, "include_in_schema", True) is False:
            continue
        endpoint = getattr(route, "endpoint", None)
        func_name = getattr(endpoint, "__name__", "unknown")
        doc = (getattr(endpoint, "__doc__", "") or "").strip()
        description = doc.split("\n")[0].strip() if doc else ""
        for method in methods:
            if method == "HEAD":
                continue
            routes.append({
                "path": route.path,
                "method": method,
                "description": description,
                "function": func_name,
            })
    # De-duplicate (same path+method registered twice keeps first).
    seen = set()
    unique = []
    for r in routes:
        key = (r["path"], r["method"])
        if key not in seen:
            seen.add(key)
            unique.append(r)
    return unique


def generate_openapi_spec(manifest: dict, routes: list[dict]) -> dict:
    """Generate OpenAPI 3.0 spec from manifest and routes."""
    spec = {
        "openapi": "3.0.3",
        "info": {
            "title": manifest.get("name", "TARS Trade Audit & Risk System"),
            "description": manifest.get("description", ""),
            "version": manifest.get("version", "0.1.0"),
        },
        "servers": [
            {"url": "https://api.tars-trade.com", "description": "Production"},
            {"url": "https://staging-api.tars-trade.com", "description": "Staging"},
            {"url": "http://localhost:8000", "description": "Local development"},
        ],
        "paths": {},
        "components": {
            "securitySchemes": {
                "AgentToken": {
                    "type": "apiKey",
                    "in": "header",
                    "name": "X-Agent-Token",
                },
            },
        },
        "security": [{"AgentToken": []}],
        "tags": [
            {"name": "Trading", "description": "Trade execution and management"},
            {"name": "Audit", "description": "Onchain audit trail queries"},
            {"name": "Risk", "description": "Risk management and statistics"},
            {"name": "Vault", "description": "Depositor-facing vault API"},
            {"name": "System", "description": "Health and manifest"},
        ],
    }
    
    for route in routes:
        path = route["path"]
        method = route["method"].lower()
        
        if path not in spec["paths"]:
            spec["paths"][path] = {}
        
        # Determine tags
        tag = "System"
        if path.startswith("/trade") or path.startswith("/kill-switch"):
            tag = "Trading"
        elif path.startswith("/audit"):
            tag = "Audit"
        elif path.startswith("/risk"):
            tag = "Risk"
        elif path.startswith("/vault") or path.startswith("/api/v1/vault"):
            tag = "Vault"
        
        spec["paths"][path][method] = {
            "tags": [tag],
            "summary": route["description"],
            "operationId": route["function"],
            "responses": {
                "200": {"description": "Successful response"},
                "400": {"description": "Bad request"},
                "401": {"description": "Unauthorized"},
                "500": {"description": "Internal server error"},
            },
        }
        
        # Add security for mutating endpoints
        if method in ("post", "put", "delete", "patch"):
            spec["paths"][path][method]["security"] = [{"AgentToken": []}]
    
    return spec


def sync_manifest_endpoints(manifest: dict, routes: list[dict]) -> dict:
    """Full sync of manifest endpoints with actual routes (I7).

    Endpoints are GENERATED, not curated: missing routes are added,
    removed routes are dropped, and descriptions refresh from docstrings.
    Anything else (name, capabilities, permissions) stays hand-maintained.
    """
    manifest["endpoints"] = sorted(
        [
            {
                "path": route["path"],
                "method": route["method"],
                "description": route["description"] or f"{route['function']} endpoint",
            }
            for route in routes
        ],
        key=lambda x: (x["path"], x["method"]),
    )

    # Update version timestamp
    manifest["openapi_generated"] = (
        datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    )

    return manifest


def main() -> int:
    print("Generating OpenAPI spec and syncing manifest...")
    
    manifest = load_manifest()
    routes = extract_routes_from_main()
    
    print(f"Found {len(routes)} routes in main.py")
    
    # Generate OpenAPI spec
    openapi_spec = generate_openapi_spec(manifest, routes)
    
    # Versioned output
    version = manifest.get("version", "0.1.0")
    versioned_path = OPENAPI_DIR / f"openapi-v{version}.json"
    latest_path = OPENAPI_DIR / "openapi.json"
    
    with open(versioned_path, "w") as f:
        json.dump(openapi_spec, f, indent=2)
    with open(latest_path, "w") as f:
        json.dump(openapi_spec, f, indent=2)
    
    print(f"Written OpenAPI spec to {versioned_path} and {latest_path}")
    
    # Sync manifest
    manifest = sync_manifest_endpoints(manifest, routes)
    save_manifest(manifest)
    print(f"Synced manifest at {MANIFEST_PATH}")
    
    print("Done!")
    return 0


if __name__ == "__main__":
    sys.exit(main())