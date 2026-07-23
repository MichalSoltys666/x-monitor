import json
import os
import requests
from datetime import datetime
from playwright.sync_api import sync_playwright

CONFIG_FILE = 'nastaveni.json'
DATA_FILE = 'data.json'
COOKIES_FILE = 'cookies.json'

def load_config():
    if not os.path.exists(CONFIG_FILE):
        return {"sledovane_profily": [], "klicova_slova": []}
    with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
        return json.load(f)

def load_existing_data():
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            return []
    return []

def save_data(data):
    data.sort(key=lambda x: x['timestamp'], reverse=True)
    limited_data = data[:100]
    with open(DATA_FILE, 'w', encoding='utf-8') as f:
        json.dump(limited_data, f, indent=2, ensure_ascii=False)

def build_search_url(config):
    profiles = config.get("sledovane_profily", [])
    keywords = config.get("klicova_slova", [])
    
    terms = []
    for p in profiles:
        terms.append(p)
    for k in keywords:
        terms.append(f'"{k}"')
        
    query = " OR ".join(terms) + " -filter:retweets"
    import urllib.parse
    encoded_query = urllib.parse.quote(query)
    return f"https://x.com/search?q={encoded_query}&f=live"

def find_matched_keyword(text, config):
    text_lower = text.lower()
    for profile in config.get("sledovane_profily", []):
        if profile.lower() in text_lower or profile.lower().replace('@', '') in text_lower:
            return profile
    for kw in config.get("klicova_slova", []):
        if kw.lower() in text_lower:
            return kw
    return "Obecná zmínka"

def send_to_discord(tweet, webhook_url):
    if not webhook_url:
        return
    tweet_url = f"https://x.com/{tweet['author']}/status/{tweet['id']}"
    payload = {
        "embeds": [{
            "title": "🚨 Nová zmínka na X!",
            "url": tweet_url,
            "color": 4193236,
            "description": tweet["text"],
            "fields": [
                {"name": "Autor", "value": f"[@{tweet['author']}](https://x.com/{tweet['author']})", "inline": True},
                {"name": "Zachycené téma", "value": tweet.get("matched_keyword", "Neznámý"), "inline": True}
            ],
            "footer": {"text": "X-Monitor (Playwright) | Kliknutím otevřeš tweet"},
            "timestamp": datetime.utcnow().isoformat()
        }]
    }
    try:
        requests.post(webhook_url, json=payload)
    except Exception:
        pass

def main():
    env_cookies = os.getenv("X_COOKIES")
    if env_cookies:
        try:
            raw_data = json.loads(env_cookies)
            if isinstance(raw_data, list):
                cookie_dict = []
                for c in raw_data:
                    if 'name' in c and 'value' in c:
                        cookie_item = {
                            'name': c['name'],
                            'value': c['value'],
                            'domain': c.get('domain', '.x.com'),
                            'path': c.get('path', '/')
                        }
                        cookie_dict.append(cookie_item)
                with open(COOKIES_FILE, 'w', encoding='utf-8') as f:
                    json.dump(cookie_dict, f, ensure_ascii=False)
            else:
                with open(COOKIES_FILE, 'w', encoding='utf-8') as f:
                    f.write(env_cookies)
        except Exception:
            with open(COOKIES_FILE, 'w', encoding='utf-8') as f:
                f.write(env_cookies)

    if not os.path.exists(COOKIES_FILE):
        print("Chyba: Cookies nebyly nalezeny.")
        return

    config = load_config()
    existing_tweets = load_existing_data()
    existing_ids = {t['id'] for t in existing_tweets}
    
    search_url = build_search_url(config)
    print(f"Otevírám URL: {search_url}")

    new_tweets_count = 0
    discord_webhook = os.getenv("DISCORD_WEBHOOK_URL")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
        
        # Načtení cookies do prohlížeče
        try:
            with open(COOKIES_FILE, 'r', encoding='utf-8') as f:
                cookies_data = json.load(f)
                if isinstance(cookies_data, list):
                    context.add_cookies(cookies_data)
        except Exception as e:
            print(f"Varování při načítání cookies: {e}")

        page = context.new_page()
        try:
            page.goto("https://x.com", timeout=60000)
            page.wait_for_timeout(3000)
            
            page.goto(search_url, timeout=60000)
            # Počkáme, až se na stránce načtou tweety (články)
            page.wait_for_selector('article[data-testid="tweet"]', timeout=15000)
            
            # Jemné posunutí dolů pro načtení víc tweetů
            page.evaluate("window.scrollBy(0, 800)")
            page.wait_for_timeout(3000)

            articles = page.locator('article[data-testid="tweet"]').all()
            print Nalezeno tweetů na stránce: {len(articles)}

            for article in articles:
                try:
                    # Zjištění textu tweetu
                    text_el = article.locator('[data-testid="tweetText"]')
                    tweet_text = text_el.inner_text() if text_el.count() > 0 else ""

                    # Zjištění autora
                    user_el = article.locator('[data-testid="User-Name"]')
                    user_text = user_el.inner_text() if user_el.count() > 0 else ""
                    # Z uživatelského bloku vytáhneme handle (např. @Jmeno)
                    author = "neznnamy"
                    for line in user_text.split('\n'):
                        if line.startswith('@'):
                            author = line.replace('@', '').strip()
                            break

                    # Zjištění odkazu / ID tweetu z URL tlačítka času
                    time_el = article.locator('time').locator('xpath=ancestor::a')
                    tweet_id = "0"
                    if time_el.count() > 0:
                        href = time_el.get_attribute('href')
                        if href and '/status/' in href:
                            tweet_id = href.split('/status/')[-1].split('/')[0]

                    if tweet_id != "0" and tweet_id not in existing_ids:
                        matched_kw = find_matched_keyword(tweet_text, config)
                        new_tweet_entry = {
                            "id": tweet_id,
                            "author": author,
                            "text": tweet_text,
                            "timestamp": datetime.utcnow().isoformat(),
                            "matched_keyword": matched_kw
                        }
                        existing_tweets.append(new_tweet_entry)
                        existing_ids.add(tweet_id)
                        new_tweets_count += 1
                        send_to_discord(new_tweet_entry, discord_webhook)
                except Exception as ex:
                    print(f"Chyba při parsování jednoho tweetu: {ex}")

        except Exception as e:
            print(f"Chyba při načítání stránky v prohlížeči: {e}")
        finally:
            browser.close()

    if new_tweets_count > 0:
        save_data(existing_tweets)
        print(f"Uloženo {new_tweets_count} nových tweetů.")
    else:
        print("Žádné nové tweety k uložení.")

if __name__ == "__main__":
    main()
