import requests
import time
import os

# ─────────────────────────────────────────────
#  Read config from environment variables
# ─────────────────────────────────────────────

WEBHOOK_URL = os.environ["WEBHOOK_URL"]
VINTED_SEARCH_URL = os.environ["VINTED_SEARCH_URL"]
MAX_PRICE = float(os.environ.get("MAX_PRICE", 30))
CHECK_INTERVAL = 30

# ─────────────────────────────────────────────
#  DO NOT EDIT BELOW THIS LINE
# ─────────────────────────────────────────────

VINTED_API = "https://www.vinted.fr/api/v2/catalog/items"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (HTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
    "Referer": "https://www.vinted.fr/",
}

seen_ids = set()
session = requests.Session()


def get_vinted_session_cookie():
    try:
        session.get("https://www.vinted.fr", headers=HEADERS, timeout=10)
        print("✅ Session cookie obtained")
    except Exception as e:
        print(f"⚠️  Could not get session cookie: {e}")


def parse_search_params(url):
    from urllib.parse import urlparse, parse_qs
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
        data = resp.json()
        return data.get("items", [])
    except requests.exceptions.HTTPError as e:
        print(f"⚠️  HTTP error: {e} — refreshing session")
        get_vinted_session_cookie()
        return []
    except Exception as e:
        print(f"⚠️  Fetch error: {e}")
        return []


def send_discord_alert(item):
    price = float(item.get("price", 0))
    title = item.get("title", "Unknown item")
    url = f"https://www.vinted.fr/items/{item['id']}"
    image = None

    photos = item.get("photos", [])
    if photos:
        image = photos[0].get("url") or photos[0].get("full_size_url")

    size = item.get("size_title", "")
    brand = item.get("brand_title", "")
    condition = item.get("status", "")

    embed = {
        "title": f"🛍️ {title}",
        "url": url,
        "color": 0x09B1BA,
        "fields": [
            {"name": "💶 Price", "value": f"**{price:.2f} €**", "inline": True},
            {"name": "📦 Brand", "value": brand or "—", "inline": True},
            {"name": "📐 Size", "value": size or "—", "inline": True},
            {"name": "✅ Condition", "value": condition or "—", "inline": True},
        ],
        "footer": {"text": "Vinted Monitor"},
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }

    if image:
        embed["image"] = {"url": image}

    payload = {
        "content": f"@here New item under {MAX_PRICE}€ → [**Buy now**]({url})",
        "embeds": [embed],
    }

    try:
        r = requests.post(WEBHOOK_URL, json=payload, timeout=10)
        r.raise_for_status()
        print(f"   📨 Alert sent: {title} — {price:.2f}€")
    except Exception as e:
        print(f"   ⚠️  Discord error: {e}")


def check_new_items(params):
    global seen_ids
    items = fetch_items(params)

    new_cheap = []
    for item in items:
        item_id = item.get("id")
        price_raw = item.get("price", 9999)
price = float(price_raw["amount"]) if isinstance(price_raw, dict) else float(price_raw)
        if item_id and item_id not in seen_ids and price <= MAX_PRICE:
            new_cheap.append(item)
        if item_id:
            seen_ids.add(item_id)

    if new_cheap:
        print(f"   🔔 {len(new_cheap)} new item(s) found!")
        for item in new_cheap:
            send_discord_alert(item)
    else:
        print(f"   No new items under {MAX_PRICE}€")

    if len(seen_ids) > 5000:
        seen_ids = set(list(seen_ids)[-2000:])


def main():
    print("=" * 50)
    print("  Vinted Discord Monitor")
    print("=" * 50)
    print(f"  Search : {VINTED_SEARCH_URL}")
    print(f"  Max price : {MAX_PRICE}€")
    print(f"  Interval : {CHECK_INTERVAL}s")
    print("=" * 50)

    get_vinted_session_cookie()
    params = parse_search_params(VINTED_SEARCH_URL)

    print("\n⏳ Initial scan (no alerts) ...")
    items = fetch_items(params)
    for item in items:
        if item.get("id"):
            seen_ids.add(item["id"])
    print(f"   {len(seen_ids)} existing items indexed. Monitoring starts now.\n")

    while True:
        print(f"🔍 Checking ... [{time.strftime('%H:%M:%S')}]")
        check_new_items(params)
        time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    main()
