# GoPlus Solana Provider Assessment — 2026-11-19

## Decision

GoPlus authentication is working, but the Solana Token Security endpoint did
not return explicit tax or honeypot fields for the representative calibration
mints tested (GTA candidates, SOL, and USDC). Those results are recorded as
`unknown` with source and observation timestamps.

GoPlus is therefore **insufficient as the sole provider for the hard
transfer-tax/honeypot gate**. It remains a corroborating source only.

## Locked policy

- Missing, unsupported, stale, ambiguous, or conflicting provider fields fail
  closed.
- Simulation/round-trip output is corroboration, never a substitute for an
  authoritative tax/honeypot result.
- A GoPlus score or generic safe label cannot authorize a candidate.
- The sixth shadow is blocked until a second authoritative source (or a
  source-specific deterministic rejection policy) supplies explicit fresh
  outcomes for every candidate.
- Live flags remain disabled and the GTA wallet remains unfunded.

## Evidence retained

- Authentication regression suite: 11 tests passing.
- Canonical SOL/USDC calibration: both explicit tax/honeypot fields absent;
  both classified `unknown`.
- GTA candidate calibrations: unsupported or missing explicit fields;
  classified `unknown`.
