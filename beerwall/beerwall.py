#!/usr/bin/env python3
"""Beer wall updater: pulls beers straight from brewery websites.

For each beer it keeps the name, ABV, style, description and artwork, stores
them in beers.json, downloads the art, and renders wall.html (a full-screen
board for a TV or browser) plus beers.csv.

Sources, in order of preference:
  * Shopify brewery shops (most UK craft breweries, e.g. Lost Cause): read via
    the shop's public product feed, which is exactly what powers their site.
  * Any other product page: read from its schema.org / Open Graph tags.
  * Anything else: add it by hand with `add-manual`.

  python3 beerwall/beerwall.py follow https://www.lostcausebrewing.co.uk
  python3 beerwall/beerwall.py update
  python3 beerwall/beerwall.py add https://brewery.example/products/some-beer
  python3 beerwall/beerwall.py add-manual "Name" --abv 5.2 --style Porter --desc "..." --art label.png
  python3 beerwall/beerwall.py list | remove NAME | build --title "On Tap"

Standard library only.
"""
import argparse
import csv
import datetime as dt
import html
import json
import re
import shutil
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
DB = HERE / "beers.json"
ART = HERE / "art"
WALL = HERE / "wall.html"
CSV_OUT = HERE / "beers.csv"
UA = "Mozilla/5.0 (compatible; beerwall/1.0)"
ABV_RE = re.compile(r"(\d{1,2}(?:\.\d{1,2})?)\s*%")
# Products in a beer collection that aren't a single beer.
NOT_A_BEER = re.compile(r"\b(mixed|gift|voucher|bundle|box|case of|selection|t-?shirt|hoodie|glass|merch|tote|cap)\b", re.I)


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
        except urllib.error.HTTPError:
            raise
        except Exception as e:  # noqa: BLE001  network blips
            if attempt == 2:
                raise
            print(f"  retrying {url} ({e})", file=sys.stderr)
            time.sleep(2 * (attempt + 1))


def fetch_json(url):
    try:
        return json.loads(fetch(url))
    except (urllib.error.HTTPError, ValueError):
        return None


def text(fragment):
    """HTML -> plain text, keeping paragraph breaks."""
    s = re.sub(r"<br\s*/?>|</p>|</div>|</h\d>|</li>", "\n", fragment or "", flags=re.I)
    s = re.sub(r"<[^>]+>", "", s)
    s = html.unescape(s).replace("\xa0", " ")
    lines = [re.sub(r"\s+", " ", ln).strip() for ln in s.splitlines()]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def site_root(url):
    if "://" not in url:
        url = "https://" + url
    u = urllib.parse.urlparse(url)
    return f"{u.scheme}://{u.netloc}"


# ---------------------------------------------------------------- parsing

def split_title(title, fallback_style=""):
    """'Lully / 4% / Citra Pale Ale' or 'Wuzza Wuzza | 4.8% | DDH Pale Ale' -> name, abv, style."""
    parts = [p.strip() for p in re.split(r"\s[/|–—-]\s|\s*\|\s*", title) if p.strip()]
    abv, rest = "", []
    for p in parts:
        m = ABV_RE.fullmatch(p.replace("ABV", "").strip())
        tail = re.search(r"\s+(\d{1,2}(?:\.\d{1,2})?)\s*%\s*(?:ABV)?$", p)  # 'Name 6.5%'
        if m and not abv:
            abv = m.group(1) + "%"
        elif tail and not abv:
            abv = tail.group(1) + "%"
            rest.append(p[: tail.start()].strip())
        else:
            rest.append(p)
    name = rest[0] if rest else title
    style = rest[-1] if len(rest) > 1 else fallback_style
    return name, abv, style


def abv_from(text_):
    m = ABV_RE.search(text_ or "")
    return m.group(1) + "%" if m else ""


def from_shopify(p, root, vendor=""):
    name, abv, style = split_title(p["title"], p.get("product_type", ""))
    desc = text(p.get("body_html", ""))
    imgs = p.get("images") or []
    return {
        "id": f"{urllib.parse.urlparse(root).netloc}:{p['id']}",
        "name": name,
        "brewery": p.get("vendor") or vendor,
        "style": style or p.get("product_type", ""),
        "abv": abv or abv_from(desc),
        "description": desc,
        "art_url": imgs[0]["src"] if imgs else "",
        "link": f"{root}/products/{p['handle']}",
        "source_date": (p.get("published_at") or p.get("created_at") or "")[:10],
    }


def shopify_products(root, collection):
    """All products in a Shopify collection (paged), or None if not a Shopify shop."""
    out, page = [], 1
    while True:
        path = f"/collections/{collection}/products.json" if collection else "/products.json"
        data = fetch_json(f"{root}{path}?limit=250&page={page}")
        if data is None or "products" not in data:
            return None if page == 1 else out
        out += data["products"]
        if len(data["products"]) < 250:
            return out
        page += 1


def from_page(url):
    """Any product page: Shopify product feed first, then JSON-LD / Open Graph."""
    root = site_root(url)
    if "/products/" in url:
        p = fetch_json(url.split("?")[0].rstrip("/") + ".json")
        if p and "product" in p:
            return from_shopify(p["product"], root)
    page = fetch(url)
    beer = {"name": "", "brewery": "", "style": "", "abv": "", "description": "", "art_url": ""}
    for block in re.findall(r'<script[^>]+application/ld\+json[^>]*>(.*?)</script>', page, re.S):
        try:
            data = json.loads(block)
        except ValueError:
            continue
        items = data if isinstance(data, list) else data.get("@graph", [data])
        for it in items:
            if isinstance(it, dict) and "Product" in str(it.get("@type")):
                img = it.get("image")
                img = img[0] if isinstance(img, list) else img
                img = img.get("url") if isinstance(img, dict) else img
                brand = it.get("brand")
                beer.update(name=it.get("name", ""), description=text(it.get("description", "")),
                            art_url=img or "", brewery=brand.get("name", "") if isinstance(brand, dict) else (brand or ""))
                break

    def meta(prop):
        m = re.search(rf'<meta[^>]+(?:property|name)="{prop}"[^>]+content="([^"]*)"', page) or \
            re.search(rf'<meta[^>]+content="([^"]*)"[^>]+(?:property|name)="{prop}"', page)
        return html.unescape(m.group(1)) if m else ""
    beer["name"] = beer["name"] or meta("og:title")
    beer["description"] = beer["description"] or meta("og:description")
    beer["art_url"] = beer["art_url"] or meta("og:image")
    beer["brewery"] = beer["brewery"] or meta("og:site_name")
    name, abv, style = split_title(beer["name"])
    beer.update(name=name, abv=abv or abv_from(beer["name"] + " " + beer["description"]), style=style,
                id=url.split("?")[0], link=url.split("?")[0], source_date="")
    return beer


# ---------------------------------------------------------------- art

def download_art(beer):
    url = beer.get("art_url") or ""
    if not url:
        beer.setdefault("art", "")
        return
    if url.startswith("//"):
        url = "https:" + url
    ART.mkdir(exist_ok=True)
    ext = Path(urllib.parse.urlparse(url).path).suffix.lower() or ".jpg"
    dest = ART / (re.sub(r"[^\w.-]", "_", beer["id"]) + ext)
    if not dest.exists():
        try:
            dest.write_bytes(fetch(url, binary=True))
        except Exception as e:  # noqa: BLE001
            print(f"  couldn't download art for {beer['name']}: {e}", file=sys.stderr)
            beer["art"] = ""
            return
    beer["art"] = f"art/{dest.name}"


# ---------------------------------------------------------------- commands

def add_beers(db, beers):
    have = {b["id"] for b in db["beers"]}
    added = []
    for b in beers:
        if b["id"] in have:
            continue
        print(f"  + {b['name']}  ({b['style'] or '?'}, {b['abv'] or '?% '})")
        download_art(b)
        b["added_to_wall"] = dt.date.today().isoformat()
        db["beers"].insert(0, b)
        have.add(b["id"])
        added.append(b)
    return added


def cmd_follow(db, a):
    root = site_root(a.site)
    coll = a.collection
    if coll is None:  # guess: a 'beer' collection if there is one, else everything
        coll = next((c for c in ("beer", "beers", "all-beer", "all") if shopify_products(root, c)), "")
    if shopify_products(root, coll) is None:
        sys.exit(f"{root} doesn't look like a Shopify shop (or collection '{coll}' is wrong).\n"
                 f"Use `add <product page url>` per beer, or `add-manual`.")
    entry = {"site": root, "collection": coll, "since": a.since or ""}
    db["breweries"] = [x for x in db["breweries"] if x["site"] != root] + [entry]
    print(f"Following {root} (collection: {coll or 'all products'})")
    save(db)
    cmd_update(db, a)


def cmd_update(db, a):
    total = 0
    for br in db["breweries"]:
        print(f"Checking {br['site']}")
        products = shopify_products(br["site"], br["collection"]) or []
        beers = [from_shopify(p, br["site"]) for p in products]
        beers = [b for b in beers if b["abv"] and not NOT_A_BEER.search(b["name"])]
        if br.get("since"):
            beers = [b for b in beers if b["source_date"] >= br["since"]]
        beers.sort(key=lambda b: b["source_date"])  # oldest first -> newest ends up on top
        total += len(add_beers(db, beers))
    print(f"{total} new beer(s).")
    save(db)
    cmd_build(db, a)


def cmd_add(db, a):
    for url in a.urls:
        print(f"Fetching {url}")
        beer = from_page(url)
        if not beer["name"]:
            print("  couldn't read a beer from that page; try add-manual", file=sys.stderr)
            continue
        if not add_beers(db, [beer]):
            print("  already on the wall")
    save(db)
    cmd_build(db, a)


def cmd_add_manual(db, a):
    beer = {"id": "manual:" + re.sub(r"\W+", "-", f"{a.brewery}-{a.name}".lower()).strip("-"),
            "name": a.name, "brewery": a.brewery, "style": a.style,
            "abv": (a.abv.rstrip("%") + "%") if a.abv else "", "description": a.desc,
            "art_url": a.art if a.art and a.art.startswith("http") else "", "link": "", "source_date": ""}
    if a.art and not a.art.startswith("http"):
        src = Path(a.art).expanduser()
        ART.mkdir(exist_ok=True)
        dest = ART / (re.sub(r"[^\w.-]", "_", beer["id"]) + src.suffix.lower())
        shutil.copyfile(src, dest)
        beer["art"] = f"art/{dest.name}"
    db["beers"] = [b for b in db["beers"] if b["id"] != beer["id"]]
    add_beers(db, [beer])
    save(db)
    cmd_build(db, a)


def cmd_remove(db, a):
    key = a.beer.lower()
    hits = [b for b in db["beers"] if b["id"].lower() == key or key in b["name"].lower()]
    if len(hits) != 1:
        print(f"{len(hits)} matches: " + ", ".join(b["name"] for b in hits) if hits else "no match")
        return
    db["beers"].remove(hits[0])
    print(f"Removed {hits[0]['name']}")
    save(db)
    cmd_build(db, a)


def cmd_list(db, a):
    for b in db["beers"]:
        print(f"{b['name']:<32} {b['abv']:>6}  {b['style']:<28} [{b['brewery']}]")
    print(f"{len(db['beers'])} beer(s); following: " + (", ".join(x["site"] for x in db["breweries"]) or "none"))


def cmd_build(db, a=None):
    if getattr(a, "title", None):
        db["title"] = a.title
        save(db)
    cards = []
    for b in db["beers"]:
        art = f'<img src="{html.escape(b["art"])}" alt="">' if b.get("art") else '<div class="noart">🍺</div>'
        desc = "".join(f"<p>{html.escape(p)}</p>" for p in b["description"].split("\n") if p.strip())
        meta = " · ".join(html.escape(x) for x in (b["style"], b["brewery"]) if x)
        cards.append(f"""
    <article class="beer">
      <div class="art">{art}</div>
      <div class="info">
        <div class="top"><h2>{html.escape(b['name'])}</h2><span class="abv">{html.escape(b['abv'])}</span></div>
        <div class="meta">{meta}</div>
        <div class="desc">{desc}</div>
      </div>
    </article>""")
    WALL.write_text(TEMPLATE.replace("{{TITLE}}", html.escape(db["title"]))
                    .replace("{{CARDS}}", "".join(cards))
                    .replace("{{UPDATED}}", dt.date.today().strftime("%d %b %Y")))
    with CSV_OUT.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["name", "brewery", "style", "abv", "description", "artwork", "link"])
        for b in db["beers"]:
            w.writerow([b["name"], b["brewery"], b["style"], b["abv"], b["description"], b.get("art", ""), b.get("link", "")])
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
  .art img { width:100%; height:100%; object-fit:cover; display:block; }
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
    p = argparse.ArgumentParser(description="Keep a beer wall up to date from brewery websites.")
    sub = p.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("follow", help="track a brewery's Shopify shop and pull its beers")
    f.add_argument("site", help="brewery website, e.g. https://www.lostcausebrewing.co.uk")
    f.add_argument("--collection", help="shop collection to read (default: guess 'beer', else all products)")
    f.add_argument("--since", help="only beers published on the shop on/after YYYY-MM-DD")
    sub.add_parser("update", help="pull new beers from every followed brewery")
    ad = sub.add_parser("add", help="add beers by product page URL (any website)")
    ad.add_argument("urls", nargs="+")
    m = sub.add_parser("add-manual", help="add a beer by hand")
    m.add_argument("name")
    m.add_argument("--brewery", default="")
    m.add_argument("--abv", default="")
    m.add_argument("--style", default="")
    m.add_argument("--desc", default="")
    m.add_argument("--art", help="image file path or URL")
    r = sub.add_parser("remove", help="take a beer off the wall (name)")
    r.add_argument("beer")
    sub.add_parser("list")
    b = sub.add_parser("build", help="re-render wall.html / beers.csv")
    b.add_argument("--title", help="heading shown on the wall")
    a = p.parse_args()
    db = load()
    {"follow": cmd_follow, "update": cmd_update, "add": cmd_add, "add-manual": cmd_add_manual,
     "remove": cmd_remove, "list": cmd_list, "build": cmd_build}[a.cmd](db, a)


if __name__ == "__main__":
    main()
