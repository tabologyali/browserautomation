#!/usr/bin/env python3
"""Beer wall updater.

Pulls beers from Untappd (artwork, ABV, style, description) for any brewery
or individual beer, keeps them in beers.json, downloads the label art, and
renders wall.html: a full-screen board for a TV or browser.

  python3 beerwall/beerwall.py follow Lost_Cause_Brewing_Co   # track a brewery
  python3 beerwall/beerwall.py update                         # pull new beers from tracked breweries
  python3 beerwall/beerwall.py add https://untappd.com/b/<slug>/<id>  # any single beer
  python3 beerwall/beerwall.py list
  python3 beerwall/beerwall.py remove "A Very Happy Ending"
  python3 beerwall/beerwall.py build                          # re-render wall.html only

Standard library only.
"""
import argparse
import csv
import datetime as dt
import html
import json
import re
import sys
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
DB = HERE / "beers.json"
ART = HERE / "art"
WALL = HERE / "wall.html"
CSV_OUT = HERE / "beers.csv"
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"
BASE = "https://untappd.com"


# ---------------------------------------------------------------- storage

def load():
    if DB.exists():
        return json.loads(DB.read_text())
    return {"title": "On Tap", "breweries": [], "beers": []}


def save(db):
    DB.write_text(json.dumps(db, indent=2, ensure_ascii=False) + "\n")


# ---------------------------------------------------------------- fetching

def fetch(url, binary=False):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                data = r.read()
                return data if binary else data.decode("utf-8", "replace")
        except Exception as e:  # noqa: BLE001
            if attempt == 2:
                raise
            print(f"  retrying {url} ({e})", file=sys.stderr)
            time.sleep(2 * (attempt + 1))


def text(fragment):
    """HTML fragment -> plain text, keeping paragraph breaks."""
    s = re.sub(r"<br\s*/?>", "\n", fragment)
    s = re.sub(r"<a [^>]*class=\"read-less[^\"]*\"[^>]*>.*?</a>", "", s, flags=re.S)
    s = re.sub(r"<[^>]+>", "", s)
    s = html.unescape(s)
    lines = [ln.strip() for ln in s.splitlines()]
    s = "\n".join(lines)
    return re.sub(r"\n{3,}", "\n\n", s).strip()


def first(pattern, s, default=""):
    m = re.search(pattern, s, re.S)
    return text(m.group(1)) if m else default


def hd_art(url):
    """Untappd small label URL -> HD label URL (falls back to the original)."""
    m = re.match(r"(https://assets\.untappd\.com/site/)beer_logos/(beer-[^/]+?)_sm\.(\w+)$", url or "")
    return f"{m.group(1)}beer_logos_hd/{m.group(2)}_hd.{m.group(3)}" if m else url


def brewery_slug(arg):
    """Accepts 'Lost_Cause_Brewing_Co', 'https://untappd.com/Lost_Cause_Brewing_Co[/beer]' or '/w/x/123'."""
    arg = arg.strip().rstrip("/")
    arg = re.sub(r"^https?://(www\.)?untappd\.com", "", arg)
    arg = re.sub(r"/beer$", "", arg)
    return arg.lstrip("/")


def parse_brewery_list(page, brewery_name):
    beers = []
    for block in re.split(r'<div class="beer-item[^"]*" data-bid="', page)[1:]:
        bid = block.split('"', 1)[0]
        name = first(r'<p class="name"><a[^>]*>(.*?)</a>', block)
        link = re.search(r'<p class="name"><a href="([^"]+)"', block)
        img = re.search(r'<a class="label"[^>]*>\s*<img src="([^"]+)"', block)
        desc = first(r'<p class="desc desc-full-\d+"[^>]*>(.*?)</p>', block) or \
            first(r'<p class="desc desc-half-\d+"[^>]*>(.*?)</p>', block)
        added = first(r'<div class="details-item date">(.*?)</div>', block).replace("Added", "").strip()
        beers.append({
            "id": bid,
            "name": name,
            "brewery": brewery_name,
            "style": first(r'<p class="style">(.*?)</p>', block),
            "abv": first(r'<div class="details-item abv">(.*?)</div>', block).replace("ABV", "").strip(),
            "description": desc,
            "art_url": hd_art(img.group(1)) if img else "",
            "untappd": BASE + link.group(1) if link else "",
            "untappd_added": us_date(added),
        })
    return beers


def us_date(s):
    try:
        return dt.datetime.strptime(s, "%m/%d/%y").date().isoformat()
    except ValueError:
        return ""


def parse_beer_page(page, url):
    m = re.search(r'/b/[^/]+/(\d+)', url)
    img = re.search(r'https://assets\.untappd\.com/site/beer_logos_hd/[^"\s]+', page) or \
        re.search(r'<a class="label[^"]*"[^>]*>\s*<img src="([^"]+)"', page)
    art = img.group(0) if img and img.re.pattern.startswith("https") else (hd_art(img.group(1)) if img else "")
    return {
        "id": m.group(1) if m else url,
        "name": first(r'<div class="name">\s*<h1>(.*?)</h1>', page),
        "brewery": first(r'<p class="brewery">\s*<a[^>]*>(.*?)</a>', page),
        "style": first(r'<div class="name">.*?<p class="style">(.*?)</p>', page),
        "abv": first(r'<p class="abv">(.*?)</p>', page).replace("ABV", "").strip(),
        "description": first(r'<div class="beer-descrption-read-less"[^>]*>(.*?)<a ', page),
        "art_url": art,
        "untappd": url.split("?")[0],
        "untappd_added": "",
    }


def download_art(beer):
    url = beer.get("art_url") or ""
    if not url or "default" in url:
        beer["art"] = ""
        return
    ART.mkdir(exist_ok=True)
    ext = url.rsplit(".", 1)[-1].split("?")[0][:5] or "jpg"
    dest = ART / f"{beer['id']}.{ext}"
    if not dest.exists():
        try:
            dest.write_bytes(fetch(url, binary=True))
        except Exception:  # HD missing -> try the small one
            small = url.replace("beer_logos_hd", "beer_logos").replace("_hd.", "_sm.")
            dest.write_bytes(fetch(small, binary=True))
    beer["art"] = f"art/{dest.name}"


# ---------------------------------------------------------------- commands

def add_beers(db, beers):
    have = {b["id"] for b in db["beers"]}
    added = []
    for b in beers:
        if b["id"] in have:
            continue
        print(f"  + {b['name']}  ({b['style']}, {b['abv']})")
        download_art(b)
        b["added_to_wall"] = dt.date.today().isoformat()
        db["beers"].insert(0, b)
        have.add(b["id"])
        added.append(b)
    return added


def cmd_follow(db, a):
    slug = brewery_slug(a.brewery)
    entry = {"slug": slug, "since": a.since or ""}
    db["breweries"] = [x for x in db["breweries"] if x["slug"] != slug] + [entry]
    print(f"Following {slug}" + (f" (beers added to Untappd since {a.since})" if a.since else ""))
    save(db)
    if not a.no_update:
        cmd_update(db, a)


def cmd_update(db, a):
    total = 0
    for br in db["breweries"]:
        url = f"{BASE}/{br['slug']}/beer?sort=created_at_desc"
        print(f"Checking {url}")
        page = fetch(url)
        name = first(r'<div class="name">\s*<h1>(.*?)</h1>', page) or br["slug"].replace("_", " ")
        beers = parse_brewery_list(page, name)
        if br.get("since"):
            beers = [b for b in beers if not b["untappd_added"] or b["untappd_added"] >= br["since"]]
        if getattr(a, "limit", None):
            beers = beers[: a.limit]
        # oldest first so the newest ends up at the top of the wall
        total += len(add_beers(db, list(reversed(beers))))
    print(f"{total} new beer(s).")
    save(db)
    cmd_build(db, a)


def cmd_add(db, a):
    for url in a.urls:
        print(f"Fetching {url}")
        beer = parse_beer_page(fetch(url), url)
        if not beer["name"]:
            print("  couldn't read a beer from that page", file=sys.stderr)
            continue
        if not add_beers(db, [beer]):
            print("  already on the wall")
    save(db)
    cmd_build(db, a)


def find(db, key):
    key = key.lower()
    return [b for b in db["beers"] if b["id"] == key or key in b["name"].lower()]


def cmd_remove(db, a):
    hits = find(db, a.beer)
    if len(hits) != 1:
        print(f"{len(hits)} matches: " + ", ".join(b["name"] for b in hits) if hits else "no match")
        return
    db["beers"].remove(hits[0])
    print(f"Removed {hits[0]['name']}")
    save(db)
    cmd_build(db, a)


def cmd_list(db, a):
    for b in db["beers"]:
        print(f"{b['id']:>9}  {b['name']:<32} {b['abv']:>6}  {b['style']}  [{b['brewery']}]")
    print(f"{len(db['beers'])} beer(s); following: " + ", ".join(x["slug"] for x in db["breweries"]))


def cmd_build(db, a=None):
    if getattr(a, "title", None):
        db["title"] = a.title
        save(db)
    cards = []
    for b in db["beers"]:
        art = f'<img src="{html.escape(b["art"])}" alt="">' if b.get("art") else '<div class="noart">🍺</div>'
        desc = "".join(f"<p>{html.escape(p)}</p>" for p in b["description"].split("\n") if p.strip())
        cards.append(f"""
    <article class="beer">
      <div class="art">{art}</div>
      <div class="info">
        <div class="top"><h2>{html.escape(b['name'])}</h2><span class="abv">{html.escape(b['abv'])}</span></div>
        <div class="meta">{html.escape(b['style'])} · {html.escape(b['brewery'])}</div>
        <div class="desc">{desc}</div>
      </div>
    </article>""")
    WALL.write_text(TEMPLATE.replace("{{TITLE}}", html.escape(db["title"]))
                    .replace("{{CARDS}}", "".join(cards))
                    .replace("{{UPDATED}}", dt.date.today().strftime("%d %b %Y")))
    with CSV_OUT.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["name", "brewery", "style", "abv", "description", "artwork", "untappd"])
        for b in db["beers"]:
            w.writerow([b["name"], b["brewery"], b["style"], b["abv"], b["description"], b.get("art", ""), b["untappd"]])
    print(f"Wrote {WALL.relative_to(HERE.parent)} and {CSV_OUT.relative_to(HERE.parent)} ({len(db['beers'])} beers)")


TEMPLATE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{{TITLE}}</title>
<style>
  :root { --bg:#111; --card:#1c1c1c; --ink:#f4efe6; --muted:#b5ab9a; --accent:#f2a516; }
  * { box-sizing:border-box; margin:0; }
  body { background:var(--bg); color:var(--ink); font-family: system-ui, -apple-system, "Segoe UI", sans-serif; padding:2vw; }
  header { display:flex; justify-content:space-between; align-items:baseline; margin-bottom:1.5vw; }
  header h1 { font-size:clamp(28px,3.2vw,72px); letter-spacing:.04em; text-transform:uppercase; }
  header span { color:var(--muted); font-size:clamp(12px,.9vw,20px); }
  .grid { display:grid; grid-template-columns:repeat(auto-fill,minmax(min(100%,520px),1fr)); gap:1.2vw; }
  .beer { display:flex; gap:1.2vw; background:var(--card); border-radius:14px; padding:1vw; min-height:0; }
  .art { flex:0 0 clamp(100px,10vw,220px); aspect-ratio:1; border-radius:10px; overflow:hidden; background:#000; }
  .art img { width:100%; height:100%; object-fit:contain; display:block; }
  .noart { display:grid; place-items:center; height:100%; font-size:4vw; }
  .info { min-width:0; display:flex; flex-direction:column; gap:.4vw; }
  .top { display:flex; justify-content:space-between; gap:1vw; align-items:baseline; }
  h2 { font-size:clamp(18px,1.6vw,40px); line-height:1.1; }
  .abv { color:var(--accent); font-weight:800; font-size:clamp(18px,1.5vw,38px); white-space:nowrap; }
  .meta { color:var(--accent); font-size:clamp(12px,.95vw,22px); text-transform:uppercase; letter-spacing:.05em; }
  .desc { color:var(--muted); font-size:clamp(12px,.85vw,20px); line-height:1.35; overflow:hidden;
          display:-webkit-box; -webkit-line-clamp:6; -webkit-box-orient:vertical; }
  .desc p + p { margin-top:.4em; }
</style></head>
<body>
  <header><h1>{{TITLE}}</h1><span>Updated {{UPDATED}}</span></header>
  <main class="grid">{{CARDS}}
  </main>
</body></html>
"""


def main():
    p = argparse.ArgumentParser(description="Keep a beer wall up to date from Untappd.")
    sub = p.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("follow", help="track a brewery (Untappd slug or URL) and pull its beers")
    f.add_argument("brewery")
    f.add_argument("--since", help="only beers added to Untappd on/after YYYY-MM-DD")
    f.add_argument("--limit", type=int, help="only the N newest beers")
    f.add_argument("--no-update", action="store_true")
    u = sub.add_parser("update", help="pull new beers from every tracked brewery")
    u.add_argument("--limit", type=int, help="only the N newest per brewery")
    ad = sub.add_parser("add", help="add individual beers by Untappd beer URL")
    ad.add_argument("urls", nargs="+")
    r = sub.add_parser("remove", help="take a beer off the wall (name or id)")
    r.add_argument("beer")
    sub.add_parser("list")
    b = sub.add_parser("build", help="re-render wall.html / beers.csv")
    b.add_argument("--title", help="heading shown on the wall")
    a = p.parse_args()
    db = load()
    {"follow": cmd_follow, "update": cmd_update, "add": cmd_add, "remove": cmd_remove,
     "list": cmd_list, "build": cmd_build}[a.cmd](db, a)


if __name__ == "__main__":
    main()
