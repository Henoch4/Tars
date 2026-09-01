#!/usr/bin/env python3
"""
Generate versioned OpenAPI export and sync with manifest.json.

Run after any endpoint changes: `python scripts/generate_openapi.py`
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
MANIFEST_PATH = REPO / "manifest.json"
OPENAPI_DIR = REPO / "openapi"
OPENAPI_DIR.mkdir(exist_ok=True)


def load_manifest() -> dict:
    with open(MANIFEST_PATH) as f:
        return json.load(f)


def save_manifest(manifest: dict) -> None:
    with open(MANIFEST_PATH, "w") as f:
        json.dump(manifest, f, indent=2)


def extract_routes_from_main() -> list[dict]:
    """Extract all FastAPI routes from main.py."""
    main_path = REPO / "src" / "main.py"
    if not main_path.exists():
        return []
    
    content = main_path.read_text()
    routes = []
    
    # Simple regex-based extraction of @router.get/@app.get etc.
    import re
    
    # Match route decorators
    pattern = r'@(?:app|router|vault_router)\.(get|post|put|delete|patch)\(["\']([^"\']+)["\']'
    matches = re.findall(pattern, content)
    
    for method, path in matches:
        # Find the function name and docstring
        func_pattern = rf'@(?:app|router|vault_router)\.{method}\(["\']{re.escape(path)}["\'][^)]*\)\s*\n(?:async\s+)?def\s+(\w+)'
        func_match = re.search(func_pattern, content)
        func_name = func_match.group(1) if func_match else "unknown"
        
        # Extract description from docstring
        desc = ""
        doc_pattern = rf'(?:async\s+)?def\s+{func_name}\s*\([^)]*\)\s*:\s*\n\s*"""([^"]+)"""'
        doc_match = re.search(doc_pattern, content)
        if doc_match:
            desc = doc_match.group(1).strip()
        
        routes.append({
            "path": path,
            "method": method.upper(),
            "description": desc or f"{func_name} endpoint",
            "function": func_name,
        })
    
    return routes


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
    """Sync manifest endpoints with actual routes."""
    # Keep existing manifest endpoints but add missing ones
    existing_paths = {(e["path"], e["method"]) for e in manifest.get("endpoints", [])}
    
    for route in routes:
        key = (route["path"], route["method"])
        if key not in existing_paths:
            manifest.setdefault("endpoints", []).append({
                "path": route["path"],
                "method": route["method"],
                "description": route["description"],
            })
            existing_paths.add(key)
    
    # Sort endpoints for consistent ordering
    manifest["endpoints"] = sorted(
        manifest.get("endpoints", []),
        key=lambda x: (x["path"], x["method"])
    )
    
    # Update version timestamp
    manifest["openapi_generated"] = datetime.utcnow().isoformat() + "Z"
    
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