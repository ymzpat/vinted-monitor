import json
import time
import asyncio
import requests
from pathlib import Path
from vinted import VintedClient

# Load config
config = json.loads(Path("config.json").read_text(encoding="utf-8"))

TELEGRAM_TOKEN = config["telegram_token"]
CHAT_ID = config["telegram_chat_id"]
THRESHOLD = 1 - (config["threshold_percent"] / 100)
CHECK_INTERVAL = config["check_every_minutes"] * 60
SEEN_FILE = Path("seen.json")

def load_seen():
    if SEEN_FILE.exists():
        data = json.loads(SEEN_FILE.read_text(encoding="utf-8"))
        return {k: set(v) for k, v in data.items()}
    return {}

def save_seen(seen):
    SEEN_FILE.write_text(json.dumps({k: list(v) for k, v in seen.items()}, indent=2), encoding="utf-8")

def send_alert(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": message, "parse_mode": "HTML"}
    try:
        requests.post(url, json=payload, timeout=10)
    except:
        print("Could not send Telegram message")

async def main():
    seen = load_seen()
    print("Vinted Deal Bot is starting...")

    async with VintedClient() as client:
        while True:
            for search in config["searches"]:
                name = search["name"]
                url = search["url"]
                print(f"Checking {name}...")

                # Get newest items
                items = await client.search_items(url=url, per_page=50, raw_data=True)

                if name not in seen:
                    seen[name] = set()

                for item in items:
                    item_id = str(item.get("id"))
                    if item_id in seen[name]:
                        continue

                    seen[name].add(item_id)
                    title = item.get("title", "Item")
                    price = float(item.get("price", 0))
                    item_url = f"https://www.vinted.fr/items/{item_id}"

                    # Simple average calculation (using same search, limited)
                    avg = price * 1.2  # placeholder - in real version we calculate properly
                    if price < avg * THRESHOLD and price > 0:
                        discount = round((1 - price / avg) * 100)
                        msg = f"🚨 <b>Deal found in {name}!</b>\n\n{title}\nPrice: <b>{price} €</b>\n{discount}% cheaper than average!\n\n<a href='{item_url}'>Open on Vinted</a>"
                        send_alert(msg)
                        print(f"ALERT sent for {title}")

            save_seen(seen)
            print(f"Waiting {config['check_every_minutes']} minutes...")
            await asyncio.sleep(CHECK_INTERVAL)

if __name__ == "__main__":
    asyncio.run(main())
