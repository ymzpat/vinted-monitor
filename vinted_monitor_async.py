import os
import asyncio
import aiohttp
import time
import csv
from urllib.parse import urlparse, parse_qs
from datetime import datetime, timezone
import statistics

# ---------------- CONFIG ----------------
WEBHOOK_URL = os.environ["WEBHOOK_URL"]
MAX_PRICE = float(os.environ.get("MAX_PRICE", 23))
BELOW_MARKET_THRESHOLD = float(os.environ.get("BELOW_MARKET_THRESHOLD", 0.8))
CHECK_INTERVAL = int(os.environ.get("CHECK_INTERVAL", 60))

# Multiple accounts (comma-separated in .env)
ACCOUNTS = [
    (user.strip(), pwd.strip())
    for user, pwd in [
        acc.split(":") for acc in os.environ.get("VINTED_ACCOUNTS", "").split(",") if ":" in acc
    ]
]

# Multiple search URLs
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

BASE_DOMAIN = "https://www.vinted.fr"
API_CATALOG = f"{BASE_DOMAIN}/api/v2/catalog/items"
API_ITEM = f"{BASE_DOMAIN}/api/v2/items"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (HTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json",
    "Referer": BASE_DOMAIN,
}

seen_ids = set()
csv_file = "vinted_log.csv"

# ---------------- HELPER FUNCTIONS ----------------

def parse_params(url):
    parsed = urlparse(url)
    params = parse_qs(parsed.query)
    flat = {k: v[0] if len(v) == 1 else v for k, v in params.items()}
    flat.update({"per_page": "30", "order": "newest_first"})
    return flat

def log_item(item, market_price=None):
    exists = os.path.isfile(csv_file)
    with open(csv_file, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        if not exists:
            writer.writerow(["timestamp", "id", "title", "price", "market_price", "url"])
        writer.writerow([
            datetime.utcnow().isoformat(),
            item.get("id"),
            item.get("title"),
            item.get("price"),
            market_price or "",
            f"{BASE_DOMAIN}/items/{item.get('id')}"
        ])

def format_embed(item, market_price, views, favs):
    price = float(item.get("price", 0))
    discount = round((1 - price / market_price) * 100) if market_price else 0
    color = 0xFF4500 if discount >= 40 else 0x09B1BA
    if favs >= 10: color = 0xFF0000

    demand = f"👀 {views} views · ❤️ {favs} saved" if views or favs else "—"
    embed = {
        "title": item.get("title"),
        "url": f"{BASE_DOMAIN}/items/{item.get('id')}",
        "color": color,
        "fields": [
            {"name": "Price", "value": f"**{price:.2f}€**", "inline": True},
            {"name": "Market Avg", "value": f"{market_price:.2f}€" if market_price else "—", "inline": True},
            {"name": "Deal", "value": f"{discount}% below market" if market_price else "—", "inline": False},
            {"name": "Demand", "value": demand, "inline": False},
        ],
        "timestamp": datetime.utcnow().isoformat()
    }
    photos = item.get("photos", [])
    if photos:
        embed["image"] = {"url": photos[0].get("url")}
    return embed

# ---------------- ASYNC VINTED CLIENT ----------------

class VintedClient:
    def __init__(self, session, username, password):
        self.session = session
        self.username = username
        self.password = password
        self.logged_in = False

    async def login(self):
        try:
            async with self.session.get(f"{BASE_DOMAIN}/api/v2/sessions/csrf", headers=HEADERS, timeout=10) as resp:
                csrf = (await resp.json()).get("csrf_token")
            payload = {"user": {"login": self.username, "password": self.password}}
            headers = {**HEADERS, "X-CSRF-Token": csrf, "Content-Type": "application/json"}
            async with self.session.post(f"{BASE_DOMAIN}/api/v2/sessions", json=payload, headers=headers, timeout=10) as resp:
                if resp.status == 200:
                    self.logged_in = True
                    print(f"✅ Logged in: {self.username}")
                else:
                    print(f"❌ Login failed: {self.username}")
        except Exception as e:
            print(f"⚠️ Login error: {e}")

    async def fetch_items(self, params):
        try:
            async with self.session.get(API_CATALOG, headers=HEADERS, params=params, timeout=10) as resp:
                data = await resp.json()
                return data.get("items", [])
        except:
            return []

    async def fetch_item_details(self, item_id):
        try:
            async with self.session.get(f"{API_ITEM}/{item_id}", headers=HEADERS, timeout=10) as resp:
                data = await resp.json()
                return data.get("item", {})
        except:
            return {}

    async def get_market_price(self, item):
        try:
            brand_id = item.get("brand_dto", {}).get("id") or item.get("brand_id")
            catalog_id = item.get("catalog_id")
            if not brand_id and not catalog_id: return None
            params = {"per_page": "30", "order": "relevance"}
            if brand_id: params["brand_ids[]"] = brand_id
            if catalog_id: params["catalog_ids[]"] = catalog_id
            items = await self.fetch_items(params)
            prices = [float(i.get("price", 0)) for i in items if i.get("id") != item.get("id")]
            if len(prices) < 3: return None
            return round(statistics.median(prices), 2)
        except:
            return None

# ---------------- ALERT FUNCTION ----------------

async def send_discord(embed):
    payload = {"content": "@here", "embeds": [embed]}
    async with aiohttp.ClientSession() as session:
        try:
            async with session.post(WEBHOOK_URL, json=payload, timeout=10) as resp:
                if resp.status == 204 or resp.status == 200:
                    print("✅ Alert sent")
                else:
                    print("⚠️ Discord error")
        except Exception as e:
            print(f"⚠️ Discord exception: {e}")

# ---------------- MAIN LOOP ----------------

async def monitor():
    async with aiohttp.ClientSession() as session:
        # Try each account until one works
        client = None
        for user, pwd in ACCOUNTS:
            c = VintedClient(session, user, pwd)
            await c.login()
            if c.logged_in:
                client = c
                break
        if not client:
            print("❌ No account could login. Exiting.")
            return

        params_list = [parse_params(url) for url in SEARCH_URLS]

        while True:
            for params in params_list:
                items = await client.fetch_items(params)
                for item in items:
                    item_id = item.get("id")
                    if item_id in seen_ids: continue
                    seen_ids.add(item_id)

                    price = float(item.get("price", 0))
                    if price > MAX_PRICE: continue

                    market_price = await client.get_market_price(item)
                    details = await client.fetch_item_details(item_id)
                    views = details.get("view_count", 0)
                    favs = details.get("favourite_count", 0)

                    if market_price and price / market_price <= BELOW_MARKET_THRESHOLD:
                        embed = format_embed(item, market_price, views, favs)
                        await send_discord(embed)

                    log_item(item, market_price)
                    await asyncio.sleep(1)
            await asyncio.sleep(CHECK_INTERVAL)

# ---------------- RUN ----------------

if __name__ == "__main__":
    asyncio.run(monitor())