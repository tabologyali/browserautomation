#!/usr/bin/env python3
"""Mark the portfolio to market and compare against the SPY benchmark.

Usage: python3 scripts/report.py [--no-history]

Fetches live quotes, prints a position table with P&L and weights, and
compares total portfolio return against $40k held in SPY since inception.
Appends a daily snapshot row to data/history.csv unless --no-history.

Note: the benchmark is SPY price return (dividends excluded), which
slightly understates the true bar; cash in the portfolio earns nothing,
which cuts the other way. Good enough for v1; revisit if the gap matters.
"""
import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from quotes import fetch_quote  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
PORTFOLIO = ROOT / "data" / "portfolio.json"
HISTORY = ROOT / "data" / "history.csv"


def main() -> None:
    pf = json.loads(PORTFOLIO.read_text())
    bench = pf["benchmark"]
    start = pf["starting_capital"]

    spy = fetch_quote(bench["symbol"])
    quote_date = spy["time"]

    rows = []
    total_value = pf["cash"]
    total_cost = pf["cash"]
    for pos in pf["positions"]:
        q = fetch_quote(pos["symbol"])
        value = pos["shares"] * q["price"]
        cost = pos["shares"] * pos["cost_basis"]
        rows.append((pos["symbol"], pos["shares"], pos["cost_basis"],
                     q["price"], value, value - cost,
                     (q["price"] / pos["cost_basis"] - 1) * 100))
        total_value += value
        total_cost += cost

    bench_value = start * spy["price"] / bench["inception_price"]
    pf_ret = (total_value / start - 1) * 100
    bench_ret = (bench_value / start - 1) * 100

    print(f"\n{pf['name']} — as of {quote_date} close "
          f"(inception {pf['inception_date']})\n")
    print(f"{'SYM':7s}{'SHARES':>7s}{'COST':>10s}{'PRICE':>10s}"
          f"{'VALUE':>12s}{'P&L':>10s}{'RET%':>8s}{'WT%':>7s}")
    for sym, sh, cb, px, val, pnl, ret in sorted(rows, key=lambda r: -r[4]):
        print(f"{sym:7s}{sh:>7d}{cb:>10.2f}{px:>10.2f}"
              f"{val:>12.2f}{pnl:>+10.2f}{ret:>+8.2f}{val / total_value * 100:>7.1f}")
    print(f"{'CASH':7s}{'':>7s}{'':>10s}{'':>10s}{pf['cash']:>12.2f}"
          f"{'':>10s}{'':>8s}{pf['cash'] / total_value * 100:>7.1f}")
    print(f"\n{'Portfolio:':14s}${total_value:>12,.2f}  ({pf_ret:+.2f}%)")
    print(f"{'SPY bench:':14s}${bench_value:>12,.2f}  ({bench_ret:+.2f}%)")
    print(f"{'Alpha:':14s}{pf_ret - bench_ret:>+14.2f} pp\n")

    if "--no-history" not in sys.argv:
        new_file = not HISTORY.exists()
        with HISTORY.open("a", newline="") as f:
            w = csv.writer(f)
            if new_file:
                w.writerow(["date", "portfolio_value", "benchmark_value",
                            "portfolio_return_pct", "benchmark_return_pct",
                            "alpha_pp"])
            w.writerow([quote_date, f"{total_value:.2f}", f"{bench_value:.2f}",
                        f"{pf_ret:.4f}", f"{bench_ret:.4f}",
                        f"{pf_ret - bench_ret:.4f}"])


if __name__ == "__main__":
    main()
