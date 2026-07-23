import json
import os
import requests
import asyncio
from datetime import datetime
from twikit import Client

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

def build_query(config):
    profiles = config.get("sledovane_profily", [])
    keywords = config.get("klicova_slova", [])
    clean_profiles = [p.replace('@', '').strip() for p in profiles]
    
    terms = []
    for p in profiles:
        terms.append(p)
        name_only = p.replace('@', '').replace('_', ' ')
        terms.append(f'"{name_only}"')
        
    for k in keywords:
        terms.append(f'"{k}"')
        
    query_terms = " OR ".join(terms)
    exclusions = " ".join([f"-from:{p}" for p in clean_profiles])
    return f"({query_terms}) {exclusions} -filter:retweets"

def find_matched_keyword(text, author, config):
    text_lower = text.lower()
    for profile in config.get("sledovane_profily", []):
        if profile.lower() in text_lower or profile.lower().replace('@', '') in text_lower:
            return profile
    for kw in config.get("klicova_slova", []):
        if kw.lower() in text_lower:
            return kw
    return "Obecná zmínka"

def parse_twitter_date(created_at):
    if isinstance(created_at, datetime):
        return created_at.isoformat()
    try:
        parsed = datetime.strptime(created_at, "%a %b %d %H:%M:%S %z %Y")
        return parsed.isoformat()
    except Exception:
        return datetime.utcnow().isoformat()

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
            "footer": {"text": "X-Monitor | Kliknutím na nadpis otevřeš tweet"},
            "timestamp": datetime.utcnow().isoformat()
        }]
    }
    try:
        requests.post(webhook_url, json=payload)
    except Exception:
        pass

async def main():
    env_cookies = os.getenv("X_COOKIES")
    if env_cookies:
        try:
            raw_data = json.loads(env_cookies)
            # Pokud přišly cookies jako list od prohlížeče, uděláme z nich slovník
            if isinstance(raw_data, list):
                cookie_dict = {c['name']: c['value'] for c in raw_data if 'name' in c and 'value' in c}
            else:
                cookie_dict = raw_data
            with open(COOKIES_FILE, 'w', encoding='utf-8') as f:
                json.dump(cookie_dict, f, ensure_ascii=False)
        except Exception:
            with open(COOKIES_FILE, 'w', encoding='utf-8') as f:
                f.write(env_cookies)
                
    if not os.path.exists(COOKIES_FILE):
        print("Chyba: Cookies nebyly nalezeny.")
        return

    # Pro jistotu ověříme soubor i před načtením
    try:
        with open(COOKIES_FILE, 'r', encoding='utf-8') as f:
            content = json.load(f)
        if isinstance(content, list):
            cookie_dict = {c['name']: c['value'] for c in content if 'name' in c and 'value' in c}
            with open(COOKIES_FILE, 'w', encoding='utf-8') as f:
                json.dump(cookie_dict, f, ensure_ascii=False)
    except Exception:
        pass

    client = Client('en-US')
    try:
        client.load_cookies(COOKIES_FILE)
    except Exception as e:
        print(f"Chyba přihlášení: {e}")
        return

    config = load_config()
    existing_tweets = load_existing_data()
    existing_ids = {t['id'] for t in existing_tweets}
    
    query = build_query(config)
    
    try:
        results = await client.search_tweet(query, product='Latest')
    except Exception as e:
        print(f"Chyba vyhledávání: {e}")
        return

    new_tweets_count = 0
    discord_webhook = os.getenv("DISCORD_WEBHOOK_URL")

    for tweet in results:
        if tweet.id in existing_ids:
            continue
            
        matched_kw = find_matched_keyword(tweet.text, tweet.user.screen_name, config)
        new_tweet_entry = {
            "id": tweet.id,
            "author": tweet.user.screen_name,
            "text": tweet.text,
            "timestamp": parse_twitter_date(tweet.created_at),
            "matched_keyword": matched_kw
        }
        existing_tweets.append(new_tweet_entry)
        new_tweets_count += 1
        send_to_discord(new_tweet_entry, discord_webhook)

    if new_tweets_count > 0:
        save_data(existing_tweets)

if __name__ == "__main__":
    asyncio.run(main())
