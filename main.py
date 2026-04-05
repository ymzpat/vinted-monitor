import os
import time
import requests
from urllib.parse import urlparse, parse_qs
from datetime import datetime, timezone

# ------------------ CONFIG ------------------
WEBHOOK_URL = os.environ.get("WEBHOOK_URL")
MAX_PRICE = float(os.environ.get("MAX_PRICE", 23))
CHECK_INTERVAL = int(os.environ.get("CHECK_INTERVAL", 60))
BELOW_MARKET_THRESHOLD = float(os.environ.get("BELOW_MARKET_THRESHOLD", 0.80))

# Optional filters
FILTER_BRAND = os.environ.get("FILTER_BRAND")  # e.g., "Nike"
FILTER_SIZE = os.environ.get("FILTER_SIZE")    # e.g., "M"
FILTER_CONDITION = os.environ.get("FILTER_CONDITION")  # e.g., "Like New"

# Collect search URLs
SEARCH_URLS = []
i = 1
while True:
    url = os.environ.get(f"VINTED_SEARCH_URL_{i}")
    if not url:
        break
    SEARCH_URLS.append(url)
    i += 1

if not SEARCH_URLS:
    single = os.environ.get("VINTED_SEARCH_URL")
    if single:
        SEARCH_URLS.append(single)

# Base API URLs
parsed_base = urlparse(SEARCH_URLS[0]) if SEARCH_URLS else None
BASE_DOMAIN = f"{parsed_base.scheme}://{parsed_base.netloc}" if parsed_base else "https://www.vinted.fr"
VINTED_API = f"{BASE_DOMAIN}/api/v2/catalog/items"
VINTED_ITEM_API = f"{BASE_DOMAIN}/api/v2/items"

# HTTP headers
HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept": "application/json",
    "Accept-Language": "fr-FR",
}

seen_ids = set()
session = requests.Session()

# ------------------ HELPERS ------------------
def get_price(item):
    raw = item.get("price", 9999)
    if isinstance(raw, dict):
        return float(raw.get("amount", 9999))
    return float(raw)

def parse_params(url):
    parsed = urlparse(url)
    params = parse_qs(parsed.query)
    flat = {k: v[0] if len(v) == 1 else v for k, v in params.items()}
    flat["per_page"] = "30"
    flat["order"] = "newest_first"
    return flat

def fetch_items(params):
    try:
        resp = session.get(VINTED_API, headers=HEADERS, params=params, timeout=10)
        resp.raise_for_status()
        return resp.json().get("items", [])
    except Exception as e:
        print(f"Error fetching items: {e}")
        return []

def get_market_price(item):
    try:
        brand_id = item.get("brand_dto", {}).get("id") or item.get("brand_id")
        catalog_id = item.get("catalog_id")
        status_id = item.get("status_id")
        size_id = item.get("size_id")
        if not brand_id and not catalog_id:
            return None

        params = {"per_page": "30", "order": "relevance"}
        if brand_id: params["brand_ids[]"] = brand_id
        if catalog_id: params["catalog_ids[]"] = catalog_id
        if status_id: params["status_ids[]"] = status_id
        if size_id: params["size_ids[]"] = size_id

        resp = session.get(VINTED_API, headers=HEADERS, params=params, timeout=10)
        resp.raise_for_status()
        items = resp.json().get("items", [])
        prices = [get_price(i) for i in items if i.get("id") != item.get("id") and 1 < get_price(i) < 500]
        if len(prices) < 3:
            return None
        return round(sum(prices) / len(prices), 2)
    except Exception:
        return None

def send_alert(item, market_price):
    price = get_price(item)
    title = item.get("title", "Unknown item")
    item_id = item.get("id")
    url = f"{BASE_DOMAIN}/items/{item_id}"
    photos = item.get("photos", [])
    image = photos[0]["url"] if photos else None

    # Discount
    discount = round((1 - price / market_price) * 100) if market_price else 0
    deal_line = f"🔥 {discount}% below market" if market_price else "—"

    # Color coding
    color = 0xFF4500 if discount >= 40 else 0x09B1BA

    embed = {
        "title": title,
        "url": url,
        "color": color,
        "fields": [
            {"name": "Price", "value": f"**{price:.2f}€**", "inline": True},
            {"name": "Brand", "value": item.get("brand_title", "—"), "inline": True},
            {"name": "Size", "value": item.get("size_title", "—"), "inline": True},
            {"name": "Condition", "value": item.get("status", "—"), "inline": True},
            {"name": "Deal", "value": deal_line, "inline": False},
        ],
        "footer": {"text": "Vinted Monitor"},
        "timestamp": datetime.utcnow().isoformat(),
    }
    if image:
        embed["image"] = {"url": image}

    payload = {"content": f"@here [**Buy Now**]({url})", "embeds": [embed]}
    try:
        r = requests.post(WEBHOOK_URL, json=payload, timeout=10)
        r.raise_for_status()
        print(f"Alert sent: {title} — {price}€")
    except Exception as e:
        print(f"Error sending Discord alert: {e}")

# ------------------ MONITOR ------------------
def check(params):
    global seen_ids
    items = fetch_items(params)
    new_items = []

    for item in items:
        item_id = item.get("id")
        price = get_price(item)

        # Filter by max price
        if price > MAX_PRICE:
            continue

        # Apply brand, size, condition filters
        if FILTER_BRAND and item.get("brand_title") != FILTER_BRAND:
            continue
        if FILTER_SIZE and item.get("size_title") != FILTER_SIZE:
            continue
        if FILTER_CONDITION and item.get("status") != FILTER_CONDITION:
            continue

        if item_id and item_id not in seen_ids:
            new_items.append(item)
            seen_ids.add(item_id)

    for item in new_items:
        market_price = get_market_price(item)
        if market_price and (get_price(item) / market_price <= BELOW_MARKET_THRESHOLD):
            send_alert(item, market_price)
        else:
            print(f"Skipped {item.get('title')} — not enough below market or no market data")

# ------------------ MAIN ------------------
def main():
    print("Vinted Monitor starting...")
    print(f"Monitoring {len(SEARCH_URLS)} searches")

    all_params = [parse_params(url) for url in SEARCH_URLS]

    # Initial scan to avoid duplicates
    for params in all_params:
        for item in fetch_items(params):
            if item.get("id"):
                seen_ids.add(item["id"])
    print(f"{len(seen_ids)} existing items indexed. Monitoring starts now.")

    while True:
        print(f"Checking... [{datetime.now().strftime('%H:%M:%S')}]")
        for params in all_params:
            check(params)
            time.sleep(5)
        time.sleep(CHECK_INTERVAL)

if __name__ == "__main__":
    main()
