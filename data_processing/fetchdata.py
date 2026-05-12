"""
fetch_crowd_data.py — Karlskrona Presence Curves Scraper
─────────────────────────────────────────────────────────
Scrapes Google Maps Popular Times for representative locations in Karlskrona,
aggregates by land-use type, and outputs data/presence_curves.csv.
 
Run once before starting app.py:
    pip install LivePopularTimes
    python fetch_crowd_data.py
 
Output: data/presence_curves.csv
  Schema: land_use, day_type, hour (0-23), presence (0.0-1.0)
  This file is a drop-in replacement for the derived baseline CSV.
  To use Telia Crowd Insights or any other source later:
    → Reformat to this schema, save as data/presence_curves.csv, done.
 
Legal note:
  Uses LivePopularTimes which scrapes Google Maps without an API key.
  Google ToS grey area — acceptable for research/hackathon, not for production.
  Source: github.com/GrocerCheck/LivePopularTimes
 
Data strategy:
  - Sample 2-4 locations per land-use type in Karlskrona
  - Average their hourly curves to get a representative cell-level curve
  - Normalize Google's 0-100 scale to 0.0-1.0
  - 0 from Google = closed/no data → treated as 0.02 floor (not truly empty)
"""
 
import csv
import time
from pathlib import Path
 
try:
    from livepopulartimes import get_populartimes_by_address
except ImportError:
    print("[ERROR] LivePopularTimes not installed.")
    print("        Run: pip install LivePopularTimes")
    raise
 
# ── Sample locations per land-use type ────────────────────────────────────────
# Strategy: pick locations that are clearly representative of each land use.
# Multiple samples per type so we average out any single noisy location.
# All addresses in Karlskrona, Sweden.
# Google Popular Times only works on named businesses, not streets/addresses.
# These are real named businesses in Karlskrona whose crowd patterns
# are representative of the surrounding land-use type.
SAMPLE_LOCATIONS = {
    "residential": [
        # Supermarkets/convenience stores in residential areas
        # proxy for neighbourhood activity
        "ICA Nära Lyckeby, Karlskrona, Sweden",
        "Hemköp Karlskrona, Karlskrona, Sweden",
    ],
    "commercial": [
        # Named retail/commercial venues in city centre
        "Wachtmeister, Karlskrona, Sweden",
        "Systembolaget Karlskrona, Karlskrona, Sweden",
        "McDonald's Karlskrona, Karlskrona, Sweden",
    ],
    "industrial": [
        # Named industrial/logistics businesses
        "SAAB Kockums, Karlskrona, Sweden",
        "Karlskrona Hamn, Karlskrona, Sweden",
    ],
    "road": [
        # Petrol stations as proxy for road traffic (always have popular times)
        "Circle K Karlskrona, Karlskrona, Sweden",
        "OKQ8 Karlskrona, Karlskrona, Sweden",
    ],
    "water": [
        # Named waterfront venues
        "Stumholmen, Karlskrona, Sweden",
        "Karlskrona Båtklubb, Karlskrona, Sweden",
    ],
    "forest": [
        # Named recreational venues near forest/parks
        "Kungsmarken naturreservat, Karlskrona, Sweden",
        "Lyckeby IF, Karlskrona, Sweden",
    ],
}
 
# Google uses 0-100 scale. 0 = closed or no data.
# We apply a small floor rather than treating 0 as truly empty.
CLOSED_FLOOR = 0.02
 
# Day names from Google → our day_type
DAY_MAP = {
    "Monday":    "weekday",
    "Tuesday":   "weekday",
    "Wednesday": "weekday",
    "Thursday":  "weekday",
    "Friday":    "weekday",
    "Saturday":  "weekend",
    "Sunday":    "weekend",
}
 
OUTPUT_PATH = Path("data") / "presence_curves.csv"
 
def scrape_location(address: str) -> dict | None:
    """
    Scrapes popular times for a single address.
    Returns dict of {day_type: [24 floats]} or None on failure.
    """
    print(f"    Scraping: {address}")
    try:
        result = get_populartimes_by_address(address)
        pop = result.get("populartimes")
        if not pop:
            print(f"    → No popular times data for this location")
            return None
 
        curves = {}
        for day_data in pop:
            day_name = day_data.get("name", "")
            day_type = DAY_MAP.get(day_name)
            if day_type is None:
                continue
            raw = day_data.get("data", [])
            if len(raw) != 24:
                continue
            # Normalize: 0-100 → 0.0-1.0, apply floor for zeros
            normalized = [
                max(CLOSED_FLOOR, v / 100.0) if v == 0 else v / 100.0
                for v in raw
            ]
            if day_type not in curves:
                curves[day_type] = []
            curves[day_type].append(normalized)
 
        # Average across days of same type
        averaged = {}
        for day_type, arrays in curves.items():
            if not arrays:
                continue
            averaged[day_type] = [
                sum(col) / len(col)
                for col in zip(*arrays)
            ]
        return averaged if averaged else None
 
    except Exception as e:
        print(f"    → Failed: {e}")
        return None
 
 
def scrape_land_use(land_use: str, addresses: list) -> dict:
    """
    Scrapes all addresses for a land-use type, averages them.
    Returns {day_type: [24 floats]}
    """
    print(f"\n[{land_use.upper()}] Scraping {len(addresses)} locations ...")
    all_curves = {"weekday": [], "weekend": []}
 
    for address in addresses:
        result = scrape_location(address)
        if result is None:
            continue
        for day_type, curve in result.items():
            if day_type in all_curves:
                all_curves[day_type].append(curve)
        # Be polite to Google — don't hammer requests
        time.sleep(2)
 
    # Average across all sampled locations
    final = {}
    for day_type, arrays in all_curves.items():
        if not arrays:
            print(f"  [WARN] No data for {land_use}/{day_type} — will use fallback")
            continue
        final[day_type] = [
            round(sum(col) / len(col), 3)
            for col in zip(*arrays)
        ]
        print(f"  [{day_type}] averaged {len(arrays)} locations")
 
    return final
 
 
def write_csv(all_curves: dict, path: Path):
    """
    Writes presence curves to CSV.
    all_curves: {land_use: {day_type: [24 floats]}}
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    for land_use, day_curves in sorted(all_curves.items()):
        for day_type, curve in sorted(day_curves.items()):
            for hour, presence in enumerate(curve):
                rows.append({
                    "land_use": land_use,
                    "day_type": day_type,
                    "hour":     hour,
                    "presence": round(presence, 3),
                })
 
    with open(path, "w", newline="") as f:
        f.write("# PRESENCE CURVES — generated by fetch_crowd_data.py\n")
        f.write("# Source: Google Maps Popular Times via LivePopularTimes scraper\n")
        f.write("# Locations: representative sample per land-use type, Karlskrona SE\n")
        f.write("# Normalization: Google 0-100 → 0.0-1.0, zero floor=0.02\n")
        f.write("# To replace with Telia/RVU data: reformat to this schema, drop in.\n")
        writer = csv.DictWriter(f, fieldnames=["land_use","day_type","hour","presence"])
        writer.writeheader()
        writer.writerows(rows)
 
    print(f"\n[OUTPUT] {len(rows)} rows written → {path}")
 
 
def main():
    print("="*55)
    print("  Karlskrona Presence Curves Scraper")
    print("  Source: Google Maps via LivePopularTimes")
    print("="*55)
 
    all_curves = {}
    failed = []
 
    for land_use, addresses in SAMPLE_LOCATIONS.items():
        result = scrape_land_use(land_use, addresses)
        if result:
            all_curves[land_use] = result
        else:
            failed.append(land_use)
            print(f"  [WARN] {land_use}: no data scraped — "
                  f"baseline CSV values will be used for this type")
 
    if not all_curves:
        print("\n[ERROR] No data scraped at all.")
        print("        Google may be blocking requests or addresses not found.")
        print("        The existing baseline presence_curves.csv will be used.")
        return
 
    write_csv(all_curves, OUTPUT_PATH)
 
    if failed:
        print(f"\n[WARN] These land uses had no scraped data: {failed}")
        print(f"       Their rows will be missing from the CSV.")
        print(f"       app.py will fall back to 0.5 multiplier for these types.")
        print(f"       Consider adding more sample addresses or using baseline CSV.")
 
    print("\nDone. Run app.py to start the server.")
    print(f"Presence curves at: {OUTPUT_PATH}")
 
 
if __name__ == "__main__":
    main()
 