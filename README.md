# MTI Equipment — Market Watch

A daily-updating dashboard of trenchless/HDD equipment listings, built so you don't have
to log into three separate sites every morning.

## How the three sites are actually handled

They're not handled the same way, on purpose:

| Site | What this project does | Why |
|---|---|---|
| **HDDBroker.com** | Scrapes it daily (this repo) | No saved-search/alert feature exists there, and their terms allow personal, non-commercial reference use. This is the one site actually worth building a tracker for. |
| **MachineryTrader.com** | Not scraped — quick link + alert setup instructions | Their Terms of Use explicitly prohibit scraping/bots/automated data collection. They also have a genuinely better tool built in: text/email alerts on saved searches, plus a Watch List that flags price drops. |
| **EquipmentTrader.com** | Not scraped — quick link + alert setup instructions | Same call: likely similar restrictions (typical for a marketplace this size), it actively blocks non-browser requests, and it has its own price-alert feature you can turn on while searching. |

Practically, this means: **check the dashboard for HDDBroker**, and **let MachineryTrader/EquipmentTrader
text or email you** for the other two. You still end up checking one inbox + one dashboard instead of
three sites — just not one dashboard for all three, because two of them don't allow it and don't need it.

### Setting up the native alerts (a few minutes, once)

**MachineryTrader.com**
1. Create a free account and log in.
2. Click your account name (top left) → **Alerts For New Listings**.
3. Set your criteria — model year, hours, price ceiling, distance — and turn on text and/or email.
4. Optionally also use **Save This Search** on any filtered results page, and add specific machines
   to a **Watch List** to get pinged on price drops.

**EquipmentTrader.com**
1. Run a search with your filters (category, price range, location).
2. Turn on the price-alert toggle on the results page and save it.

## What the scraper actually collects

Eight categories, chosen because they're HDDBroker's core equipment categories (matching what you
described — drills, trenchers, plows, vac/mud systems) rather than parts/accessories:

`HDD Drills`, `Trenchers`, `Plows`, `Electronics / Locators`, `Auger Boring / Tunnelling`,
`Vacuum Systems`, `Mud Mixing Systems / Tanks`, `Mud Recycling Systems`

HDDBroker has more categories than this (Drill Parts, Tooling, Trailers, Miscellaneous, etc.) that
read more like accessories than machines, so they're left out. To add one: open
`hddbroker.com/en/listings/browsecategory.php`, click the category, and copy the `catid=` number
from the URL into the `CATEGORIES` dict at the top of `scraper/scrape_hddbroker.py`.

For each listing it captures: year, manufacturer, model, category, hours, price, HDDBroker's own
New/Price Reduced/Updated tag, and a link to the original listing. It does **not** open every
individual listing page — only the search-results pages — to keep the request count low (roughly
20–30 requests once a day, not hundreds).

## Deploying it (free, no server to maintain)

This runs entirely on GitHub's free tier: **GitHub Actions** scrapes on a schedule and commits the
result, **GitHub Pages** serves the dashboard as a static site that reads that data. No hosting bill,
nothing to keep running yourself.

1. Create a new **public** GitHub repository (Pages' free tier needs public, unless you have GitHub
   Pro/Team/Enterprise) and push this folder to it.
2. In the repo, go to **Settings → Secrets and variables → Actions → Variables** and add a repository
   variable named `CONTACT_EMAIL` set to an email address of yours. This goes in the scraper's
   User-Agent string, so if HDDBroker ever needs to reach out about the traffic, they can — good
   etiquette for an automated visitor.
3. Go to **Settings → Pages**, and under "Build and deployment" choose **Deploy from a branch**,
   branch `main`, folder `/ (root)`. Save. GitHub will give you a URL like
   `https://<your-username>.github.io/<repo-name>/` — that's your dashboard.
4. Go to the **Actions** tab, open "Scrape HDDBroker listings," and click **Run workflow** to trigger
   the first run by hand instead of waiting for the schedule. It'll scrape all eight categories and
   commit `data/latest.json`.
5. After that, it runs automatically every day at 6:00am US Eastern (10:00 UTC) — edit the `cron`
   line in `.github/workflows/scrape.yml` if you want a different time. Note it'll drift to 5am
   Eastern in winter since the cron time is fixed to UTC and doesn't follow Daylight Saving.

The repo ships with `data/latest.json` already populated with ~30 real current listings (gathered
while building this) so the dashboard has something to show immediately — the first Action run
replaces it with a full sweep of all eight categories.

### Testing locally before you deploy

Opening `index.html` directly by double-clicking it won't work — browsers block a plain HTML file
from fetching a local JSON file for security reasons. Instead, from this folder, run:

```
python3 -m http.server 8000
```

and visit `http://localhost:8000` in a browser.

To test the scraper itself:

```
pip install -r requirements.txt
python3 scraper/scrape_hddbroker.py
```

## Adding MachineryTrader / EquipmentTrader listings yourself

Since those two sites can't be scraped, `add-listing.html` gives you a way to add specific finds by
hand — no risk of breaking the data file's formatting:

1. Open `add-listing.html` (in a browser, works locally or once deployed) and open your repo's
   `data/manual-listings.json` in another tab.
2. Paste that file's current content into the box at the top of the tool (leave it as `[]` for your
   first entry).
3. Fill in the listing — title, price, and the **specific listing's URL** (not a search or category
   page) are what matter most.
4. Click **Add to list**. Repeat for as many as you found in one sitting.
5. Copy the result and paste it into `data/manual-listings.json` on GitHub, **replacing everything
   that was there**, then commit.

These show up on the dashboard tagged with their source, filterable just like the HDDBroker listings,
right alongside them.

## Editing anything in this project

Every file here can be edited straight from GitHub's website — no software to install:

1. Open your repository on github.com and click the file you want to change.
2. Click the pencil icon (top right of the file view) to switch it into an editable text box.
3. Make your change, or select all and paste in new content.
4. Scroll down, add a short note if you like, and click **Commit changes**. The live site reflects it
   within a minute or two.



The parsing logic (`parse_listings_from_text` in `scrape_hddbroker.py`) is unit-tested against a real
page captured from HDDBroker — see `scraper/test_parser.py`, which passes against genuine listing
text pulled straight from the site. What that test *can't* cover is the exact HTML structure the live
site will hand back to a plain HTTP request, since that wasn't available to test against directly.
If the first real run comes back with 0 listings, the site's markup likely differs slightly from what
was assumed — the fix is almost always a small tweak to how `get_total_listings()` finds the "Found
N listings" text, not a rewrite. Check the Action's log output first; it prints a per-category count
as it goes.

## Files

```
index.html                        the dashboard (static, no build step)
add-listing.html                  turns a MachineryTrader/EquipmentTrader find into paste-ready JSON
data/latest.json                  current HDDBroker snapshot (scraped automatically)
data/manual-listings.json         MachineryTrader/EquipmentTrader listings you've added by hand
data/history/YYYY-MM-DD.json       one HDDBroker snapshot per day, for trend data later
scraper/scrape_hddbroker.py       the scraper
scraper/test_parser.py            unit test, run against real saved fixture data
scraper/build_seed_data.py        one-off script that built the initial seed data
.github/workflows/scrape.yml      the daily schedule
```

## Scope, for the record

This is for MTI Equipment's own internal market monitoring — seeing what's out there and at what
price. It isn't set up to republish or resell HDDBroker's listing data anywhere, which is the line
their terms draw.
