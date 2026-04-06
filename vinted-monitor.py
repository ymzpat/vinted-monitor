#import requests
#import time
#import os
from urllib.parse import urlparse, parse_qs

MAX_PRICE = float(os.environ.get("MAX_PRICE", 23))
CHECK_INTERVAL = 60
BELOW_MARKET_THRESHOLD = 0.80

# Load search URLs and their matching webhooks
# VINTED_SEARCH_URL_1 pairs with WEBHOOK_URL_1, etc.
SEARCHES = []
i = 1
while True:
    url = os.environ.get(f"VINTED_SEARCH_URL_{i}")
    webhook = os.environ.get(f"WEBHOOK_URL_{i}")
    if not url:
        break
    if not webhook:
        # Fall back to default webhook if no specific one set
        webhook = os.environ.get("WEBHOOK_URL")
    if webhook:
        SEARCHES.append({"url": url, "webhook": webhook, "index": i})
    i += 1

# Fallback to single URL + single webhook
if not SEARCHES:
    url = os.environ.get("VINTED_SEARCH_URL")
    webhook = os.environ.get("WEBHOOK_URL")
    if url and webhook:
        SEARCHES.append({"url": url, "webhook": webhook, "index": 1})

parsed_base = urlparse(SEARCHES[0]["url"]) if SEARCHES else None
BASE_DOMAIN = f"{parsed_base.scheme}://{parsed_base.netloc}" if parsed_base else "https://www.vinted.fr"
VINTED_API = f"{BASE_DOMAIN}/api/v2/catalog/items"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (HTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
    "Referer": f"{BASE_DOMAIN}/",
}

seen_ids = set()
session = requests.Session()


def get_price(item):
    raw = item.get("price", 9999)
    if isinstance(raw, dict):
        return float(raw.get("amount", 9999))
    return float(raw)


def get_session_cookie():
    try:
        session.get(BASE_DOMAIN, headers=HEADERS, timeout=10)
        print("Session cookie obtained")
    except Exception as e:
        print(f"Could not get session cookie: {e}")


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
    except requests.exceptions.HTTPError as e:
        print(f"HTTP error: {e} — refreshing session")
        get_session_cookie()
        return []
    except Exception as e:
        print(f"Fetch error: {e}")
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
        if brand_id:
            params["brand_ids[]"] = brand_id
        if catalog_id:
            params["catalog_ids[]"] = catalog_id
        if status_id:
            params["status_ids[]"] = status_id
        if size_id:
            params["size_ids[]"] = size_id

        resp = session.get(VINTED_API, headers=HEADERS, params=params, timeout=10)
        resp.raise_for_status()
        similar = resp.json().get("items", [])
        item_id = item.get("id")
        prices = [get_price(s) for s in similar if s.get("id") != item_id and 1 < get_price(s) < 500]

        if len(prices) < 1 and size_id:
            params.pop("size_ids[]", None)
            resp2 = session.get(VINTED_API, headers=HEADERS, params=params, timeout=10)
            resp2.raise_for_status()
            similar2 = resp2.json().get("items", [])
            prices = [get_price(s) for s in similar2 if s.get("id") != item_id and 1 < get_price(s) < 500]

        if len(prices) < 1:
            return None

        return round(sum(prices) / len(prices), 2)

    except Exception as e:
        print(f"Market price error: {e}")
        return None


def send_alert(item, webhook_url, market_price=None):
    price = get_price(item)
    title = item.get("title", "Unknown item")
    item_id = item.get("id")
    url = f"{BASE_DOMAIN}/items/{item_id}"

    photos = item.get("photos", [])
    image = photos[0].get("url") if photos else None

    if market_price:
        discount = round((1 - price / market_price) * 100)
        deal_line = f"🔥 **{discount}% below market** (avg {market_price:.2f}€)"
        color = 0xFF4500 if discount >= 40 else 0x09B1BA
    else:
        deal_line = "No market data"
        color = 0x09B1BA

    embed = {
        "title": title,
        "url": url,
        "color": color,
        "fields": [
            {"name": "Price", "value": f"**{price:.2f}€**", "inline": True},
            {"name": "Brand", "value": item.get("brand_title") or "—", "inline": True},
            {"name": "Size", "value": item.get("size_title") or "—", "inline": True},
            {"name": "Condition", "value": item.get("status") or "—", "inline": True},
            {"name": "Deal", "value": deal_line, "inline": False},
        ],
        "footer": {"text": "Vinted Monitor"},
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }

    if image:
        embed["image"] = {"url": image}

    payload = {
        "content": f"@here [**Buy now**]({url})",
        "embeds": [embed],
    }

    try:
        r = requests.post(webhook_url, json=payload, timeout=10)
        r.raise_for_status()
        if market_price:
            discount = round((1 - price / market_price) * 100)
            print(f"Alert sent: {title} — {price:.2f}€ | {discount}% below market")
        else:
            print(f"Alert sent: {title} — {price:.2f}€ | no market data")
    except Exception as e:
        print(f"Discord error: {e}")


def check(search):
    global seen_ids
    params = parse_params(search["url"])
    webhook_url = search["webhook"]
    items = fetch_items(params)
    new_items = []

    for item in items:
        item_id = item.get("id")
        price = get_price(item)
        if item_id and item_id not in seen_ids and price <= MAX_PRICE:
            new_items.append(item)
        if item_id:
            seen_ids.add(item_id)

    if not new_items:
        print(f"Search {search['index']}: No new items")
        return

    print(f"Search {search['index']}: {len(new_items)} new item(s) under {MAX_PRICE}€ — checking market price...")

    for item in new_items:
        price = get_price(item)
        market_price = get_market_price(item)

        if market_price:
            ratio = price / market_price
            discount = round((1 - ratio) * 100)
            print(f"  {item.get('title')} — {price:.2f}€ vs market {market_price:.2f}€ ({discount}% below)")
            if ratio <= BELOW_MARKET_THRESHOLD:
                send_alert(item, webhook_url, market_price)
            else:
                print(f"  Skipped — not enough below market")
        else:
            print(f"  No market data — alerting anyway")
            send_alert(item, webhook_url, None)

        time.sleep(2)

    if len(seen_ids) > 5000:
        seen_ids = set(list(seen_ids)[-2000:])


def is_off_hours():
    hour = int(time.strftime("%H", time.gmtime()))
    return 2 <= hour < 9


def main():
    print("Vinted Monitor starting...")
    print(f"Using domain: {BASE_DOMAIN}")
    print(f"Monitoring {len(SEARCHES)} search(es)")
    print(f"Max budget: {MAX_PRICE}€")
    for s in SEARCHES:
        print(f"  Search {s['index']}: {s['url'][:60]}...")

    get_session_cookie()

    print("Initial scan (no alerts)...")
    for search in SEARCHES:
        params = parse_params(search["url"])
        for item in fetch_items(params):
            if item.get("id"):
                seen_ids.add(item["id"])
    print(f"{len(seen_ids)} existing items indexed. Monitoring starts now.")

    while True:
        if is_off_hours():
            print("Off hours (2am-9am) — sleeping to save credits...")
            time.sleep(300)
            continue

        print(f"Checking... [{time.strftime('%H:%M:%S')}]")
        for search in SEARCHES:
            check(search)
            time.sleep(5)
        time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    main()
