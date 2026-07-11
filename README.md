# 40K vs S&P (UK book)

A paper portfolio: £40,000 starting capital in LSE-listed stocks, investment
trusts, and ETFs, benchmarked head-to-head against the S&P 500 as a UK
investor would actually own it — 374.82 units of VUSA.L (Vanguard S&P 500
UCITS ETF, GBP) bought with the same £40,000 at the same inception
(2026-07-10, £106.69). The mandate: beat the S&P, after real trading costs.

**This is paper trading.** No real money moves. Each business-day morning a
briefing states exactly what to do that day (usually: nothing), so the
portfolio can be mirrored at a real UK broker.

## Trading cost model

Both the portfolio and the benchmark pay costs:

- **Commission:** £9.95 per trade
- **Stamp duty (SDRT):** 0.5% on purchases of UK-incorporated shares.
  Irish-domiciled ETFs, ETCs (e.g. SGLN), and non-UK-incorporated
  companies (e.g. Glencore, Jersey) are exempt. No stamp duty on sells.
- Fills at last close; dividends excluded on both sides (for now).

Costs make churn expensive: a round trip in a UK share costs ~£20 + 0.5%.
The default daily action is therefore **no action**.

## Layout

| Path | What it is |
|---|---|
| `data/portfolio.json` | Current holdings, cash, cost config, benchmark units |
| `data/trades.csv` | Append-only trade log: price, commission, stamp, rationale |
| `data/history.csv` | Snapshots: portfolio vs benchmark vs alpha |
| `journal/` | Decision journal + daily briefings |
| `scripts/quotes.py` | Live quote fetcher (Yahoo Finance; handles GBp pence) |
| `scripts/report.py` | Mark-to-market report + benchmark comparison |

## Usage

```sh
python3 scripts/report.py              # current standings, appends to history
python3 scripts/report.py --no-history # peek without recording a snapshot
python3 scripts/quotes.py RR.L AZN.L   # spot quotes
```

No dependencies beyond the Python 3 standard library.

## Rules

1. Fills at last close only — no intraday hindsight.
2. Every trade logs commission + stamp duty at the time it's made.
3. Position changes get a journal entry explaining the reasoning.
4. The benchmark is fixed at inception and never redefined, especially
   not when losing.
5. Default daily action is no action; trades need a reason strong enough
   to pay ~£10-30 in costs.
