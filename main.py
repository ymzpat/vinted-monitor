import requests
import os
import time
from urllib.parse import urlparse, parse_qs
from datetime import datetime, timezone

WEBHOOK_URL = os.environ.get("WEBHOOK_URL")
MAX_PRICE = float(os.environ.get("MAX_PRICE", 1000))
VERY_CHEAP_THRESHOLD = float(os.environ.get("VERY_CHEAP_THRESHOLD", 20))  # items below 20€ highlighted
POPULAR_BRANDS = os.environ.get("POPULAR_BRANDS", "Nike,Adidas").split(",")
CHECK_INTERVAL = 60

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

parsed_base = urlparse(SEARCH_URLS[0]) if SEARCH_URLS else None
BASE_DOMAIN = f"{parsed_base.scheme}://{parsed_base.netloc}" if parsed_base else "https://www.vinted.com"
VINTED_API = f"{BASE_DOMAIN}/api/v2/catalog/items"

HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept": "application/json",
}

seen_ids = set()
session = requests.Session()


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


def send_alert(item):
    price = get_price(item)
    title = item.get("title", "Unknown")
    item_id = item.get("id")
    url = f"{BASE_DOMAIN}/items/{item_id}"

    tags = item.get("labels") or []
    tag_text = ", ".join(tags) if tags else "No tags"

    brand = item.get("brand_title") or "—"
    condition = item.get("status") or "—"

    # Calculate listing age
    listed_at_ts = item.get("created_at_ts")
    listed_text = "Unknown"
    if listed_at_ts:
        listed_dt = datetime.fromtimestamp(listed_at_ts, tz=timezone.utc)
        delta = datetime.now(timezone.utc) - listed_dt
        minutes = int(delta.total_seconds() / 60)
        listed_text = f"{minutes}m ago" if minutes < 60 else f"{minutes//60}h ago"

    # Color rules
    color = 0x09B1BA  # default
    if price <= VERY_CHEAP_THRESHOLD:
        color = 0xFF4500  # very cheap
    if brand in POPULAR_BRANDS:
        color = 0xFFD700  # popular brand

    payload = {
        "content": f"@here [Buy now]({url})",
        "embeds": [{
            "title": title,
            "url": url,
            "color": color,
            "fields": [
                {"name": "Price", "value": f"{price:.2f}€", "inline": True},
                {"name": "Brand", "value": brand, "inline": True},
                {"name": "Condition", "value": condition, "inline": True},
                {"name": "Tags", "value": tag_text, "inline": False},
                {"name": "Listed", "value": listed_text, "inline": True}
            ],
            "footer": {"text": "Vinted Monitor"},
            "timestamp": datetime.utcnow().isoformat()
        }]
    }

    try:
        r = requests.post(WEBHOOK_URL, json=payload, timeout=10)
        r.raise_for_status()
        print(f"Alert sent: {title} — {price:.2f}€ | {brand} | {tag_text}")
    except Exception as e:
        print(f"Error sending Discord alert: {e}")


def check(params):
    global seen_ids
    items = fetch_items(params)
    new_items = []

    for item in items:
        item_id = item.get("id")
        if not item_id or item_id in seen_ids:
            continue
        price = get_price(item)
        if price <= MAX_PRICE:
            new_items.append(item)
        seen_ids.add(item_id)

    for item in new_items:
        send_alert(item)

    print(f"{len(new_items)} new items checked.")


def main():
    print("Vinted Monitor starting...")
    print(f"Monitoring {len(SEARCH_URLS)} searches")
    all_params = [parse_params(url) for url in SEARCH_URLS]

    while True:
        for params in all_params:
            check(params)
            time.sleep(5)
        time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    main()
