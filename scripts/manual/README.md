# Manual read-only diagnostics

These commands intentionally make public network requests when run. They are
operator diagnostics, not offline tests, and importing them does not make calls.
Run from the repository root:

```powershell
python -m scripts.manual.check_time_gate --help
python -m scripts.manual.hyperliquid_bookwalk --help

# Explicit live read-only probes:
python -m scripts.manual.check_time_gate
python -m scripts.manual.hyperliquid_bookwalk
```

`check_time_gate` prints the current UTC clock offset and window classification,
plus classifications for a fixed set of example hours. It does not change any
window, launch a trading loop, or submit an order.

`hyperliquid_bookwalk` reads BTC/ETH/SOL books and compares book-walk execution
estimates with the existing square-root model. It defaults to
`data/diagnostics/hl_bookwalk_results.json` (ignored runtime output); `--output`
selects another result file. Fee/model assumptions are unchanged.

The former `scripts/test_hl_multi.py` used a removed function and workstation-only
paths. Its original text is retained in the local archive. Use the maintained
single-scenario CLI for additional comparisons:

```powershell
python -m scripts.hyperliquid_slippage --coin SOL --size-usd 10000 --side buy --fetch-live
```
