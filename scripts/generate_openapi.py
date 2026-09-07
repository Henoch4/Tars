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
MANIFEST_OVERLAY_PATH = REPO / "manifest.overlay.json"
OPENAPI_DIR = REPO / "openapi"
OPENAPI_DIR.mkdir(exist_ok=True)

sys.path.insert(0, str(REPO))


def load_manifest() -> dict:
    """Load base manifest and merge with overlay (if exists)."""
    with open(MANIFEST_PATH) as f:
        manifest = json.load(f)
    
    # Merge overlay for OKX-specific fields that shouldn't be regenerated
    if MANIFEST_OVERLAY_PATH.exists():
        with open(MANIFEST_OVERLAY_PATH) as f:
            overlay = json.load(f)
        manifest = deep_merge(manifest, overlay)
    
    return manifest


def deep_merge(base: dict, overlay: dict) -> dict:
    """Recursively merge overlay into base."""
    result = base.copy()
    for key, value in overlay.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = value
    return result


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


def get_openapi_schemas() -> dict:
    """Return the OpenAPI component schemas for all request/response models."""
    return {
        "HireRequest": {
            "type": "object",
            "required": ["mode", "profile_mode"],
            "properties": {
                "mode": {
                    "type": "string",
                    "enum": ["own_account"],
                    "default": "own_account",
                    "description": "Only 'own_account' is supported."
                },
                "profile_mode": {
                    "type": "string",
                    "enum": ["demo", "live"],
                    "default": "demo",
                    "description": "Audit mode: 'demo' for testnet data or 'live' for real account."
                },
                "inst_type": {
                    "type": "string",
                    "enum": ["SWAP", "FUTURES", "OPTION"],
                    "description": "Optional filter for the leverage/positions check."
                },
                "balance_data": {
                    "type": "object",
                    "description": "Output of `okx account balance-all --json`. Required for data-forwarding mode (production).",
                    "example": {"data": [{"ccy": "USDT", "availBal": "10000"}]}
                },
                "positions_data": {
                    "type": "array",
                    "description": "Output of `okx account positions --json`. Array of position objects.",
                    "items": {"type": "object"},
                    "example": [{"instId": "BTC-USDT-SWAP", "pos": "0.1", "avgPx": "50000"}]
                }
            },
            "example": {
                "mode": "own_account",
                "profile_mode": "demo",
                "inst_type": "SWAP",
                "balance_data": {"data": [{"ccy": "USDT", "availBal": "10000"}]},
                "positions_data": [{"instId": "BTC-USDT-SWAP", "pos": "0.1", "avgPx": "50000"}]
            }
        },
        "TradeRequest": {
            "type": "object",
            "properties": {
                "assets": {
                    "type": "array",
                    "items": {"type": "string"},
                    "default": ["BTC-USDT-SWAP", "ETH-USDT-SWAP", "SOL-USDT-SWAP"],
                    "description": "Assets to trade"
                },
                "mode": {
                    "type": "string",
                    "enum": ["single", "cycle"],
                    "default": "single",
                    "description": "Single cycle or continuous (cycle not implemented in serverless)"
                },
                "interval_seconds": {
                    "type": "integer",
                    "default": 300,
                    "description": "Interval for continuous mode"
                }
            },
            "example": {
                "assets": ["BTC-USDT-SWAP", "ETH-USDT-SWAP"],
                "mode": "single"
            }
        },
        "KillSwitchRequest": {
            "type": "object",
            "required": ["reason"],
            "properties": {
                "reason": {
                    "type": "string",
                    "minLength": 1,
                    "description": "Why the kill switch is being activated"
                }
            },
            "example": {"reason": "Daily loss limit exceeded"}
        },
        "AuditReport": {
            "type": "object",
            "properties": {
                "total_equity_usd": {"type": "number", "description": "Total account equity in USD"},
                "risk_score": {"type": "integer", "description": "Overall risk score (0-100)"},
                "positions": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "asset": {"type": "string"},
                            "size": {"type": "number"},
                            "leverage": {"type": "number"},
                            "unrealized_pnl": {"type": "number"}
                        }
                    }
                },
                "risk_violations": {
                    "type": "array",
                    "items": {"type": "string"}
                },
                "onchain_decision_hash": {"type": "string", "description": "Onchain decision log transaction hash"},
                "timestamp": {"type": "number", "description": "Unix timestamp of the audit"}
            }
        },
        "TradingCycleResult": {
            "type": "object",
            "properties": {
                "cycle_id": {"type": "string"},
                "timestamp": {"type": "number"},
                "signals": {"type": "array", "items": {"type": "object"}},
                "decisions": {"type": "array", "items": {"type": "object"}},
                "executions": {"type": "array", "items": {"type": "object"}},
                "total_pnl_usd": {"type": "number"},
                "total_fees_usd": {"type": "number"},
                "status": {"type": "string"},
                "errors": {"type": "array", "items": {"type": "string"}},
                "dry_run": {"type": "boolean"}
            }
        },
        "MooveLinkRequest": {
            "type": "object",
            "required": ["to_amount"],
            "properties": {
                "to_amount": {
                    "type": ["string", "number"],
                    "description": "Amount in the settlement token (USDC). Forwarded as string to avoid float rounding."
                },
                "description": {
                    "type": "string",
                    "maxLength": 500,
                    "description": "Shown to the payer on checkout (e.g., 'TARS hire audit')."
                },
                "max_usage": {
                    "type": "integer",
                    "minimum": 1,
                    "description": "Payments accepted before completion. Omit = unlimited."
                },
                "expiration_date": {
                    "type": "string",
                    "format": "date-time",
                    "description": "ISO 8601 future timestamp. Omit = never expires."
                }
            },
            "example": {
                "to_amount": "0.50",
                "description": "TARS hire audit",
                "max_usage": 1
            }
        }
    }


def get_endpoint_specs() -> dict:
    """Return endpoint-specific OpenAPI specs (requestBody, responses, parameters)."""
    return {
        "/hire": {
            "post": {
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": {"$ref": "#/components/schemas/HireRequest"}
                        }
                    }
                },
                "responses": {
                    "200": {
                        "description": "Successful response - returns AuditReport",
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/AuditReport"}
                            }
                        }
                    },
                    "400": {"description": "Bad request - invalid input data"},
                    "401": {"description": "Unauthorized - missing or invalid X-Agent-Token header"},
                    "403": {"description": "Forbidden - live mode not enabled on this deployment"},
                    "429": {"description": "Too Many Requests - rate limit exceeded"},
                    "500": {"description": "Internal server error"},
                    "502": {"description": "Bad Gateway - OKX CLI call failed"}
                }
            }
        },
        "/trade": {
            "post": {
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": {"$ref": "#/components/schemas/TradeRequest"}
                        }
                    }
                },
                "responses": {
                    "200": {
                        "description": "Successful response - returns TradingCycleResult",
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/TradingCycleResult"}
                            }
                        }
                    },
                    "400": {"description": "Bad request"},
                    "401": {"description": "Unauthorized"},
                    "423": {"description": "Locked - kill switch is active"},
                    "429": {"description": "Too Many Requests - rate limit exceeded"},
                    "500": {"description": "Internal server error"},
                    "501": {"description": "Not Implemented - mode='cycle' not supported in serverless"}
                }
            }
        },
        "/kill-switch/activate": {
            "post": {
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": {"$ref": "#/components/schemas/KillSwitchRequest"}
                        }
                    }
                },
                "responses": {
                    "200": {"description": "Successful response"},
                    "400": {"description": "Bad request"},
                    "401": {"description": "Unauthorized"},
                    "500": {"description": "Internal server error"}
                }
            }
        },
        "/api/v1/billing/moove-link": {
            "post": {
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": {"$ref": "#/components/schemas/MooveLinkRequest"}
                        }
                    }
                },
                "responses": {
                    "200": {"description": "Successful response"},
                    "400": {"description": "Bad request"},
                    "401": {"description": "Unauthorized"},
                    "500": {"description": "Internal server error"}
                }
            }
        },
        "/api/v1/vault/attest": {
            "post": {
                "responses": {
                    "200": {"description": "Successful response"},
                    "400": {"description": "Bad request"},
                    "401": {"description": "Unauthorized"},
                    "500": {"description": "Internal server error"},
                    "502": {"description": "Bad Gateway - could not read OKX balance"},
                    "503": {"description": "Service Unavailable - vault not deployed or not configured"}
                }
            }
        },
        "/api/v1/estimate": {
            "get": {
                "parameters": [
                    {
                        "name": "route",
                        "in": "query",
                        "required": True,
                        "schema": {"type": "string"},
                        "description": "Route path to get price estimate for (e.g., /hire, /trade)"
                    }
                ],
                "responses": {
                    "200": {"description": "Successful response"},
                    "400": {"description": "Bad request"},
                    "401": {"description": "Unauthorized"},
                    "500": {"description": "Internal server error"}
                }
            }
        },
        "/audit-stats": {
            "get": {
                "parameters": [
                    {
                        "name": "days",
                        "in": "query",
                        "schema": {"type": "integer", "default": 7, "minimum": 1, "maximum": 30},
                        "description": "Number of days to query"
                    }
                ],
                "responses": {
                    "200": {"description": "Successful response"},
                    "400": {"description": "Bad request"},
                    "401": {"description": "Unauthorized"},
                    "500": {"description": "Internal server error"}
                }
            }
        },
        "/api/v1/billing/moove-links": {
            "get": {
                "parameters": [
                    {
                        "name": "status",
                        "in": "query",
                        "schema": {"type": "string", "enum": ["active", "inactive", "completed"]},
                        "description": "Filter by link status"
                    },
                    {
                        "name": "offset",
                        "in": "query",
                        "schema": {"type": "integer", "default": 0},
                        "description": "Pagination offset"
                    }
                ],
                "responses": {
                    "200": {"description": "Successful response"},
                    "400": {"description": "Bad request"},
                    "401": {"description": "Unauthorized"},
                    "500": {"description": "Internal server error"}
                }
            }
        },
        "/api/v1/billing/moove-link/{link_id}": {
            "get": {
                "parameters": [
                    {
                        "name": "link_id",
                        "in": "path",
                        "required": True,
                        "schema": {"type": "string"},
                        "description": "Payment link ID"
                    }
                ],
                "responses": {
                    "200": {"description": "Successful response"},
                    "400": {"description": "Bad request"},
                    "401": {"description": "Unauthorized"},
                    "500": {"description": "Internal server error"}
                }
            }
        }
    }


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
            "schemas": get_openapi_schemas(),
        },
        "security": [{"AgentToken": []}],
        "tags": [
            {"name": "Trading", "description": "Trade execution and management"},
            {"name": "Audit", "description": "Onchain audit trail queries"},
            {"name": "Risk", "description": "Risk management and statistics"},
            {"name": "Vault", "description": "Depositor-facing vault API"},
            {"name": "System", "description": "Health, manifest, and billing"},
        ],
    }

    endpoint_specs = get_endpoint_specs()

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

        # Apply endpoint-specific overrides (requestBody, custom responses, parameters)
        if path in endpoint_specs and method in endpoint_specs[path]:
            spec["paths"][path][method].update(endpoint_specs[path][method])

    return spec


def sync_manifest_endpoints(manifest: dict, routes: list[dict]) -> dict:
    """Full sync of manifest endpoints with actual routes (I7).

    Endpoints are GENERATED, not curated: missing routes are added,
    removed routes are dropped, and descriptions refresh from docstrings.
    Anything else (name, capabilities, permissions, service) stays hand-maintained.
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

    # Sync manifest (endpoints only; name, service, capabilities preserved from overlay)
    manifest = sync_manifest_endpoints(manifest, routes)
    save_manifest(manifest)
    print(f"Synced manifest at {MANIFEST_PATH}")

    print("Done!")
    return 0


if __name__ == "__main__":
    sys.exit(main())