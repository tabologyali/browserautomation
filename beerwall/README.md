# Beer wall

Pulls beers straight from brewery websites (name, ABV, style, description,
artwork) and builds `wall.html`, a full-screen board for a TV or browser, plus
`beers.csv` for menu/POS tools. Python 3 standard library only.

It reads the public product feed behind the brewery's online shop. Supported
shop types: **Shopify**, **WooCommerce** and **Squarespace**. Only in-stock beers
are added; merch, spirits, event tickets, mixed packs and gift sets are skipped.

## Find any beer, check it, then add it

```sh
python3 beerwall/beerwall.py find "northern monk faith"   # lists matches + writes found.html preview
python3 beerwall/beerwall.py pick 1                        # adds match 1 to the wall
```

`find` searches the breweries you follow (including sold-out beers), the
directory of UK brewery shops in `breweries.json` (44 breweries; add more by
editing it), the brewery's own site if the query starts with a brewery name it
doesn't know yet. Everything comes straight from brewery websites; `--databases`
also searches Open Food Facts (off by default). It shows each match with ABV,
style, description and the link it came from, and writes `found.html` so
you can see the artwork and badge before adding anything. Nothing is added
until you `pick`.

## Follow breweries

```sh
python3 beerwall/beerwall.py search "Northern Monk"          # find a brewery's site + check it's supported
python3 beerwall/beerwall.py follow https://brewyork.co.uk --name "Brew York"
python3 beerwall/beerwall.py update                           # pull anything new from every brewery
python3 beerwall/beerwall.py add https://somebrewery.co.uk/products/a-beer
python3 beerwall/beerwall.py add-manual "Beer Name" --brewery X --abv 5.2 --style Porter --desc "..." --art label.png
python3 beerwall/beerwall.py list
python3 beerwall/beerwall.py remove "Lully"
python3 beerwall/beerwall.py build --title "On Tap"
```

`follow` options: `--name` (brewery name shown), `--collection` (one shop
category), `--since YYYY-MM-DD`, `--include-sold-out`.

Breweries currently followed: Lost Cause (Shopify), Brew York (WooCommerce).
Rivington's website has no beer listings, so add their beers with `add-manual`.

Files: `beers.json` (wall data, editable by hand), `art/`, `wall.html`, `beers.csv`.
