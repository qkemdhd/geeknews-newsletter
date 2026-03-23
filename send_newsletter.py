#!/usr/bin/env python3
"""
GeekNews (news.hada.io) 어제자 게시물을 수집하여
Google Gemini AI로 요약 후 Gmail로 발송하는 스크립트
"""

import os
import smtplib
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from google import genai

# ───────────────────────────────────────────
# 환경변수 (GitHub Secrets에서 주입됨)
# ───────────────────────────────────────────
GEMINI_API_KEY  = os.environ["GEMINI_API_KEY"]
GMAIL_USER      = os.environ["GMAIL_USER"]
GMAIL_APP_PW    = os.environ["GMAIL_APP_PASSWORD"]
RECIPIENT_EMAIL = os.environ["RECIPIENT_EMAIL"]

BASE_URL = "https://news.hada.io"
KST      = timezone(timedelta(hours=9))


# ───────────────────────────────────────────
# 1. 어제 날짜 계산 (KST 기준)
# ───────────────────────────────────────────
def get_yesterday():
    yesterday = datetime.now(KST) - timedelta(days=1)
    return yesterday.strftime("%Y-%m-%d"), yesterday.strftime("%Y년 %m월 %d일")


# ───────────────────────────────────────────
# 2. GeekNews 게시물 크롤링
# ───────────────────────────────────────────
def fetch_yesterday_posts(date_str):
    headers = {"User-Agent": "Mozilla/5.0 (compatible; NewsletterBot/1.0)"}
    posts = []

    for page in range(1, 6):
        url = f"{BASE_URL}/?page={page}"
        resp = requests.get(url, headers=headers, timeout=15)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        items = soup.select("div.topic_row")

        for item in items:
            time_tag = item.select_one("span.time")
            if not time_tag:
                continue
            item_date = time_tag.get("title", "")[:10]

            if item_date == date_str:
                title_tag = item.select_one("a.topictitle")
                if not title_tag:
                    continue

                title      = title_tag.get_text(strip=True)
                link       = BASE_URL + title_tag["href"]
                point_tag  = item.select_one("span.point")
                cmt_tag    = item.select_one("a.comments_count")
                origin_tag = item.select_one("a.domain")

                posts.append({
                    "title":      title,
                    "link":       link,
                    "origin_url": origin_tag["href"] if origin_tag else link,
                    "points":     point_tag.get_text(strip=True)  if point_tag else "0",
                    "comments":   cmt_tag.get_text(strip=True)    if cmt_tag   else "0",
                })

            elif item_date < date_str:
                return posts

    return posts


# ───────────────────────────────────────────
# 3. 게시물 본문 가져오기
# ───────────────────────────────────────────
def fetch_post_content(link):
    try:
        headers = {"User-Agent": "Mozilla/5.0 (compatible; NewsletterBot/1.0)"}
        resp = requests.get(link, headers=headers, timeout=15)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        content_div = soup.select_one("div.content") or soup.select_one("div.topic_content")
        if content_div:
            return content_div.get_text(separator="\n", strip=True)[:3000]

        paragraphs = soup.select("p")
        return "\n".join(p.get_text(strip=True) for p in paragraphs[:5])[:3000]
    except Exception:
        return ""


# ───────────────────────────────────────────
# 4. Gemini AI 요약
# ───────────────────────────────────────────
def summarize_post(client, post):
    content = fetch_post_content(post["link"])
    context = f"제목: {post['title']}\n\n본문:\n{content}" if content else f"제목: {post['title']}"

    prompt = f"""다음 IT/기술 뉴스 게시물을 비전공자도 쉽게 이해할 수 있도록 요약해주세요.

{context}

아래 형식으로 작성해주세요:
- **한 줄 요약**: 핵심을 한 문장으로 (비유나 일상적 표현 사용)
- **이게 뭔가요?**: 배경 지식 없이도 이해할 수 있는 설명 (2~3문장)
- **왜 중요한가요?**: 실생활이나 산업에 미치는 영향 (2~3문장)
- **핵심 포인트**: 기억할 만한 사실 2~3가지 (불릿 포인트)

전문 용어는 반드시 쉬운 말로 풀어쓰고, 친근하고 읽기 쉬운 톤으로 작성해주세요."""

    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=prompt,
    )
    return response.text


# ───────────────────────────────────────────
# 5. HTML 뉴스레터 생성
# ───────────────────────────────────────────
def md_to_html(text):
    import re
    text = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', text)
    lines = text.split("\n")
    html_lines = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        if line.startswith("- ") or line.startswith("• "):
            html_lines.append(f'<li>{line[2:]}</li>')
        else:
            html_lines.append(f'<p>{line}</p>')
    result = "\n".join(html_lines)
    result = re.sub(r'(<li>.*?</li>\n?)+', lambda m: f'<ul>{m.group()}</ul>', result, flags=re.DOTALL)
    return result


def build_html(date_kor, posts):
    articles_html = ""
    for i, item in enumerate(posts, 1):
        summary_html = md_to_html(item["summary"])
        origin_link  = (f'&nbsp;|&nbsp; <a href="{item["origin_url"]}" target="_blank">원문 보기 →</a>'
                        if item["origin_url"] != item["link"] else "")
        articles_html += f"""
        <div class="article">
          <div class="article-num">{i}</div>
          <h2 class="article-title">
            <a href="{item['link']}" target="_blank">{item['title']}</a>
          </h2>
          <div class="article-meta">
            👍 {item['points']}점 &nbsp;|&nbsp; 💬 {item['comments']}개 댓글{origin_link}
          </div>
          <div class="summary">{summary_html}</div>
        </div>"""

    return f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>GeekNews 뉴스레터 - {date_kor}</title>
<style>
  body {{
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
    background: #f4f6f8; margin: 0; padding: 0; color: #1a1a2e;
  }}
  .wrapper {{ max-width: 680px; margin: 0 auto; background: #fff; }}
  .header {{
    background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%);
    padding: 36px 32px; text-align: center;
  }}
  .header h1 {{ color: #fff; font-size: 24px; margin: 0 0 6px; }}
  .header .subtitle {{ color: #a0aec0; font-size: 14px; margin: 0; }}
  .header .date-badge {{
    display: inline-block; background: #4a90e2; color: white;
    font-size: 13px; padding: 4px 14px; border-radius: 20px; margin-top: 12px;
  }}
  .content {{ padding: 24px 32px; }}
  .intro {{
    background: #f0f4ff; border-left: 4px solid #4a90e2;
    padding: 14px 18px; border-radius: 0 8px 8px 0;
    margin-bottom: 28px; font-size: 14px; color: #4a5568; line-height: 1.6;
  }}
  .article {{
    border: 1px solid #e8ecf0; border-radius: 12px;
    padding: 24px; margin-bottom: 20px; position: relative;
  }}
  .article-num {{
    position: absolute; top: -12px; left: 20px;
    background: #4a90e2; color: white; font-size: 12px; font-weight: 700;
    width: 24px; height: 24px; border-radius: 50%;
    display: flex; align-items: center; justify-content: center;
  }}
  .article-title {{ font-size: 17px; font-weight: 700; margin: 0 0 8px; line-height: 1.4; }}
  .article-title a {{ color: #1a1a2e; text-decoration: none; }}
  .article-title a:hover {{ color: #4a90e2; text-decoration: underline; }}
  .article-meta {{ font-size: 12px; color: #718096; margin-bottom: 14px; }}
  .article-meta a {{ color: #4a90e2; text-decoration: none; }}
  .summary {{
    background: #f8fafc; border-radius: 8px;
    padding: 16px 18px; font-size: 14px; line-height: 1.7; color: #2d3748;
  }}
  .summary p {{ margin: 0 0 8px; }}
  .summary ul {{ margin: 4px 0 8px; padding-left: 20px; }}
  .summary li {{ margin-bottom: 4px; }}
  .footer {{
    background: #f4f6f8; padding: 24px 32px; text-align: center;
    font-size: 12px; color: #a0aec0; border-top: 1px solid #e8ecf0;
  }}
  .footer a {{ color: #4a90e2; text-decoration: none; }}
</style>
</head>
<body>
<div class="wrapper">
  <div class="header">
    <h1>📰 GeekNews 데일리 브리핑</h1>
    <p class="subtitle">비전공자도 쉽게 읽는 IT 뉴스 요약</p>
    <span class="date-badge">{date_kor}</span>
  </div>
  <div class="content">
    <div class="intro">
      어제 <strong>GeekNews</strong>에 올라온 주요 게시물
      <strong>{len(posts)}개</strong>를 쉽게 정리했습니다.
      제목을 클릭하면 원문 게시물로 이동합니다. ☕
    </div>
    {articles_html}
  </div>
  <div class="footer">
    <p>이 뉴스레터는 <a href="{BASE_URL}">GeekNews</a>의 게시물을
       Google Gemini AI가 요약한 것입니다.</p>
    <p>원문 출처: <a href="{BASE_URL}">{BASE_URL}</a></p>
  </div>
</div>
</body>
</html>"""


# ───────────────────────────────────────────
# 6. Gmail 발송
# ───────────────────────────────────────────
def send_email(subject, html_body, recipient):
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = GMAIL_USER
    msg["To"]      = recipient
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(GMAIL_USER, GMAIL_APP_PW)
        server.sendmail(GMAIL_USER, recipient, msg.as_string())
    print(f"✅ 이메일 발송 완료 → {recipient}")


# ───────────────────────────────────────────
# 메인
# ───────────────────────────────────────────
def main():
    date_str, date_kor = get_yesterday()
    print(f"📅 수집 날짜: {date_kor} ({date_str})")

    print("🔍 GeekNews 크롤링 중...")
    posts = fetch_yesterday_posts(date_str)
    if not posts:
        print("⚠️  어제자 게시물이 없습니다. 종료합니다.")
        return
    print(f"   → {len(posts)}개 게시물 발견")

    client = genai.Client(api_key=GEMINI_API_KEY)
    for i, post in enumerate(posts, 1):
        print(f"🤖 요약 중 ({i}/{len(posts)}): {post['title'][:40]}...")
        post["summary"] = summarize_post(client, post)

    html    = build_html(date_kor, posts)
    subject = f"📰 GeekNews 데일리 브리핑 | {date_kor}"
    send_email(subject, html, RECIPIENT_EMAIL)


if __name__ == "__main__":
    main()
