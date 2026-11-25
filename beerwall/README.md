# Beer wall

Pulls beers from Untappd (label artwork, ABV, style, description) and builds
`wall.html`, a full-screen board for a TV or browser, plus `beers.csv` for
importing into a menu or POS tool. Works for any brewery or single beer on
Untappd. Python 3 standard library only.

```sh
# track a brewery (Untappd slug or URL); --since/--limit stop it backfilling old beers
python3 beerwall/beerwall.py follow Lost_Cause_Brewing_Co --since 2026-09-01

# pull anything new from every tracked brewery (run whenever, or on a schedule)
python3 beerwall/beerwall.py update

# add any single beer from any brewery
python3 beerwall/beerwall.py add https://untappd.com/b/<beer-slug>/<id>

python3 beerwall/beerwall.py list
python3 beerwall/beerwall.py remove "Not Dead Yet"      # take one off the wall
python3 beerwall/beerwall.py build --title "On Tap"     # change heading / re-render
```

Files: `beers.json` (the wall's data, editable by hand), `art/` (downloaded
HD labels), `wall.html`, `beers.csv`.

Note: `update` reads the newest ~25 beers on a brewery's Untappd page, which
covers new releases; use `add` for anything older.
