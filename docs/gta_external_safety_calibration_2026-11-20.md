# GTA External Safety Calibration — 2026-11-20

## Scope

Read-only calibration of the GoPlus and RugCheck adapters against two GTA
candidate mints. No signing, broadcasting, wallet access, or live service
activation occurred.

## Result

Both providers were reachable from the VPS and returned parseable responses.
Neither returned an explicit, fresh tax/honeypot verdict for either candidate.

| Evidence source | Candidate 1 | Candidate 2 |
|---|---|---|
| GoPlus | unknown: missing result | unknown: missing result |
| RugCheck | unknown: no explicit verdict | unknown: no explicit verdict |
| Combined external evidence | unknown: incomplete | unknown: incomplete |

## Gate decision

The adapters are operational, but provider reachability is not safety proof.
External evidence remains corroboration only. Missing or ambiguous tax,
honeypot, or sellability fields remain hard blockers.

The sixth shadow and all live execution remain disabled until each candidate
has complete independent evidence from chain inspection and an authoritative
sellability source or validated round-trip path.

## Next action

Evaluate a stronger sellability source or a separately approved, funded
simulation path. Do not reinterpret these provider results as zero tax or
non-honeypot.
