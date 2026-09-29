#!/usr/bin/env python3
"""
scrape_hddbroker.py

Scrapes current equipment listings from HDDBroker.com across the main
equipment categories (drills, trenchers, plows, locators, auger boring,
vacuum systems, mud mixing/recycling), then diffs the result against
the previous run to flag new listings, price changes, and delisted items.

Writes:
    data/latest.json          - full current snapshot + this run's changes
    data/history/<date>.json  - dated copy, kept so trends can be charted later

Being a reasonable guest on someone else's server:
    - Only fetches search-results pages, never every individual listing page
    - Waits REQUEST_DELAY_SECONDS between requests
    - Sends a descriptive User-Agent with a contact address
    - Runs once a day (via the scheduled GitHub Action), not continuously

This script is for MTI Equipment's own internal market-monitoring use.
Don't repurpose it to republish or resell HDDBroker's listing data --
see the README for the relevant excerpt from their Terms & Conditions.
"""

import json
import os
import re
import sys
import time
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import requests
from bs4 import BeautifulSoup

BASE_URL = "https://www.hddbroker.com/en/listings/search.php"
LISTING_URL = "https://www.hddbroker.com/en/listings/viewlisting.php?id={id}"
USER_AGENT = "MTIEquipmentMarketWatch/1.0 (internal price-tracking tool; contact: {contact})"
REQUEST_DELAY_SECONDS = 2.5
LISTINGS_PER_PAGE = 50

# Category IDs confirmed directly from hddbroker.com's own category links.
# This covers the main equipment categories (matches what you described:
# drills, trenchers, plows, vac/mud systems). HDDBroker has ~25 categories
# total, including things like Drill Parts, Tooling, and Trailers, which
# are accessories rather than machines -- add their catid here if you want
# them too (find catid=NN in the URL when you click that category on
# hddbroker.com/en/listings/browsecategory.php).
CATEGORIES = {
    1: "HDD Drills",
    9: "Trenchers",
    48: "Plows",
    7: "Electronics / Locators",
    46: "Auger Boring / Tunnelling",
    10: "Vacuum Systems",
    2: "Mud Mixing Systems / Tanks",
    43: "Mud Recycling Systems",
}

# Known manufacturers, longest name first, so multi-word names ("Ditch
# Witch", "American Augers") get matched whole instead of splitting on
# the first word alone. Pulled from HDDBroker's own manufacturer filter.
MANUFACTURERS = sorted([
    "Akkerman", "American Augers", "Astec Underground", "Barbco", "Bobcat",
    "Bron", "Case", "Caterpillar", "Connectra", "Delta", "DigiTrak",
    "Ditch Witch", "Dynatel", "EGT", "Elgin", "Ellis Williams", "Felling",
    "FMC Technologies", "Gardner Denver", "HammerHead", "Inrock",
    "International", "John Deere", "Kem-Tron", "Kubota", "Marais",
    "McElroy", "McLaughlin", "Melfred Borzall", "Metrotech", "Mincon",
    "Morbark", "Mud Technology International", "Power Mole", "Prime Drilling",
    "Radiodetection", "Richmond", "Ring-O-Matic", "Ritmo", "Roose",
    "Straightline", "Subsite", "Super Products", "Tesmec", "Tibban",
    "Tornado Global Hydrovacs", "Toro", "Towmaster", "Contrail", "Trencor",
    "Tri-Flo", "TT Technologies", "Tracto-Technik", "Tulsa Rig Iron",
    "Universal", "Vac-Con", "Vac-Tron", "Vactor", "Vermeer", "Wyo-Ben",
    "Yanmar",
], key=len, reverse=True)

TITLE_RE = re.compile(
    r"^(?P<year>(?:19|20)\d{2})\s+(?P<makemodel>.+?)\s+Ref\s*#\s*(?P<ref>\d+)\s*$"
)
HOURS_RE = re.compile(r"([\d,]+)\s*hours?\b", re.IGNORECASE)
PRICE_RE = re.compile(r"\$[\d,]+(?:\.\d+)?\s*USD(?:\s*o\.b\.o\.)?", re.IGNORECASE)
CALL_FOR_PRICING_RE = re.compile(r"call\s+for\s+pricing", re.IGNORECASE)
STATUS_WORDS = {"new", "price reduced", "updated", "featured"}


@dataclass
class Listing:
    ref_id: str
    year: Optional[int]
    manufacturer: Optional[str]
    model: Optional[str]
    title: str
    category: str
    description: str
    hours: Optional[int]
    price_usd: Optional[int]
    price_display: str
    status: Optional[str]
    url: str


def split_manufacturer(makemodel: str):
    """'Ditch Witch JT4020 Mach 1' -> ('Ditch Witch', 'JT4020 Mach 1')"""
    for mfr in MANUFACTURERS:
        if makemodel.lower().startswith(mfr.lower()):
            model = makemodel[len(mfr):].strip()
            return mfr, (model or None)
    parts = makemodel.split(" ", 1)
    if len(parts) == 2:
        return parts[0], parts[1]
    return makemodel, None


def parse_price(text: str):
    if CALL_FOR_PRICING_RE.search(text):
        return None, "Call for pricing"
    m = PRICE_RE.search(text)
    if not m:
        return None, text.strip()
    raw = m.group(0)
    digits = re.sub(r"[^\d]", "", raw.split("USD")[0])
    value = int(digits) if digits else None
    return value, raw.strip()


def parse_listings_from_text(page_text: str, category: str):
    """
    Pure-text parser: given the linearized visible text of a search-results
    page -- BeautifulSoup's soup.get_text(separator="\\n"), or an equivalent
    plain-text rendering -- return a list of Listing objects.

    Kept separate from the HTTP/HTML fetching on purpose, so it can be
    unit-tested against saved fixture text with no network connection.
    See scraper/test_parser.py.
    """
    lines = [ln.strip() for ln in page_text.split("\n")]
    lines = [ln for ln in lines if ln and ln != "*"]

    title_positions = []
    for i, ln in enumerate(lines):
        m = TITLE_RE.match(ln)
        if m:
            title_positions.append((i, m))

    listings = []
    for idx, (pos, m) in enumerate(title_positions):
        status = None
        for back in range(1, 4):
            j = pos - back
            if j < 0:
                break
            if lines[j].lower() in STATUS_WORDS:
                status = lines[j]
                break

        end = title_positions[idx + 1][0] if idx + 1 < len(title_positions) else len(lines)
        block = [b for b in lines[pos + 1:end] if b.lower() != "info check"]
        # The next listing's status word (e.g. "New") sits on its own line
        # right before that listing's title, which puts it inside *this*
        # slice -- trim it back off before treating the last line as price.
        while block and block[-1].lower() in STATUS_WORDS:
            block.pop()

        if block:
            price_source = block[-1]
            description = " ".join(block[:-1])
        else:
            price_source = ""
            description = ""

        hours = None
        hm = HOURS_RE.search(description)
        if hm:
            hours = int(hm.group(1).replace(",", ""))

        price_usd, price_display = parse_price(price_source)

        year = int(m.group("year"))
        makemodel = m.group("makemodel").strip()
        ref_id = m.group("ref")
        manufacturer, model = split_manufacturer(makemodel)

        listings.append(Listing(
            ref_id=ref_id,
            year=year,
            manufacturer=manufacturer,
            model=model,
            title=f"{year} {makemodel}",
            category=category,
            description=description,
            hours=hours,
            price_usd=price_usd,
            price_display=price_display,
            status=status,
            url=LISTING_URL.format(id=ref_id),
        ))

    return listings


def get_total_listings(html: str) -> int:
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text(separator="\n")
    m = re.search(r"Found\s+([\d,]+)\s+listings", text, re.IGNORECASE)
    return int(m.group(1).replace(",", "")) if m else 0


def fetch_page_html(session: requests.Session, category_id: int, page: int) -> str:
    params = {"catid": category_id, "lpp": LISTINGS_PER_PAGE, "cp": page}
    resp = session.get(BASE_URL, params=params, timeout=30)
    resp.raise_for_status()
    return resp.text


def scrape_category(session: requests.Session, category_id: int, category_name: str):
    all_listings = []
    page = 1
    total = None
    while True:
        html = fetch_page_html(session, category_id, page)
        soup = BeautifulSoup(html, "html.parser")
        text = soup.get_text(separator="\n")

        if total is None:
            total = get_total_listings(html)
            print(f"  {category_name}: {total} listings found")

        page_listings = parse_listings_from_text(text, category_name)
        if not page_listings:
            break
        all_listings.extend(page_listings)

        total_pages = -(-total // LISTINGS_PER_PAGE) if total else page
        if page >= total_pages:
            break
        page += 1
        time.sleep(REQUEST_DELAY_SECONDS)

    return all_listings


def scrape_all(contact_email: str):
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT.format(contact=contact_email)})

    all_listings = []
    for category_id, category_name in CATEGORIES.items():
        print(f"Scraping {category_name} (catid={category_id})...")
        try:
            all_listings.extend(scrape_category(session, category_id, category_name))
        except requests.RequestException as exc:
            print(f"  WARNING: failed to fetch {category_name}: {exc}", file=sys.stderr)
        time.sleep(REQUEST_DELAY_SECONDS)

    return all_listings


def load_previous_snapshot(path: Path):
    if not path.exists():
        return {}
    data = json.loads(path.read_text())
    return {item["ref_id"]: item for item in data.get("listings", [])}


def diff_snapshots(previous: dict, current_listings: list):
    previous_ids = set(previous.keys())
    current_ids = {l.ref_id for l in current_listings}

    new_ids = current_ids - previous_ids
    removed_ids = previous_ids - current_ids

    price_drops, price_increases = [], []
    for listing in current_listings:
        prev = previous.get(listing.ref_id)
        if prev and prev.get("price_usd") is not None and listing.price_usd is not None:
            if listing.price_usd < prev["price_usd"]:
                price_drops.append(listing.ref_id)
            elif listing.price_usd > prev["price_usd"]:
                price_increases.append(listing.ref_id)

    return {
        "new_ids": sorted(new_ids),
        "removed_ids": sorted(removed_ids),
        "price_drop_ids": price_drops,
        "price_increase_ids": price_increases,
    }


def main():
    contact_email = os.environ.get("CONTACT_EMAIL", "info@mtiequipment.example")
    root = Path(__file__).resolve().parent.parent
    data_dir = root / "data"
    history_dir = data_dir / "history"
    data_dir.mkdir(exist_ok=True)
    history_dir.mkdir(exist_ok=True)
    latest_path = data_dir / "latest.json"

    previous = load_previous_snapshot(latest_path)

    print("Starting HDDBroker scrape...")
    listings = scrape_all(contact_email)
    print(f"Done. {len(listings)} total listings scraped.")

    changes = diff_snapshots(previous, listings)
    now = datetime.now(timezone.utc)

    snapshot = {
        "last_updated": now.isoformat(),
        "source": "hddbroker.com",
        "categories_tracked": list(CATEGORIES.values()),
        "total_listings": len(listings),
        "changes_since_last_run": changes,
        "listings": [asdict(l) for l in listings],
    }

    latest_path.write_text(json.dumps(snapshot, indent=2))
    (history_dir / f"{now.strftime('%Y-%m-%d')}.json").write_text(json.dumps(snapshot, indent=2))

    print(f"New: {len(changes['new_ids'])} | Price drops: {len(changes['price_drop_ids'])} | "
          f"Removed: {len(changes['removed_ids'])}")


if __name__ == "__main__":
    main()
