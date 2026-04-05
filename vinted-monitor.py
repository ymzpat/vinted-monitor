import requests
import time
import os
from urllib.parse import urlparse, parse_qs
from datetime import datetime, timezone

WEBHOOK_URL = os.environ["WEBHOOK_URL"]
MAX_PRICE = float(os.environ.get("MAX_PRICE", 23))
VINTED_USERNAME = os.environ["VINTED_USERNAME"]
VINTED_PASSWORD = os.environ["VINTED_PASSWORD"]
CHECK_INTERVAL = 60
BELOW_MARKET_THRESHOLD = 0.80

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
BASE_DOMAIN = f"{parsed_base.scheme}://{parsed_base.netloc}" if parsed_base else "https://www.vinted.fr"
VINTED_API = f"{BASE_DOMAIN}/api/v2/catalog/items"
VINTED_ITEM_API = f"{BASE_DOMAIN}/api/v2/items"

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


def login():
    """Log in to Vinted and store session cookies."""
    try:
        # First get CSRF token
        resp = session.get(f"{BASE_DOMAIN}/api/v2/sessions/csrf", headers=HEADERS, timeout=10)
        csrf = resp.json().get("csrf_token") or resp.cookies.get("CSRF-TOKEN", "")

        login_headers = {**HEADERS, "X-CSRF-Token": csrf, "Content-Type": "application/json"}

        payload = {
            "user": {
                "login": VINTED_USERNAME,
                "password": VINTED_PASSWORD,
            }
        }

        resp2 = session.post(
            f"{BASE_DOMAIN}/api/v2/sessions",
            json=payload,
            headers=login_headers,
            timeout=10,
        )

        if resp2.status_code == 200:
            print("Logged in successfully")
            return True
        else:
            print(f"Login failed: {resp2.status_code} — {resp2.text[:200]}")
            return False

    except Exception as e:
        print(f"Login error: {e}")
        return False


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
        login()
        return []
    except Exception as e:
        print(f"Fetch error: {e}")
        return []


def fetch_item_details(item_id):
    try:
        resp = session.get(f"{VINTED_ITEM_API}/{item_id}", headers=HEADERS, timeout=10)
        resp.raise_for_status()
        return resp.json().get("item", {})
    except Exception as e:
        print(f"Item detail error: {e}")
        return {}


def get_demand_info(item_id, listed_at):
    details = fetch_item_details(item_id)
    views = details.get("view_count", 0) or 0
    favourites = details.get("favourite_count", 0) or 0

    minutes_ago = None
    if listed_at:
        try:
            listed_time = datetime.fromisoformat(str(listed_at).replace("Z", "+00:00"))
            now = datetime.now(timezone.utc)
            minutes_ago = int((now - listed_time).total_seconds() / 60)
        except Exception:
            pass

    label = None
    if favourites >= 10:
        label = "🔥 High demand"
    elif favourites >= 5:
        label = "⚡ Getting attention"
    elif favourites >= 2 and minutes_ago and minutes_ago < 30:
        label = "⚡ Moving fast"

    return views, favourites, minutes_ago, label


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

        if len(prices) < 3 and size_id:
            params.pop("size_ids[]", None)
            resp2 = session.get(VINTED_API, headers=HEADERS, params=params, timeout=10)
            resp2.raise_for_status()
            similar2 = resp2.json().get("items", [])
            prices = [get_price(s) for s in similar2 if s.get("id") != item_id and 1 < get_price(s) < 500]

        if len(prices) < 3:
            return None

        return round(sum(prices) / len(prices), 2)

    except Exception as e:
        print(f"Market price error: {e}")
        return None


def send_alert(item, market_price):
    price = get_price(item)
    title = item.get("title", "Unknown item")
    item_id = item.get("id")
    url = f"{BASE_DOMAIN}/items/{item_id}"
    listed_at = item.get("created_at_ts") or item.get("updated_at_ts")

    photos = item.get("photos", [])
    image = photos[0].get("url") if photos else None

    brand = item.get("brand_title") or "—"
    size = item.get("size_title") or "—"
    condition = item.get("status") or "—"

    views, favourites, minutes_ago, demand_label = get_demand_info(item_id, listed_at)

    discount = round((1 - price / market_price) * 100)
    deal_line = f"🔥 **{discount}% below market** (avg {market_price:.2f}€)"
    color = 0xFF4500 if discount >= 40 else 0x09B1BA

    demand_parts = []
    if views:
        demand_parts.append(f"👀 {views} views")
    if favourites:
        demand_parts.append(f"❤️ {favourites} saved")
    if minutes_ago is not None:
        if minutes_ago < 60:
            demand_parts.append(f"🕐 {minutes_ago}m ago")
        else:
            demand_parts.append(f"🕐 {minutes_ago // 60}h ago")
    if demand_label:
        demand_parts.append(demand_label)

    demand_line = " · ".join(demand_parts) if demand_parts else "—"

    if favourites >= 10:
        color = 0xFF0000

    embed = {
        "title": title,
        "url": url,
        "color": color,
        "fields": [
            {"name": "Price", "value": f"**{price:.2f}€**", "inline": True},
            {"name": "Brand", "value": brand, "inline": True},
            {"name": "Size", "value": size, "inline": True},
            {"name": "Condition", "value": condition, "inline": True},
            {"name": "Deal", "value": deal_line, "inline": False},
            {"name": "Demand", "value": demand_line, "inline": False},
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
        print(f"Alert sent: {title} — {price:.2f}€ | ❤️ {favourites} | 👀 {views} | {discount}% below market")
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

    print(f"{len(new_items)} new item(s) under {MAX_PRICE}€ — analysing...")

    for item in new_items:
        price = get_price(item)
        market_price = get_market_price(item)

        if market_price:
            ratio = price / market_price
            discount = round((1 - ratio) * 100)
            print(f"  {item.get('title')} — {price:.2f}€ vs market {market_price:.2f}€ ({discount}% below)")
            if ratio <= BELOW_MARKET_THRESHOLD:
                send_alert(item, market_price)
            else:
                print(f"  Skipped — not enough below market")
        else:
            print(f"  Skipped — no market data")

        time.sleep(2)

    if len(seen_ids) > 5000:
        seen_ids = set(list(seen_ids)[-2000:])


def is_off_hours():
    hour = int(time.strftime("%H", time.gmtime()))
    return 2 <= hour < 9


def main():
    print("Vinted Monitor starting...")
    print(f"Monitoring {len(SEARCH_URLS)} search(es)")
    print(f"Max budget: {MAX_PRICE}€ — alerting only if 20%+ below market")

    get_session_cookie()
    logged_in = login()
    if not logged_in:
        print("Warning: not logged in — demand data will be unavailable")

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
