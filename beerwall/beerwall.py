#!/usr/bin/env python3
"""Beer wall updater: pulls beers straight from brewery websites.

For each beer it keeps the name, ABV, style, description and artwork, stores
them in beers.json, downloads the art, and renders wall.html (a full-screen
board for a TV or browser) plus beers.csv.

Reads the brewery's own shop feed (Shopify, WooCommerce or Squarespace), keeps
in-stock beers only, and skips merch, spirits, tickets and packs. Other sites:
`add <beer page url>` or `add-manual`.

  python3 beerwall/beerwall.py search "Brew York"            # find a brewery's site
  python3 beerwall/beerwall.py follow https://brewyork.co.uk --name "Brew York"
  python3 beerwall/beerwall.py update                        # pull new beers
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
UA = "beerwall/1.0"
ABV_RE = re.compile(r"(\d{1,2}(?:\.\d{1,2})?)\s*%")
# Products in a beer collection that aren't a single beer.
NOT_A_BEER = re.compile(r"\b(mixed|gift|voucher|bundle|box|case of|cases?|selection|t-?shirt|hoodie|glass|tankard|"
                        r"merch|tote|cap|hat|beanie|poster|print|sauce|pack|set|subscription|credit|advent|calendar|hamper|tasting|tour|"
                        r"ticket|admission|early bird|experience|launch|event|gin|rum|vodka|whisky|whiskey|\d+cl)\b", re.I)
PROMO_RE = re.compile(r"£|basket|special deal|free (delivery|shipping)|discount code|add to cart|^\[.*\]$", re.I)
SIZE_RE = re.compile(r"^\d+\s*(ml|cl|l|litre|pint|oz)\b|^(can|bottle|keg|cask)$", re.I)


# ---------------------------------------------------------------- storage

def load():
    if DB.exists():
        return json.loads(DB.read_text())
    return {"title": "On Tap", "breweries": [], "beers": []}


def save(db):
    DB.write_text(json.dumps(db, indent=2, ensure_ascii=False) + "\n")


# ---------------------------------------------------------------- fetching

def fetch(url, binary=False, tries=3, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    for attempt in range(tries):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                data = r.read()
                return data if binary else data.decode("utf-8", "replace")
        except urllib.error.HTTPError:
            raise
        except Exception as e:  # noqa: BLE001  network blips
            if attempt == tries - 1:
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
    lines = [ln for ln in lines if not PROMO_RE.search(ln)]
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


GENERIC_TYPES = {"", "beer", "beers", "can", "cans", "single", "core", "product"}


def from_handle(handle, name):
    """'440ml-faith-hazy-pale-ale-5-0' -> ('5.0%', 'Hazy Pale Ale'); some shops only put these in the URL."""
    m = re.search(r"-(\d{1,2})-(\d{1,2})$", handle)
    if not m:
        return "", ""
    rest = re.sub(r"^\d+(ml|cl)-", "", handle[: m.start()])
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    rest = rest[len(slug) + 1:] if slug and rest.startswith(slug + "-") else ""
    return f"{m.group(1)}.{m.group(2)}%", rest.replace("-", " ").title()


def from_shopify(p, root, vendor=""):
    ptype = p.get("product_type", "")
    name, abv, style = split_title(p["title"], "" if ptype.lower() in GENERIC_TYPES else ptype)
    if name.isupper() and len(name) > 3:  # 'FAITH' -> 'Faith'
        name = name.title().replace("'S", "'s").replace("’S", "’s")
    desc = text(p.get("body_html", ""))
    h_abv, h_style = from_handle(p.get("handle", ""), name)
    imgs = p.get("images") or []
    return {
        "id": f"{urllib.parse.urlparse(root).netloc}:{p['id']}",
        "name": name,
        "brewery": p.get("vendor") or vendor,
        "style": style or h_style or ("" if ptype.lower() in GENERIC_TYPES else ptype),
        "abv": abv or abv_from(desc) or h_abv,
        "description": desc,
        "art_url": imgs[0]["src"] if imgs else "",
        "link": f"{root}/products/{p['handle']}",
        "source_date": (p.get("published_at") or p.get("created_at") or "")[:10],
        "in_stock": any(v.get("available", True) for v in p.get("variants") or [{}]),
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


def info_line(desc):
    """Find a 'American Pale Ale | 4.3% | 440ml | Gluten Free' style line -> (abv, style, remaining text)."""
    lines = desc.split("\n")
    for i, ln in enumerate(lines[:4]):
        if ABV_RE.search(ln) and ("|" in ln or "/" in ln or "·" in ln or len(ln) < 60):
            parts = [p.strip() for p in re.split(r"\s*[|/·•]\s*|\s+[–—-]\s+", ln) if p.strip()]
            abv = next((ABV_RE.search(p).group(1) + "%" for p in parts if ABV_RE.search(p)), "")
            # style is the first other part; 'Barley Wine 13.0%' -> 'Barley Wine'
            cleaned = [ABV_RE.sub("", p).replace("ABV", "").strip(" -–") for p in parts]
            style = next((c for c in cleaned if c and not SIZE_RE.search(c)), "")
            if len(style) > 50:  # that was a sentence, not an info line
                continue
            if abv:
                return abv, style, "\n".join(lines[:i] + lines[i + 1:]).strip()
    return abv_from(desc), "", desc


def woo_beer(root, pid, title, body, img, link, date, brewery, in_stock=True, has_category=True):
    abv, style, desc = info_line(text(body))
    name, t_abv, t_style = split_title(html.unescape(title))
    name = re.sub(r"\s*[–-]\s*(pre-?order|sold out|new)\s*$", "", name, flags=re.I)
    return {"id": f"{urllib.parse.urlparse(root).netloc}:{pid}", "name": name, "brewery": brewery,
            "style": style or t_style, "abv": abv or t_abv, "description": desc,
            "art_url": img or "", "link": link, "source_date": (date or "")[:10],
            "in_stock": in_stock, "has_category": has_category}


def woo_products(root, collection=""):
    """WooCommerce (WordPress) shops. Tries the Store API, then the WordPress product API."""
    site = fetch_json(f"{root}/wp-json/") or {}
    brewery = html.unescape(site.get("name", "")) or urllib.parse.urlparse(root).netloc
    out, page = [], 1
    while True:  # Store API
        q = f"&category={collection}" if collection else ""
        data = fetch_json(f"{root}/wp-json/wc/store/v1/products?per_page=100&page={page}{q}")
        if not isinstance(data, list):
            break
        out += [woo_beer(root, p["id"], p["name"], p.get("description") or p.get("short_description", ""),
                         (p.get("images") or [{}])[0].get("src"), p.get("permalink", ""), "", brewery,
                         p.get("is_in_stock", True), bool(p.get("categories"))) for p in data]
        if len(data) < 100:
            return out
        page += 1
    if out:
        return out
    page = 1
    while True:  # WordPress REST fallback (works even when the Store API is switched off)
        data = fetch_json(f"{root}/wp-json/wp/v2/product?per_page=100&page={page}&_embed=wp:featuredmedia,wp:term")
        if not isinstance(data, list):
            return out if page > 1 else None
        for p in data:
            emb = p.get("_embedded", {})
            terms = {t.get("slug") for grp in emb.get("wp:term", []) for t in grp if isinstance(t, dict)}
            if collection and collection not in terms:
                continue
            img = (emb.get("wp:featuredmedia") or [{}])[0].get("source_url")
            body = p["content"]["rendered"] or p.get("excerpt", {}).get("rendered", "")
            cls = p.get("class_list") or []
            cls = list(cls.values()) if isinstance(cls, dict) else cls
            out.append(woo_beer(root, p["id"], p["title"]["rendered"], body, img, p.get("link", ""), p.get("date"), brewery,
                                "outofstock" not in cls, any(c.startswith("product_cat-") for c in cls) or not cls))
        if len(data) < 100:
            return out
        page += 1


def squarespace_products(root, collection="shop"):
    """Squarespace shops: every page has a ?format=json twin."""
    url, out, brewery = f"{root}/{collection or 'shop'}?format=json", [], ""
    while url:
        data = fetch_json(url)
        if not data or "items" not in data:
            return out or None
        brewery = brewery or data.get("website", {}).get("siteTitle", "")
        for it in data["items"]:
            abv, style, desc = info_line(text(it.get("body") or it.get("excerpt", "")))
            name, t_abv, t_style = split_title(it.get("title", ""))
            date = dt.datetime.utcfromtimestamp(it["publishOn"] / 1000).date().isoformat() if it.get("publishOn") else ""
            out.append({"id": f"{urllib.parse.urlparse(root).netloc}:{it['id']}", "name": name, "brewery": brewery,
                        "style": style or t_style, "abv": abv or t_abv, "description": desc,
                        "art_url": it.get("assetUrl", ""), "link": root + it.get("fullUrl", ""), "source_date": date})
        nxt = (data.get("pagination") or {}).get("nextPageUrl")
        url = f"{root}{nxt}&format=json" if nxt else None
    return out


def detect_platform(root):
    """Which kind of shop is this? -> (platform, default collection) or (None, reason)."""
    if fetch_json(f"{root}/products.json?limit=1") is not None:
        coll = next((c for c in ("beer", "beers", "all-beer") if shopify_products(root, c)), "")
        return "shopify", coll
    if isinstance(fetch_json(f"{root}/wp-json/wp/v2/product?per_page=1"), list) or \
            isinstance(fetch_json(f"{root}/wp-json/wc/store/v1/products?per_page=1"), list):
        return "woocommerce", ""
    try:
        home = fetch(root)
    except Exception as e:  # noqa: BLE001
        return None, f"couldn't load the site ({e})"
    if "squarespace" in home.lower():
        for coll in ("shop", "beer", "store", "beers"):
            items = squarespace_products(root, coll)
            if items:
                return "squarespace", coll
        return None, "Squarespace site, but its shop has no products listed"
    return None, "no product feed found (not Shopify, WooCommerce or Squarespace)"


def products_for(br):
    platform = br.get("platform", "shopify")
    if platform == "woocommerce":
        return woo_products(br["site"], br.get("collection", "")) or []
    if platform == "squarespace":
        return squarespace_products(br["site"], br.get("collection", "")) or []
    return [from_shopify(p, br["site"]) for p in (shopify_products(br["site"], br.get("collection", "")) or [])]


STOP_WORDS = {"brewing", "brewery", "brewers", "brew", "co", "company", "beer", "the", "ltd", "and"}


def candidate_sites(name):
    words = re.sub(r"[^a-z0-9 ]", "", name.lower().replace("&", " and ")).split()
    core = "".join(w for w in words if w not in STOP_WORDS)
    bases = {"".join(words), core} | {core + s for s in ("brewing", "brewery", "brewingco", "brewco", "brew", "beer", "brewers")}
    return [f"https://{www}{b}{tld}" for b in sorted(x for x in bases if x)
            for tld in (".co.uk", ".com", ".uk", ".beer", ".co") for www in ("www.", "")]


def probe(url):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=8) as r:
            return site_root(r.geturl()), r.read(400_000).decode("utf-8", "replace")
    except Exception:  # noqa: BLE001  most guesses don't exist
        return None


def cmd_search(db, a):
    from concurrent.futures import ThreadPoolExecutor
    print(f"Looking for {a.name!r}'s website...")
    core = [w for w in re.findall(r"[a-z0-9]+", a.name.lower()) if w not in STOP_WORDS]
    with ThreadPoolExecutor(16) as pool:
        hits = [h for h in pool.map(probe, candidate_sites(a.name)) if h]
    seen, found = set(), 0
    for root, page in hits:
        host = urllib.parse.urlparse(root).netloc.removeprefix("www.")
        m = re.search(r"<title[^>]*>(.*?)</title>", page, re.S)
        title = text(m.group(1))[:80] if m else ""
        hay = (title + " " + host).lower()
        if host in seen or not all(w in hay for w in core) or not re.search(r"\b(beers?|brew\w*|ales?)\b", title, re.I):
            continue  # someone else's site, or not a brewery
        seen.add(host)
        platform, coll = detect_platform(root)
        found += bool(platform)
        print(f"  {root}  \u201c{title}\u201d\n      " +
              (f"\u2714 {platform} \u2014 beers can be pulled" if platform else f"\u2718 {coll}"))
    if not seen:
        print("  No website found by guessing. If you know it: follow <website>")
    elif found:
        print("\nTo add one: python3 beerwall/beerwall.py follow <website>")


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

ART_SIZE = 600  # px; plenty for a TV card, keeps the wall light


def shrink(data):
    """Resize to ART_SIZE and save as JPEG if Pillow is installed; otherwise keep the original."""
    try:
        import io
        from PIL import Image
    except ImportError:
        return data, None
    try:
        im = Image.open(io.BytesIO(data))
        im.thumbnail((ART_SIZE, ART_SIZE))
        if im.mode in ("RGBA", "LA", "P"):
            im = im.convert("RGBA")
            bg = Image.new("RGB", im.size, (0, 0, 0))
            bg.paste(im, mask=im.split()[-1])
            im = bg
        out = io.BytesIO()
        im.convert("RGB").save(out, "JPEG", quality=85, optimize=True)
        return out.getvalue(), ".jpg"
    except Exception:  # noqa: BLE001  odd formats: keep as is
        return data, None


def download_art(beer):
    url = beer.get("art_url") or ""
    if not url:
        beer.setdefault("art", "")
        return
    if url.startswith("//"):
        url = "https:" + url
    if "cdn.shopify.com" in url:  # Shopify resizes on request
        url += ("&" if "?" in url else "?") + f"width={ART_SIZE}"
    ART.mkdir(exist_ok=True)
    stem = re.sub(r"[^\w.-]", "_", beer["id"])
    existing = next(ART.glob(stem + ".*"), None)
    if existing:
        beer["art"] = f"art/{existing.name}"
        return
    try:
        data = fetch(url, binary=True)
    except Exception as e:  # noqa: BLE001
        print(f"  couldn't download art for {beer['name']}: {e}", file=sys.stderr)
        beer["art"] = ""
        return
    data, ext = shrink(data)
    dest = ART / (stem + (ext or Path(urllib.parse.urlparse(url).path).suffix.lower() or ".jpg"))
    dest.write_bytes(data)
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
    platform, coll = detect_platform(root)
    if not platform:
        sys.exit(f"Can't pull beers from {root}: {coll}.\nUse `add <beer page url>` per beer, or `add-manual`.")
    if a.collection is not None:
        coll = a.collection
    entry = {"site": root, "platform": platform, "collection": coll, "since": a.since or "", "name": a.name or ""}
    db["breweries"] = [x for x in db["breweries"] if x["site"] != root] + [entry]
    print(f"Following {root} ({platform}, collection: {coll or 'all products'})")
    save(db)
    cmd_update(db, a)


def cmd_update(db, a):
    total = 0
    for br in db["breweries"]:
        print(f"Checking {br['site']}")
        beers = products_for(br)
        if br.get("name"):
            for b in beers:
                b["brewery"] = br["name"]
        beers = [b for b in beers if b["abv"] and float(b["abv"].rstrip("%")) <= 20 and b.get("has_category", True)
                 and not NOT_A_BEER.search(b["name"])]
        if not getattr(a, "include_sold_out", False):
            beers = [b for b in beers if b.get("in_stock", True)]
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


def cmd_refresh_art(db, a):
    shutil.rmtree(ART, ignore_errors=True)
    for b in db["beers"]:
        if b.get("art_url"):
            download_art(b)
    save(db)
    cmd_build(db, a)


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
<title>Beer Wall</title>
<meta name="claude:viewport-fit-sides" content="cover">
<style>
  /* Bar-board look, deliberately dark only: label art on charcoal cards, amber for ABV and style */
  :root { --bg:#121110; --card:#1d1b19; --ink:#f4efe6; --muted:#b5ab9a; --accent:#f2a516; color-scheme:dark; }
  * { box-sizing:border-box; margin:0; }
  body { background:var(--bg); color:var(--ink); font-family: system-ui, -apple-system, "Segoe UI", sans-serif;
         padding-block:2vw; padding-left:max(16px, 2vw, env(safe-area-inset-left, 0px)); padding-right:max(16px, 2vw, env(safe-area-inset-right, 0px)); }
  header { display:flex; flex-wrap:wrap; gap:8px 16px; justify-content:space-between; align-items:baseline; margin-bottom:1.5vw; }
  header h1 { font-size:clamp(28px,3.2vw,72px); letter-spacing:.04em; text-transform:uppercase; }
  header span { color:var(--muted); font-size:clamp(12px,.9vw,20px); }
  .grid { display:grid; grid-template-columns:repeat(auto-fill,minmax(min(100%,520px),1fr)); gap:1.2vw; }
  .beer { display:flex; gap:1.2vw; background:var(--card); border-radius:14px; padding:1vw; min-height:0; }
  .art { flex:0 0 clamp(96px,10vw,220px); aspect-ratio:1; max-width:100%; border-radius:10px; overflow:hidden; background:#000; }
  .art img { max-width:100%; width:100%; height:100%; object-fit:cover; display:block; }
  .noart { display:grid; place-items:center; height:100%; font-size:4vw; }
  .info { min-width:0; display:flex; flex-direction:column; gap:.4vw; }
  .top { display:flex; justify-content:space-between; gap:1vw; align-items:baseline; }
  h2 { font-size:clamp(18px,1.6vw,40px); line-height:1.1; text-wrap:balance; overflow-wrap:anywhere; }
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
    s = sub.add_parser("search", help="find a brewery's website by name and check it can be pulled")
    s.add_argument("name")
    f = sub.add_parser("follow", help="track a brewery's website and pull its beers")
    f.add_argument("site", help="brewery website, e.g. https://www.lostcausebrewing.co.uk")
    f.add_argument("--collection", help="shop collection to read (default: guess 'beer', else all products)")
    f.add_argument("--since", help="only beers published on the shop on/after YYYY-MM-DD")
    f.add_argument("--include-sold-out", action="store_true")
    f.add_argument("--name", help="brewery name to show (default: from the website)")
    u = sub.add_parser("update", help="pull new beers from every followed brewery")
    u.add_argument("--include-sold-out", action="store_true")
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
    sub.add_parser("refresh-art", help="re-download all artwork (smaller files)")
    b = sub.add_parser("build", help="re-render wall.html / beers.csv")
    b.add_argument("--title", help="heading shown on the wall")
    a = p.parse_args()
    db = load()
    {"search": cmd_search, "follow": cmd_follow, "update": cmd_update, "add": cmd_add, "add-manual": cmd_add_manual,
     "remove": cmd_remove, "refresh-art": cmd_refresh_art, "list": cmd_list, "build": cmd_build}[a.cmd](db, a)


if __name__ == "__main__":
    main()
