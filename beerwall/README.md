# Beer wall

Pulls beers straight from brewery websites (name, ABV, style, description,
artwork) and builds `wall.html`, a full-screen board for a TV or browser, plus
`beers.csv` for menu/POS tools. Python 3 standard library only.

How it reads breweries:
- **Shopify shops** (Lost Cause and most UK craft breweries): `follow` reads the
  shop's public product feed, so `update` picks up new beers as they're listed.
- **Any other product page**: `add <url>` reads the page's product tags.
- **Anything else**: `add-manual`.

```sh
python3 beerwall/beerwall.py follow https://www.lostcausebrewing.co.uk   # track a brewery
python3 beerwall/beerwall.py update                                     # pull anything new
python3 beerwall/beerwall.py add https://somebrewery.co.uk/products/a-beer
python3 beerwall/beerwall.py add-manual "Beer Name" --brewery X --abv 5.2 --style Porter --desc "..." --art label.png
python3 beerwall/beerwall.py list
python3 beerwall/beerwall.py remove "Lully"
python3 beerwall/beerwall.py build --title "On Tap"
```

`follow` options: `--collection limited-range` to read a specific shop
collection; `--since 2026-11-01` to skip beers listed before a date.

Files: `beers.json` (wall data, editable by hand), `art/`, `wall.html`, `beers.csv`.
