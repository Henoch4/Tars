# Type safety as trading risk control — the OCaml lesson

*Why the most successful quantitative trading firm runs on a language most
programmers have never used, and what this repo is doing about it.*

## The question

Jane Street — one of the largest quantitative trading firms in the world —
runs its core trading systems on **OCaml**, a language with a fraction of
Python's popularity and none of its hype. Not a research toy: millions of
lines, their own standard library (`Core`), their own concurrency library
(`Async`), and they fund the language's core tooling. They are the largest
commercial OCaml user on earth. Why?

## The history

Jane Street was founded in 2000 and switched to OCaml around 2002–2003,
championed by Yaron Minsky, who later wrote the classic essay
["OCaml for the masses"](https://blog.janestreet.com/ocaml-for-the-masses/).
The choice has compounded for two decades: the codebase, the tooling, and
the hiring pipeline (they teach OCaml to every new hire, including math
graduates who have never programmed) all grew around it.

## What OCaml is

OCaml is from the **ML family** of languages — descended not from business
software but from **theorem provers**. Robin Milner designed ML for the LCF
proof assistant: a language whose type system exists to make it *structurally
impossible to express a false statement*. That heritage is the whole story.
A language born to guarantee that only valid proofs type-check turns out to
be excellent at guaranteeing that only valid trading operations compile.

## The five properties that matter for trading

1. **"Make illegal states unrepresentable."** Jane Street's most-cited
   reason. Trading protocols have states; in some states some messages are
   invalid. Encode the states as variant types and the compiler *rejects*
   code that could send an invalid transition. A whole class of runtime bugs
   fails to compile instead of losing money at 2am.
2. **Type inference.** Full inference means OCaml reads almost as tersely as
   Python while checking like C++. Safety without annotation sludge.
3. **Phantom types.** Values can be tagged at the type level with meaning
   the runtime never sees: `usdt`, `contracts`, `validated_price`. Mixing
   them is a compile error. (Remember this one.)
4. **Predictable performance.** Native compilation, a GC historically tuned
   for low-latency pauses, no JVM-style surprises — matters when quoting
   prices continuously.
5. **The module system.** ML's module language (signatures, functors) enforces
   rigid interfaces across millions of lines — how a few hundred engineers
   keep a giant codebase from becoming soup.

The alternatives lost on the merits as of 2002: C++ (memory-unsafe,
footgun-heavy), Java (GC pauses, weaker types), Haskell (laziness makes
performance reasoning hard), Standard ML (dead ecosystem). Rust did not
exist yet.

## The caveat that keeps this honest

OCaml is a **multiplier, not the edge**. Jane Street wins because of people,
breadth, and infrastructure; the language lets a few hundred engineers write
millions of lines of correct trading code without drowning. It is why they
could scale — not why they are profitable. Tooling amplifies whatever
strategy exists; it substitutes for none. (This repo's validation-gate
results make the same point from the other direction.)

## This repo's tuition payment

The order-size bug was exactly the bug class phantom types exist to prevent.

The executor passed `--sz` as a **USDT dollar amount** where OKX SWAP expects
**contracts**. A $50 test order was ~$100k of notional. The fix (shipped with
regression tests in `tests/test_execution.py`): a runtime `ctVal` lookup via
the public instruments endpoint, fractional lot sizes honored, fail-closed
refusal on missing metadata or a zero contract count.

In OCaml-with-phantom-types, the original line does not compile:

```ocaml
(* usdt_amount : Usdt.t   and   okx expects Contracts.t — different types.
   The bug is a type error, caught before the program ever runs. *)
let place ~size:usdt_amount = OkxCli.order ~sz:usdt_amount (* type error *)
```

The Python approximation, in this repo's priority order:

```python
from typing import NewType
Usdt = NewType("Usdt", float)
Contracts = NewType("Contracts", float)

def to_contracts(usdt: Usdt, ct_val: Usdt, lot_sz: float) -> Contracts:
    ...  # the ONLY place the boundary between units exists

def place(sz: Contracts) -> None: ...   # mixing units is now a mypy error
```

Plus `Enum`/`Literal` states instead of stringly-typed exchange mappings —
`OrderStatus.from_exchange`'s unknown→PENDING fallback is the dynamic
workaround for what OCaml's exhaustive pattern matching does natively — and
mypy strict scoped to the execution package first.

Runtime checks and regression tests stay regardless. Types are
defense-in-depth on the exact path that touches money: they shrink the
*space of possible programs* before any test runs.

## See also

- Yaron Minsky, ["OCaml for the masses"](https://blog.janestreet.com/ocaml-for-the-masses/) (ACM Queue, 2011)
- The regression tests guarding the runtime fix: `tests/test_execution.py`
- The commit that shipped it: `fix(core): execution correctness, risk-gate wiring, onchain receipt ownership`
