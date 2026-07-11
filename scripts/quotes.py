#!/usr/bin/env python3
"""Fetch last-close quotes from Yahoo Finance chart API.

Usage: python3 scripts/quotes.py SPY TSM MU ...
Prints one line per symbol: SYMBOL PRICE DATE (previous regular-session close).
"""
import json
import sys
import time
import urllib.request

UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"}
HOSTS = ["query2.finance.yahoo.com", "query1.finance.yahoo.com"]


def fetch_quote(symbol: str, retries: int = 4) -> dict:
    last_err = None
    for attempt in range(retries):
        host = HOSTS[attempt % len(HOSTS)]
        url = f"https://{host}/v8/finance/chart/{symbol}?interval=1d&range=5d"
        req = urllib.request.Request(url, headers=UA)
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                data = json.load(resp)
            meta = data["chart"]["result"][0]["meta"]
            price = meta["regularMarketPrice"]
            currency = meta.get("currency", "USD")
            if currency == "GBp":  # LSE quotes in pence -> pounds
                price /= 100
                currency = "GBP"
            return {
                "symbol": symbol,
                "price": price,
                "time": time.strftime(
                    "%Y-%m-%d", time.gmtime(meta["regularMarketTime"])
                ),
                "currency": currency,
            }
        except Exception as e:  # noqa: BLE001 - retry on any transient failure
            last_err = e
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"failed to fetch {symbol}: {last_err}")


def main() -> None:
    symbols = sys.argv[1:]
    if not symbols:
        sys.exit("usage: quotes.py SYMBOL [SYMBOL ...]")
    for sym in symbols:
        q = fetch_quote(sym)
        print(f"{q['symbol']:8s} {q['price']:>10.2f}  {q['time']}  {q['currency']}")
        time.sleep(0.5)


if __name__ == "__main__":
    main()
