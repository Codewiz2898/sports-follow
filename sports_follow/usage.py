"""What each source spent this hour and today, by lane (adapters/budget.py), and what it may spend.

    .venv/bin/python -m sports_follow.usage
"""

from __future__ import annotations

from .adapters import budget


def main() -> None:
    rows = budget.usage(budget.known())
    print(f"{'source':12} {'lane':8} {'sent: hour':>10} {'today':>7} {'refused: hour':>14} {'today':>7}")
    for r in rows:
        print(f"{r['source']:12} {r['lane']:8} {r['sent_h']:>10} {r['sent_d']:>7} {r['refused_h']:>14} {r['refused_d']:>7}")
    if not rows:
        print("No source requests counted today.")
    print()
    for b in budget.known():
        if not b.windows:
            print(f"{b.name}: no published limit")
            continue
        limits = ", ".join(f"{w.requests} {budget.per(w.seconds)}" for w in b.windows)
        reserve = f"; {b.reserve} kept for live games and fans" if b.reserve else ""
        share = f"; fans at most {b.fan} {budget.per(b.windows[0].seconds)}" if b.fan is not None else ""
        print(f"{b.name}: {limits}{reserve}{share}")


if __name__ == "__main__":
    main()
