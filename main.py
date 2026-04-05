import os
import time
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin

WEBHOOK_URL = os.environ["WEBHOOK_URL"]
MAX_PRICE = float(os.environ.get("MAX_PRICE", 23))
CHECK_INTERVAL = 60

# Collect search URLs from env vars
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

print(f"Monitoring {len(SEARCH_URLS)} search URL(s)")

seen_items = set()
HEADERS = {"User-Agent": "Mozilla/5.0"}

def is_off_hours():
    hour = int(time.strftime("%H", time.gmtime()))
    return 2 <= hour < 9

def send_discord_alert(title, price, url, brand=None, size=None, condition=None, image=None):
    embed = {
        "title": title,
        "url": url,
        "color": 0x09B1BA,
        "fields": [
            {"name": "Price", "value": f"{price}€", "inline": True},
            {"name": "Brand", "value": brand or "—", "inline": True},
            {"name": "Size", "value": size or "—", "inline": True},
            {"name": "Condition", "value": condition or "—", "inline": True},
        ],
        "footer": {"text": "Vinted Monitor"},
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    }
    if image:
        embed["image"] = {"url": image}

    payload = {"content": f"@here [Buy now]({url})", "embeds": [embed]}
    try:
        r = requests.post(WEBHOOK_URL, json=payload, timeout=10)
        r.raise_for_status()
        print(f"Alert sent: {title} — {price}€")
    except Exception as e:
        print(f"Discord error: {e}")

def fetch_items(url):
    try:
        r = requests.get(url, headers=HEADERS, timeout=10)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        items = []

        for card in soup.select("div.feed-grid__item"):
            link_tag = card.select_one("a.item__link")
            if not link_tag: continue
            item_url = urljoin("https://www.vinted.com", link_tag["href"])
            title_tag = card.select_one("h3.item__title")
            price_tag = card.select_one("div.item__price")
            image_tag = card.select_one("img[itemprop='image']")
            brand_tag = card.select_one("div.item__brand")
            size_tag = card.select_one("div.item__size")
            condition_tag = card.select_one("div.item__condition")

            if not price_tag: continue
            try:
                price = float(price_tag.text.replace("€", "").strip())
            except:
                continue

            if price > MAX_PRICE: continue

            item_id = item_url.split("/")[-1]
            if item_id in seen_items: continue
            seen_items.add(item_id)

            items.append({
                "title": title_tag.text.strip() if title_tag else "Unknown",
                "price": price,
                "url": item_url,
                "image": image_tag["src"] if image_tag else None,
                "brand": brand_tag.text.strip() if brand_tag else None,
                "size": size_tag.text.strip() if size_tag else None,
                "condition": condition_tag.text.strip() if condition_tag else None
            })
        return items
    except Exception as e:
        print(f"Error fetching items: {e}")
        return []

def main():
    print("Vinted Monitor starting...")
    while True:
        if is_off_hours():
            print("Off hours (2am-9am) — sleeping...")
            time.sleep(300)
            continue

        for url in SEARCH_URLS:
            new_items = fetch_items(url)
            if not new_items:
                print("No new items")
            for item in new_items:
                send_discord_alert(
                    title=item["title"],
                    price=item["price"],
                    url=item["url"],
                    brand=item["brand"],
                    size=item["size"],
                    condition=item["condition"],
                    image=item["image"]
                )
        time.sleep(CHECK_INTERVAL)

if __name__ == "__main__":
    main()
