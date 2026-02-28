import feedparser
import os
import json
import requests
from datetime import datetime, timezone
from anthropic import Anthropic
from supabase import create_client
from dotenv import load_dotenv

load_dotenv()

anthropic_client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
supabase = create_client(
    os.environ["SUPABASE_URL"],
    os.environ["SUPABASE_KEY"]
)

FEEDS = {
    "Google新聞": "https://news.google.com/rss?hl=zh-TW&gl=TW&ceid=TW:zh-Hant",
    "雅虎台灣": "https://tw.news.yahoo.com/rss",
    "路透社": "https://www.reutersagency.com/feed/?best-topics=top-news&post_type=best",
    "中央社": "https://www.cna.com.tw/rss/aall.aspx",
    "BBC中文": "https://feeds.bbci.co.uk/zhongwen/trad/rss.xml",
    "彭博": "https://feeds.bloomberg.com/markets/news.rss",
    "美聯社": "https://apnews.com/rss",
    "DW德國之聲": "https://rss.dw.com/rdf/rss-en-top",
    "半島電視台": "https://www.aljazeera.com/xml/rss/all.xml",
    "經濟學人": "https://www.economist.com/latest/rss.xml"
}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}


def fetch_all_news():
    all_articles = []
    for source_name, url in FEEDS.items():
        try:
            response = requests.get(url, headers=HEADERS, timeout=10)
            feed = feedparser.parse(response.content)
            count = 0
            for entry in feed.entries[:8]:
                title = entry.get("title", "").strip()
                if not title:
                    continue
                all_articles.append({
                    "source": source_name,
                    "title": title,
                    "link": entry.get("link", ""),
                    "summary": entry.get("summary", "")[:300],
                    "published": entry.get("published", str(datetime.now()))
                })
                count += 1
            print(f"✅ {source_name}：抓取 {count} 則")
        except Exception as e:
            print(f"❌ {source_name} 失敗：{e}")
    return all_articles


def process_batch(batch, batch_start):
    news_text = ""
    for i, a in enumerate(batch, 1):
        news_text += f"{i}. [{a['source']}] {a['title']}\n   {a['summary'][:150]}\n\n"

    prompt = f"""你是專業新聞編輯，分析以下{len(batch)}則新聞，每則提供：中文標題、中文摘要(100字內)、分類(政治/財經/科技/國際/社會/兩岸/其他)、重要程度(高/中/低)、關鍵詞(3個逗號分隔)。

回傳純JSON陣列，index從{batch_start+1}開始：
[{{"index":{batch_start+1},"zh_title":"","zh_summary":"","category":"","importance":"","keywords":""}}]

新聞：
{news_text}

只回傳JSON陣列。"""

    message = anthropic_client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=3000,
        messages=[{"role": "user", "content": prompt}]
    )
    text = message.content[0].text.strip()
    if "```" in text:
        parts = text.split("```")
        text = parts[1] if len(parts) > 1 else text
        if text.startswith("json"):
            text = text[4:]
    text = text.strip()
    return json.loads(text)


def ai_process_news(articles):
    all_results = []
    batch_size = 15
    total_batches = (len(articles) + batch_size - 1) // batch_size

    for batch_num in range(total_batches):
        start = batch_num * batch_size
        batch = articles[start:start + batch_size]
        try:
            results = process_batch(batch, start)
            all_results.extend(results)
            print(f"  ✅ 批次 {batch_num + 1}/{total_batches} 完成（{len(results)} 則）")
        except Exception as e:
            print(f"  ❌ 批次 {batch_num + 1} 失敗：{e}")

    return all_results


def generate_daily_summary(articles, ai_results):
    
    hour = datetime.now().hour
    if hour < 12:
        greeting = "早安，為您帶來今日早間重點快報。"
    elif hour < 17:
        greeting = "午安，為您帶來今日午間重點快報。"
    else:
        greeting = "晚安，為您帶來今日晚間重點快報。"

    ai_map = {item["index"]: item for item in ai_results}
    high_news = []
    for i, article in enumerate(articles, 1):
        ai_data = ai_map.get(i, {})
        if ai_data.get("importance") == "高":
            high_news.append({
                "category": ai_data.get("category", "其他"),
                "title": ai_data.get("zh_title", article["title"]),
                "summary": ai_data.get("zh_summary", "")
            })

    news_text = "\n".join([f"- [{n['category']}] {n['title']}：{n['summary']}" for n in high_news[:15]])

    prompt = f"""你是專業新聞主播，根據以下重要新聞，用繁體中文撰寫今日重點摘要。

格式要求：
1. 開頭用「{greeting}」
2. 依【政治焦點】【國際情勢】【財經動態】【科技趨勢】【社會民生】等分類，每類2-3句話
3. 只寫有新聞的分類，總字數約300-400字
4. 語氣專業、簡潔、客觀

重要新聞：
{news_text}

直接輸出摘要文字，不要加任何標記或說明。"""

    message = anthropic_client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=1000,
        messages=[{"role": "user", "content": prompt}]
    )
    return message.content[0].text.strip()


def save_to_supabase(articles, ai_results):
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    ai_map = {item["index"]: item for item in ai_results}
    records = []
    for i, article in enumerate(articles, 1):
        ai_data = ai_map.get(i, {})
        records.append({
            "date": today,
            "source": article["source"],
            "original_title": article["title"],
            "zh_title": ai_data.get("zh_title", article["title"]),
            "zh_summary": ai_data.get("zh_summary", ""),
            "category": ai_data.get("category", "其他"),
            "importance": ai_data.get("importance", "中"),
            "keywords": ai_data.get("keywords", ""),
            "link": article["link"],
            "published": article["published"]
        })
    supabase.table("news_digest").delete().eq("date", today).execute()
    supabase.table("news_digest").insert(records).execute()
    print(f"✅ 已儲存 {len(records)} 則新聞")


def save_summary_to_supabase(summary_text):
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    supabase.table("daily_summary").upsert({
        "date": today,
        "summary_text": summary_text
    }, on_conflict="date").execute()
    print(f"✅ 已儲存每日摘要")


def main():
    print(f"🚀 開始抓取新聞... {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    articles = fetch_all_news()
    print(f"\n📰 共抓取 {len(articles)} 則新聞")

    if len(articles) == 0:
        print("❌ 沒有抓到任何新聞")
        return

    print("\n🤖 AI 分析中...")
    ai_results = ai_process_news(articles)
    print(f"✅ AI 處理完成，共 {len(ai_results)} 則")

    print("\n📝 生成每日摘要...")
    summary = generate_daily_summary(articles, ai_results)
    print("✅ 摘要生成完成")

    print("\n💾 儲存到資料庫...")
    save_to_supabase(articles, ai_results)
    save_summary_to_supabase(summary)

    print("\n🎉 完成！")


if __name__ == "__main__":
    main()