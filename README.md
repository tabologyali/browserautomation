# 40K vs S&P

A paper portfolio: $40,000 starting capital, benchmarked head-to-head against
$40,000 of SPY from the same inception date (2026-07-10, at $754.95/share).
The mandate is simple — beat the S&P 500. Every trade is logged, every
decision journaled, and performance is measured against the benchmark from
day one. No revisionism.

**This is paper trading.** No real money moves. Fills are simulated at the
last regular-session close. If the strategy proves out, it can be mirrored
at a real broker.

## Layout

| Path | What it is |
|---|---|
| `data/portfolio.json` | Current holdings, cash, and cost basis |
| `data/trades.csv` | Append-only trade log with rationale per trade |
| `data/history.csv` | Daily snapshots: portfolio vs benchmark vs alpha |
| `journal/` | Dated decision journal — thesis, changes, mistakes |
| `scripts/quotes.py` | Live quote fetcher (Yahoo Finance chart API) |
| `scripts/report.py` | Mark-to-market report + benchmark comparison |

## Usage

```sh
python3 scripts/report.py              # current standings, appends to history
python3 scripts/report.py --no-history # peek without recording a snapshot
python3 scripts/quotes.py NVDA TSM     # spot quotes
```

No dependencies beyond the Python 3 standard library.

## Rules

1. Fills at last close only — no intraday hindsight.
2. Every trade gets a rationale in `trades.csv` at the time it's made.
3. Position changes get a journal entry explaining the reasoning.
4. Benchmark is fixed: SPY price return from $754.95. It never gets
   redefined, especially not when losing.
