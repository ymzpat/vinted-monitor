#import requests
#import time
#import os
from urllib.parse import urlparse, parse_qs

WEBHOOK_URL = os.environ["WEBHOOK_URL"]
MAX_PRICE = float(os.environ.get("MAX_PRICE", 23))
CHECK_INTERVAL = 60
BELOW_MARKET_THRESHOLD = 0.80  # alert if price is 20% or more below market average

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

VINTED_API = "https://www.vinted.fr/api/v2/catalog/items"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (HTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
    "Referer": "https://www.vinted.fr/",
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
        session.get("https://www.vinted.fr", headers=HEADERS, timeout=10)
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
    """Search for similar items and return the average price."""
    try:
        title = item.get("title", "")
        brand = item.get("brand_title", "")
        # Use brand + first 2 words of title as search query
        words = (brand + " " + title).strip().split()[:4]
        query = " ".join(words)

        params = {
            "search_text": query,
            "per_page": "20",
            "order": "relevance",
        }

        resp = session.get(VINTED_API, headers=HEADERS, params=params, timeout=10)
        resp.raise_for_status()
        similar_items = resp.json().get("items", [])

        prices = []
        for s in similar_items:
            p = get_price(s)
            # Only include reasonably priced items to avoid skewing the average
            if 1 < p < 200:
                prices.append(p)

        if len(prices) < 3:
            return None

        avg = sum(prices) / len(prices)
        return round(avg, 2)

    except Exception as e:
        print(f"Market price error: {e}")
        return None


def send_alert(item, market_price=None):
    price = get_price(item)
    title = item.get("title", "Unknown item")
    url = f"https://www.vinted.fr/items/{item['id']}"

    photos = item.get("photos", [])
    image = photos[0].get("url") if photos else None

    if market_price and market_price > 0:
        discount = round((1 - price / market_price) * 100)
        deal_line = f"🔥 **{discount}% below market** (avg {market_price:.2f}€)"
        color = 0xFF4500 if discount >= 40 else 0x09B1BA
    else:
        deal_line = f"Under {MAX_PRICE}€"
        color = 0x09B1BA

    embed = {
        "title": f"{title}",
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
        r = requests.post(WEBHOOK_URL, json=payload, timeout=10)
        r.raise_for_status()
        print(f"Alert sent: {title} — {price:.2f}€")
    except Exception as e:
        print(f"Discord error: {e}")


def check(params):
    global seen_ids
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
        print("No new items")
        return

    print(f"{len(new_items)} new item(s) under {MAX_PRICE}€ — checking market price...")

    for item in new_items:
        price = get_price(item)
        market_price = get_market_price(item)

        if market_price:
            ratio = price / market_price
            print(f"  {item.get('title')} — {price:.2f}€ vs market {market_price:.2f}€ (ratio {ratio:.2f})")
            if ratio <= BELOW_MARKET_THRESHOLD:
                send_alert(item, market_price)
            else:
                print(f"  Skipped — not enough below market")
        else:
            # Not enough data to compare — alert anyway since it's under budget
            print(f"  No market data — alerting anyway")
            send_alert(item, None)

        time.sleep(2)  # small pause between market price checks

    if len(seen_ids) > 5000:
        seen_ids = set(list(seen_ids)[-2000:])


def is_off_hours():
    """Returns True between 2am and 9am — pause to save Railway credits."""
    hour = int(time.strftime("%H", time.gmtime()))
    return 2 <= hour < 9


def main():
    print("Vinted Monitor starting...")
    print(f"Monitoring {len(SEARCH_URLS)} search(es)")
    print(f"Max budget: {MAX_PRICE}€ — alerting if 20%+ below market")

    get_session_cookie()
    all_params = [parse_params(url) for url in SEARCH_URLS]

    print("Initial scan (no alerts)...")
    for params in all_params:
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
        for params in all_params:
            check(params)
            time.sleep(5)
        time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    main()
