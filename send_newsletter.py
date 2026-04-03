#!/usr/bin/env python3
"""
GeekNews (news.hada.io/new) 어제자 게시물을 수집하여
Google Gemini AI로 요약 후 Gmail로 발송하는 스크립트
"""

import os
import re
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
# 2. 상대 시간 파싱
# ───────────────────────────────────────────
def parse_posted_date(info_text):
    now = datetime.now(KST)
    m = re.search(r'(\d+)\s*분\s*전', info_text)
    if m: return (now - timedelta(minutes=int(m.group(1)))).strftime("%Y-%m-%d")
    m = re.search(r'(\d+)\s*시간\s*전', info_text)
    if m: return (now - timedelta(hours=int(m.group(1)))).strftime("%Y-%m-%d")
    m = re.search(r'(\d+)\s*일\s*전', info_text)
    if m: return (now - timedelta(days=int(m.group(1)))).strftime("%Y-%m-%d")
    if '어제' in info_text: return (now - timedelta(days=1)).strftime("%Y-%m-%d")
    return None


# ───────────────────────────────────────────
# 3. GeekNews /new 크롤링
# ───────────────────────────────────────────
def fetch_yesterday_posts(date_str):
    headers = {"User-Agent": "Mozilla/5.0 (compatible; NewsletterBot/1.0)"}
    posts   = []

    for page in range(1, 10):
        url  = f"{BASE_URL}/new?page={page}"
        resp = requests.get(url, headers=headers, timeout=15)
        resp.raise_for_status()
        soup  = BeautifulSoup(resp.text, "html.parser")
        items = soup.select("div.topic_row")
        if not items:
            break

        stop = False
        for item in items:
            info_div  = item.select_one("div.topicinfo")
            info_text = info_div.get_text(" ", strip=True) if info_div else ""
            item_date = parse_posted_date(info_text)
            if item_date is None:
                continue

            if item_date == date_str:
                title_a  = item.select_one("div.topictitle > a")
                if not title_a:
                    continue
                h1_tag   = title_a.find("h1")
                title    = h1_tag.get_text(strip=True) if h1_tag else title_a.get_text(strip=True)
                orig_url = title_a.get("href", "")
                gn_a     = item.select_one("div.topicdesc > a")
                gn_link  = BASE_URL + "/" + gn_a["href"].lstrip("/") if gn_a else BASE_URL
                pt_span  = item.select_one("span[id^='tp']")
                cmt_a    = item.select_one("a.u")
                posts.append({
                    "title":      title,
                    "link":       gn_link,
                    "origin_url": orig_url,
                    "points":     pt_span.get_text(strip=True) if pt_span else "0",
                    "comments":   cmt_a.get_text(strip=True)   if cmt_a   else "댓글 없음",
                })
            elif item_date < date_str:
                print(f"   → {item_date} 게시물 발견, 수집 종료 (총 {len(posts)}개)")
                stop = True
                break
        if stop:
            break

    return posts


# ───────────────────────────────────────────
# 4. 연관 뉴스 찾기 (같은 날 게시물 중 키워드 매칭)
# ───────────────────────────────────────────
def find_related(current_post, all_posts, n=3):
    """제목에서 의미있는 키워드를 추출해 다른 게시물과 매칭"""
    # 불용어
    stopwords = {'show', 'gn', 'ai', '및', '의', '를', '을', '이', '가', '에', '은', '는',
                 '로', '으로', '와', '과', '도', '만', '에서', '한', '하는', '하기', '하여',
                 '대한', '위한', '있는', '없는', '통해', '기반', '활용', '사용'}

    def keywords(title):
        words = re.findall(r'[가-힣a-zA-Z]{2,}', title.lower())
        return {w for w in words if w not in stopwords}

    cur_kw = keywords(current_post["title"])
    scored = []
    for p in all_posts:
        if p["link"] == current_post["link"]:
            continue
        score = len(cur_kw & keywords(p["title"]))
        if score > 0:
            scored.append((score, p))

    scored.sort(key=lambda x: -x[0])
    return [p for _, p in scored[:n]]


# ───────────────────────────────────────────
# 5. 게시물 본문 가져오기
# ───────────────────────────────────────────
def fetch_post_content(gn_link):
    try:
        headers = {"User-Agent": "Mozilla/5.0 (compatible; NewsletterBot/1.0)"}
        resp = requests.get(gn_link, headers=headers, timeout=15)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        content_div = soup.select_one("div.content, div.topic_content")
        if content_div:
            return content_div.get_text(separator="\n", strip=True)[:3000]
        paragraphs = soup.select("p")
        return "\n".join(p.get_text(strip=True) for p in paragraphs[:5])[:3000]
    except Exception:
        return ""


# ───────────────────────────────────────────
# 6. Gemini AI 요약
# ───────────────────────────────────────────
def summarize_post(client, post):
    content = fetch_post_content(post["link"])
    context = f"제목: {post['title']}\n\n본문:\n{content}" if content else f"제목: {post['title']}"

    prompt = f"""다음 IT/기술 뉴스를 비전공자도 이해할 수 있도록 요약해주세요.
도입부 없이 아래 형식으로 바로 시작하세요.

{context}

형식:
**한 줄 요약**: (핵심을 일상적 비유로 한 문장)
**이게 뭔가요?**: (배경 지식 없이 이해할 수 있는 설명, 2~3문장)
**왜 중요한가요?**: (실생활·산업에 미치는 영향, 2~3문장)
**핵심 포인트**:
- (기억할 만한 사실 1)
- (기억할 만한 사실 2)
- (기억할 만한 사실 3)

전문 용어는 쉬운 말로 풀어쓰고, 친근한 톤으로 작성하세요."""

    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=prompt,
    )
    return response.text


# ───────────────────────────────────────────
# 7. HTML 뉴스레터 생성
# ───────────────────────────────────────────
def md_to_html(text):
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
    for i, post in enumerate(posts, 1):
        summary_html = md_to_html(post["summary"])
        origin_link  = (f'<a href="{post["origin_url"]}" target="_blank">원문 보기 →</a>'
                        if post["origin_url"] and post["origin_url"] != post["link"] else "")

        # 연관 뉴스
        related      = find_related(post, posts)
        related_html = ""
        if related:
            related_items = "".join(
                f'<li><a href="{r["link"]}" target="_blank">{r["title"]}</a></li>'
                for r in related
            )
            related_html = f"""
            <div class="related">
              <div class="related-title">📎 연관 뉴스</div>
              <ul>{related_items}</ul>
            </div>"""

        articles_html += f"""
        <div class="article">
          <details>
            <summary>
              <span class="article-num">{i}</span>
              <span class="article-title-text">{post['title']}</span>
              <span class="meta-inline">👍 {post['points']}점 &nbsp;·&nbsp; 💬 {post['comments']}</span>
            </summary>
            <div class="article-body">
              <div class="article-links">
                <a href="{post['link']}" target="_blank">GeekNews에서 보기</a>
                {"&nbsp;|&nbsp;" + origin_link if origin_link else ""}
              </div>
              <div class="summary">{summary_html}</div>
              {related_html}
            </div>
          </details>
        </div>"""

    return f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>GeekNews 뉴스레터 - {date_kor}</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', 'Noto Sans KR', sans-serif;
    background: #f0f2f5; color: #1a1a2e;
  }}
  .wrapper {{ max-width: 700px; margin: 0 auto; background: #fff; }}

  /* 헤더 */
  .header {{
    background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%);
    padding: 40px 32px; text-align: center;
  }}
  .header h1 {{ color: #fff; font-size: 22px; font-weight: 700; margin-bottom: 6px; }}
  .header .subtitle {{ color: #a0aec0; font-size: 13px; }}
  .header .date-badge {{
    display: inline-block; background: #4a90e2; color: #fff;
    font-size: 12px; padding: 5px 16px; border-radius: 20px; margin-top: 14px;
  }}

  /* 인트로 */
  .content {{ padding: 24px 28px; }}
  .intro {{
    background: #f0f4ff; border-left: 4px solid #4a90e2;
    padding: 13px 18px; border-radius: 0 8px 8px 0;
    margin-bottom: 24px; font-size: 14px; color: #4a5568; line-height: 1.6;
  }}

  /* 아코디언 아티클 */
  .article {{
    border: 1px solid #e2e8f0; border-radius: 12px;
    margin-bottom: 14px; overflow: hidden;
  }}
  details > summary {{
    list-style: none;
    display: flex;
    align-items: center;
    gap: 12px;
    padding: 16px 20px;
    cursor: pointer;
    background: #fff;
    transition: background 0.15s;
    user-select: none;
  }}
  details > summary::-webkit-details-marker {{ display: none; }}
  details > summary:hover {{ background: #f8fafc; }}
  details[open] > summary {{ background: #f0f4ff; border-bottom: 1px solid #e2e8f0; }}

  /* 번호 뱃지 */
  .article-num {{
    flex-shrink: 0;
    width: 26px; height: 26px;
    background: #4a90e2; color: #fff;
    border-radius: 50%;
    font-size: 12px; font-weight: 700;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    line-height: 1;
  }}

  .article-title-text {{
    flex: 1;
    font-size: 15px; font-weight: 600;
    color: #1a1a2e; line-height: 1.4;
  }}
  .meta-inline {{
    flex-shrink: 0;
    font-size: 12px; color: #718096; white-space: nowrap;
  }}

  /* 펼쳐진 본문 */
  .article-body {{ padding: 20px; background: #fff; }}
  .article-links {{
    font-size: 13px; margin-bottom: 14px;
  }}
  .article-links a {{
    color: #4a90e2; text-decoration: none; font-weight: 500;
  }}
  .article-links a:hover {{ text-decoration: underline; }}

  /* 요약 */
  .summary {{
    background: #f8fafc; border-radius: 8px;
    padding: 16px 18px; font-size: 14px; line-height: 1.75; color: #2d3748;
  }}
  .summary p {{ margin-bottom: 8px; }}
  .summary p:last-child {{ margin-bottom: 0; }}
  .summary ul {{ padding-left: 18px; margin: 6px 0; }}
  .summary li {{ margin-bottom: 5px; }}

  /* 연관 뉴스 */
  .related {{
    margin-top: 14px;
    background: #fffbeb; border: 1px solid #fde68a;
    border-radius: 8px; padding: 13px 16px;
  }}
  .related-title {{
    font-size: 12px; font-weight: 700;
    color: #92400e; margin-bottom: 8px;
  }}
  .related ul {{ padding-left: 16px; }}
  .related li {{ margin-bottom: 5px; font-size: 13px; }}
  .related a {{ color: #1a1a2e; text-decoration: none; }}
  .related a:hover {{ color: #4a90e2; text-decoration: underline; }}

  /* 푸터 */
  .footer {{
    background: #f0f2f5; padding: 24px 32px; text-align: center;
    font-size: 12px; color: #a0aec0; border-top: 1px solid #e2e8f0;
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
      어제 <strong>GeekNews</strong>에 올라온 게시물 <strong>{len(posts)}개</strong>를 정리했습니다.
      제목을 클릭하면 요약 내용이 펼쳐집니다. ☕
    </div>
    {articles_html}
  </div>
  <div class="footer">
    <p>이 뉴스레터는 <a href="{BASE_URL}">GeekNews</a> 게시물을 Google Gemini AI가 요약한 것입니다.</p>
  </div>
</div>
</body>
</html>"""


# ───────────────────────────────────────────
# 8. Gmail 발송 (다중 수신자 BCC)
# ───────────────────────────────────────────
def send_email(subject, html_body, recipients):
    if isinstance(recipients, str):
        recipients = [r.strip() for r in recipients.split(",") if r.strip()]
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = GMAIL_USER
    msg["To"]      = GMAIL_USER
    msg["Bcc"]     = ", ".join(recipients)
    msg.attach(MIMEText(html_body, "html", "utf-8"))
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(GMAIL_USER, GMAIL_APP_PW)
        server.sendmail(GMAIL_USER, recipients, msg.as_string())
    print(f"✅ 이메일 발송 완료 (숨은참조) → {', '.join(recipients)}")


# ───────────────────────────────────────────
# 메인
# ───────────────────────────────────────────
def main():
    date_str, date_kor = get_yesterday()
    print(f"📅 수집 날짜: {date_kor} ({date_str})")

    print("🔍 GeekNews /new 크롤링 중...")
    posts = fetch_yesterday_posts(date_str)
    if not posts:
        print("⚠️  어제자 게시물이 없습니다. 종료합니다.")
        return
    print(f"   → 총 {len(posts)}개 게시물 수집 완료")

    client = genai.Client(api_key=GEMINI_API_KEY)
    for i, post in enumerate(posts, 1):
        print(f"🤖 요약 중 ({i}/{len(posts)}): {post['title'][:45]}...")
        post["summary"] = summarize_post(client, post)

    html    = build_html(date_kor, posts)
    subject = f"📰 GeekNews 데일리 브리핑 | {date_kor}"
    send_email(subject, html, RECIPIENT_EMAIL)


if __name__ == "__main__":
    main()
