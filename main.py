# vinted_monitor_async.py
import os
import asyncio
import aiohttp
from datetime import datetime, timezone
from urllib.parse import urlparse, parse_qs

# === CONFIG ===
WEBHOOK_URL = os.environ.get("WEBHOOK_URL")
MAX_PRICE = float(os.environ.get("MAX_PRICE", 23))
CHECK_INTERVAL = int(os.environ.get("CHECK_INTERVAL", 60))
BELOW_MARKET_THRESHOLD = float(os.environ.get("BELOW_MARKET_THRESHOLD", 0.8))

VINTED_ACCOUNTS = []
i = 1
while True:
    username = os.environ.get(f"VINTED_USERNAME_{i}")
    password = os.environ.get(f"VINTED_PASSWORD_{i}")
    if not username or not password:
        break
    VINTED_ACCOUNTS.append((username, password))
    i += 1

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
    "User-Agent": "Mozilla/5.0",
    "Accept": "application/json",
    "Referer": f"{BASE_DOMAIN}/",
}

seen_ids = set()


# === FUNCTIONS ===
async def fetch_json(session, url, params=None, headers=None):
    try:
        async with session.get(url, params=params, headers=headers, timeout=10) as r:
            r.raise_for_status()
            return await r.json()
    except:
        return None


def parse_params(url):
    parsed = urlparse(url)
    params = parse_qs(parsed.query)
    flat = {k: v[0] if len(v) == 1 else v for k, v in params.items()}
    flat["per_page"] = "30"
    flat["order"] = "newest_first"
    return flat


def get_price(item):
    raw = item.get("price", 9999)
    if isinstance(raw, dict):
        return float(raw.get("amount", 9999))
    return float(raw)


async def send_alert(item, market_price):
    price = get_price(item)
    item_id = item.get("id")
    url = f"{BASE_DOMAIN}/items/{item_id}"
    discount = round((1 - price / market_price) * 100) if market_price else 0
    content = f"@here **{item.get('title')}** — {price:.2f}€ ({discount}% below market) [Buy now]({url})"
    payload = {"content": content}

    async with aiohttp.ClientSession() as session:
        try:
            async with session.post(WEBHOOK_URL, json=payload) as r:
                if r.status == 200:
                    print(f"Alert sent: {item.get('title')} — {price:.2f}€ | {discount}% below market")
        except Exception as e:
            print(f"Discord error: {e}")


async def check_items():
    global seen_ids
    async with aiohttp.ClientSession() as session:
        for url in SEARCH_URLS:
            params = parse_params(url)
            data = await fetch_json(session, VINTED_API, params=params, headers=HEADERS)
            if not data:
                continue
            items = data.get("items", [])
            for item in items:
                item_id = item.get("id")
                price = get_price(item)
                if item_id not in seen_ids and price <= MAX_PRICE:
                    seen_ids.add(item_id)
                    market_price = price / BELOW_MARKET_THRESHOLD  # simple placeholder
                    await send_alert(item, market_price)


async def main_loop():
    print("Vinted monitor starting...")
    print(f"Monitoring {len(SEARCH_URLS)} search URLs")
    while True:
        await check_items()
        await asyncio.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    asyncio.run(main_loop())
