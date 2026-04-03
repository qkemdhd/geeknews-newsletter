#!/usr/bin/env python3
"""
GeekNews (news.hada.io/new) 어제자 게시물을 수집하여
Google Gemini AI로 요약 후 Gmail로 발송하는 스크립트
+ GitHub Pages에 인터랙티브 HTML 저장
"""

import os
import re
import smtplib
import subprocess
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
GITHUB_TOKEN    = os.environ.get("GITHUB_TOKEN", "")
GITHUB_REPO     = os.environ.get("GITHUB_REPOSITORY", "")  # "owner/repo" 형식

BASE_URL = "https://news.hada.io"
KST      = timezone(timedelta(hours=9))


def get_yesterday():
    yesterday = datetime.now(KST) - timedelta(days=1)
    return yesterday.strftime("%Y-%m-%d"), yesterday.strftime("%Y년 %m월 %d일")


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


def fetch_yesterday_posts(date_str):
    headers = {"User-Agent": "Mozilla/5.0 (compatible; NewsletterBot/1.0)"}
    posts   = []
    for page in range(1, 10):
        url  = f"{BASE_URL}/new?page={page}"
        resp = requests.get(url, headers=headers, timeout=15)
        resp.raise_for_status()
        soup  = BeautifulSoup(resp.text, "html.parser")
        items = soup.select("div.topic_row")
        if not items: break
        stop = False
        for item in items:
            info_div  = item.select_one("div.topicinfo")
            info_text = info_div.get_text(" ", strip=True) if info_div else ""
            item_date = parse_posted_date(info_text)
            if item_date is None: continue
            if item_date == date_str:
                title_a  = item.select_one("div.topictitle > a")
                if not title_a: continue
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
                stop = True; break
        if stop: break
    return posts


def find_related(current_post, all_posts, n=3):
    stopwords = {'show','gn','ai','및','의','를','을','이','가','에','은','는','로','으로',
                 '와','과','도','만','에서','한','하는','하기','하여','대한','위한','있는',
                 '없는','통해','기반','활용','사용'}
    def keywords(title):
        words = re.findall(r'[가-힣a-zA-Z]{2,}', title.lower())
        return {w for w in words if w not in stopwords}
    cur_kw = keywords(current_post["title"])
    scored = []
    for p in all_posts:
        if p["link"] == current_post["link"]: continue
        score = len(cur_kw & keywords(p["title"]))
        if score > 0: scored.append((score, p))
    scored.sort(key=lambda x: -x[0])
    return [p for _, p in scored[:n]]


def fetch_post_content(gn_link):
    try:
        headers = {"User-Agent": "Mozilla/5.0 (compatible; NewsletterBot/1.0)"}
        resp = requests.get(gn_link, headers=headers, timeout=15)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        content_div = soup.select_one("div.content, div.topic_content")
        if content_div:
            return content_div.get_text(separator="\n", strip=True)[:3000]
        return "\n".join(p.get_text(strip=True) for p in soup.select("p")[:5])[:3000]
    except Exception:
        return ""


# 카테고리별 색상 (배경색, 글자색)
CATEGORY_COLORS = {
    "AI/ML":        ("#e0f2fe", "#0369a1"),
    "개발/프로그래밍": ("#f0fdf4", "#166534"),
    "보안":          ("#fef2f2", "#991b1b"),
    "스타트업":       ("#fdf4ff", "#7e22ce"),
    "클라우드/인프라": ("#fff7ed", "#9a3412"),
    "오픈소스":       ("#f0fdfa", "#115e59"),
    "데이터":         ("#fefce8", "#854d0e"),
    "모바일":         ("#f5f3ff", "#5b21b6"),
    "웹":            ("#fff1f2", "#9f1239"),
    "기타":          ("#f8fafc", "#475569"),
}

def summarize_post(client, post):
    content = fetch_post_content(post["link"])
    context = f"제목: {post['title']}\n\n본문:\n{content}" if content else f"제목: {post['title']}"
    prompt  = f"""다음 IT/기술 뉴스를 비전공자도 이해할 수 있도록 요약해주세요.
도입부 없이 아래 형식으로 바로 시작하세요.

{context}

형식:
**카테고리**: (아래 중 하나만 선택: AI/ML, 개발/프로그래밍, 보안, 스타트업, 클라우드/인프라, 오픈소스, 데이터, 모바일, 웹, 기타)
**한 줄 요약**: (핵심을 일상적 비유로 한 문장)
**이게 뭔가요?**: (배경 지식 없이 이해할 수 있는 설명, 2~3문장)
**왜 중요한가요?**: (실생활·산업에 미치는 영향, 2~3문장)
**핵심 포인트**:
- (기억할 만한 사실 1)
- (기억할 만한 사실 2)
- (기억할 만한 사실 3)

전문 용어는 쉬운 말로 풀어쓰고, 친근한 톤으로 작성하세요."""
    response = client.models.generate_content(model="gemini-2.5-flash", contents=prompt)
    text = response.text

    # 카테고리 추출
    category = "기타"
    m = re.search(r'\*\*카테고리\*\*\s*:\s*(.+)', text)
    if m:
        raw = m.group(1).strip()
        for key in CATEGORY_COLORS:
            if key in raw:
                category = key
                break
        # 카테고리 줄은 요약 본문에서 제거
        text = re.sub(r'\*\*카테고리\*\*\s*:.+\n?', '', text).strip()

    return text, category


def md_to_html(text):
    text = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', text)
    lines = text.split("\n")
    html_lines = []
    for line in lines:
        line = line.strip()
        if not line: continue
        if line.startswith("- ") or line.startswith("• "):
            html_lines.append(f'<li>{line[2:]}</li>')
        else:
            html_lines.append(f'<p>{line}</p>')
    result = "\n".join(html_lines)
    result = re.sub(r'(<li>.*?</li>\n?)+', lambda m: f'<ul>{m.group()}</ul>', result, flags=re.DOTALL)
    return result


# ───────────────────────────────────────────
# 이메일용 HTML (이메일 클라이언트 호환)
# - 아코디언 없이 전체 펼침
# - flexbox 대신 line-height로 번호 정렬
# ───────────────────────────────────────────
def build_email_html(date_kor, posts, web_url=""):
    articles_html = ""
    for i, post in enumerate(posts, 1):
        summary_html = md_to_html(post["summary"])
        origin_link  = (f'&nbsp;|&nbsp;<a href="{post["origin_url"]}" style="color:#4a90e2;text-decoration:none;">원문 보기 →</a>'
                        if post["origin_url"] and post["origin_url"] != post["link"] else "")
        related      = find_related(post, posts)
        related_html = ""
        if related:
            items = "".join(f'<li style="margin-bottom:5px;font-size:13px;"><a href="{r["link"]}" style="color:#1a1a2e;text-decoration:none;">{r["title"]}</a></li>' for r in related)
            related_html = f"""
            <div style="margin-top:14px;background:#fffbeb;border:1px solid #fde68a;border-radius:8px;padding:13px 16px;">
              <div style="font-size:12px;font-weight:700;color:#92400e;margin-bottom:8px;">📎 연관 뉴스</div>
              <ul style="padding-left:16px;margin:0;">{items}</ul>
            </div>"""

        # 카테고리 뱃지
        cat        = post.get("category", "기타")
        bg, fg     = CATEGORY_COLORS.get(cat, ("#f8fafc", "#475569"))
        cat_badge  = f'<span style="display:inline-block;background:{bg};color:{fg};font-size:11px;font-weight:700;padding:2px 10px;border-radius:20px;margin-bottom:8px;">{cat}</span>'

        articles_html += f"""
        <div style="border:1px solid #e2e8f0;border-radius:12px;margin-bottom:14px;overflow:hidden;">
          <!-- 헤더 -->
          <div style="padding:16px 20px;background:#f0f4ff;border-bottom:1px solid #e2e8f0;">
            {cat_badge}
            <table cellpadding="0" cellspacing="0" border="0" width="100%">
              <tr>
                <td width="30" valign="middle">
                  <div style="width:26px;height:26px;background:#4a90e2;border-radius:50%;
                              text-align:center;line-height:26px;
                              font-size:12px;font-weight:700;color:#ffffff;">
                    {i}
                  </div>
                </td>
                <td valign="middle" style="padding-left:10px;">
                  <a href="{post['link']}" style="font-size:15px;font-weight:600;color:#1a1a2e;text-decoration:none;">
                    {post['title']}
                  </a>
                </td>
              </tr>
            </table>
            <div style="margin-top:8px;font-size:12px;color:#718096;padding-left:36px;">
              👍 {post['points']}점 &nbsp;·&nbsp; 💬 {post['comments']}
              &nbsp;|&nbsp;<a href="{post['link']}" style="color:#4a90e2;text-decoration:none;">GeekNews →</a>
              {origin_link}
            </div>
          </div>
          <!-- 요약 본문 -->
          <div style="padding:20px;background:#fff;">
            <div style="background:#f8fafc;border-radius:8px;padding:16px 18px;font-size:14px;line-height:1.75;color:#2d3748;">
              {summary_html}
            </div>
            {related_html}
          </div>
        </div>"""

    web_btn = ""
    if web_url:
        web_btn = f"""
        <div style="text-align:center;margin-bottom:20px;">
          <a href="{web_url}"
             style="display:inline-block;background:#4a90e2;color:#ffffff;
                    font-size:14px;font-weight:600;padding:10px 28px;
                    border-radius:8px;text-decoration:none;">
            🌐 아코디언 버전으로 보기
          </a>
        </div>"""

    return f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>GeekNews 뉴스레터 - {date_kor}</title>
</head>
<body style="margin:0;padding:0;background:#f0f2f5;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI','Noto Sans KR',sans-serif;color:#1a1a2e;">
<div style="max-width:700px;margin:0 auto;background:#ffffff;">
  <!-- 헤더 -->
  <div style="background:linear-gradient(135deg,#1a1a2e 0%,#16213e 100%);padding:40px 32px;text-align:center;">
    <h1 style="color:#ffffff;font-size:22px;font-weight:700;margin:0 0 6px;">📰 GeekNews 데일리 브리핑</h1>
    <p style="color:#a0aec0;font-size:13px;margin:0;">비전공자도 쉽게 읽는 IT 뉴스 요약</p>
    <span style="display:inline-block;background:#4a90e2;color:#ffffff;font-size:12px;padding:5px 16px;border-radius:20px;margin-top:14px;">{date_kor}</span>
  </div>
  <!-- 본문 -->
  <div style="padding:24px 28px;">
    <div style="background:#f0f4ff;border-left:4px solid #4a90e2;padding:13px 18px;border-radius:0 8px 8px 0;margin-bottom:24px;font-size:14px;color:#4a5568;line-height:1.6;">
      어제 <strong>GeekNews</strong>에 올라온 게시물 <strong>{len(posts)}개</strong>를 정리했습니다. ☕
    </div>
    {web_btn}
    {articles_html}
  </div>
  <!-- 푸터 -->
  <div style="background:#f0f2f5;padding:24px 32px;text-align:center;font-size:12px;color:#a0aec0;border-top:1px solid #e2e8f0;">
    <p>이 뉴스레터는 <a href="{BASE_URL}" style="color:#4a90e2;text-decoration:none;">GeekNews</a> 게시물을 Google Gemini AI가 요약한 것입니다.</p>
  </div>
</div>
</body>
</html>"""


# ───────────────────────────────────────────
# 웹(GitHub Pages)용 HTML (아코디언 포함)
# ───────────────────────────────────────────
def build_web_html(date_kor, posts):
    articles_html = ""
    for i, post in enumerate(posts, 1):
        summary_html = md_to_html(post["summary"])
        origin_link  = (f'<a href="{post["origin_url"]}" target="_blank">원문 보기 →</a>'
                        if post["origin_url"] and post["origin_url"] != post["link"] else "")
        related      = find_related(post, posts)
        related_html = ""
        if related:
            items = "".join(f'<li><a href="{r["link"]}" target="_blank">{r["title"]}</a></li>' for r in related)
            related_html = f'<div class="related"><div class="related-title">📎 연관 뉴스</div><ul>{items}</ul></div>'

        cat    = post.get("category", "기타")
        bg, fg = CATEGORY_COLORS.get(cat, ("#f8fafc", "#475569"))

        articles_html += f"""
        <div class="article">
          <details>
            <summary>
              <span class="article-num">{i}</span>
              <span class="article-title-text">{post['title']}</span>
              <span class="cat-badge" style="background:{bg};color:{fg};">{cat}</span>
              <span class="meta-inline">👍 {post['points']}점 · 💬 {post['comments']}</span>
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
<title>GeekNews 브리핑 - {date_kor}</title>
<style>
  *{{box-sizing:border-box;margin:0;padding:0;}}
  body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI','Noto Sans KR',sans-serif;background:#f0f2f5;color:#1a1a2e;}}
  .wrapper{{max-width:700px;margin:0 auto;background:#fff;}}
  .header{{background:linear-gradient(135deg,#1a1a2e 0%,#16213e 100%);padding:40px 32px;text-align:center;}}
  .header h1{{color:#fff;font-size:22px;font-weight:700;margin-bottom:6px;}}
  .header .subtitle{{color:#a0aec0;font-size:13px;}}
  .header .date-badge{{display:inline-block;background:#4a90e2;color:#fff;font-size:12px;padding:5px 16px;border-radius:20px;margin-top:14px;}}
  .content{{padding:24px 28px;}}
  .intro{{background:#f0f4ff;border-left:4px solid #4a90e2;padding:13px 18px;border-radius:0 8px 8px 0;margin-bottom:24px;font-size:14px;color:#4a5568;line-height:1.6;}}
  .article{{border:1px solid #e2e8f0;border-radius:12px;margin-bottom:14px;overflow:hidden;}}
  details>summary{{list-style:none;display:flex;align-items:center;gap:12px;padding:16px 20px;cursor:pointer;background:#fff;transition:background 0.15s;user-select:none;}}
  details>summary::-webkit-details-marker{{display:none;}}
  details>summary:hover{{background:#f8fafc;}}
  details[open]>summary{{background:#f0f4ff;border-bottom:1px solid #e2e8f0;}}
  .article-num{{flex-shrink:0;width:26px;height:26px;background:#4a90e2;color:#fff;border-radius:50%;font-size:12px;font-weight:700;display:inline-flex;align-items:center;justify-content:center;line-height:1;}}
  .article-title-text{{flex:1;font-size:15px;font-weight:600;color:#1a1a2e;line-height:1.4;}}
  .cat-badge{{flex-shrink:0;font-size:11px;font-weight:700;padding:2px 10px;border-radius:20px;white-space:nowrap;}}
  .meta-inline{{flex-shrink:0;font-size:12px;color:#718096;white-space:nowrap;}}
  .article-body{{padding:20px;background:#fff;}}
  .article-links{{font-size:13px;margin-bottom:14px;}}
  .article-links a{{color:#4a90e2;text-decoration:none;font-weight:500;}}
  .summary{{background:#f8fafc;border-radius:8px;padding:16px 18px;font-size:14px;line-height:1.75;color:#2d3748;}}
  .summary p{{margin-bottom:8px;}}
  .summary ul{{padding-left:18px;margin:6px 0;}}
  .summary li{{margin-bottom:5px;}}
  .related{{margin-top:14px;background:#fffbeb;border:1px solid #fde68a;border-radius:8px;padding:13px 16px;}}
  .related-title{{font-size:12px;font-weight:700;color:#92400e;margin-bottom:8px;}}
  .related ul{{padding-left:16px;}}
  .related li{{margin-bottom:5px;font-size:13px;}}
  .related a{{color:#1a1a2e;text-decoration:none;}}
  .footer{{background:#f0f2f5;padding:24px 32px;text-align:center;font-size:12px;color:#a0aec0;border-top:1px solid #e2e8f0;}}
  .footer a{{color:#4a90e2;text-decoration:none;}}
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
      제목을 클릭하면 요약이 펼쳐집니다. ☕
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
# GitHub Pages에 HTML 저장 (git push)
# ───────────────────────────────────────────
def save_to_github_pages(html, date_str):
    """docs/index.html 에 저장하고 push → GitHub Pages로 서빙"""
    try:
        os.makedirs("docs", exist_ok=True)
        with open("docs/index.html", "w", encoding="utf-8") as f:
            f.write(html)

        owner_repo = GITHUB_REPO  # "owner/repo"
        subprocess.run(["git", "config", "user.email", "action@github.com"], check=True)
        subprocess.run(["git", "config", "user.name", "GitHub Actions"], check=True)
        subprocess.run(["git", "add", "docs/index.html"], check=True)
        subprocess.run(["git", "commit", "-m", f"뉴스레터 업데이트: {date_str}"], check=True)

        remote = f"https://x-access-token:{GITHUB_TOKEN}@github.com/{owner_repo}.git"
        subprocess.run(["git", "push", remote, "HEAD:main"], check=True)

        # GitHub Pages URL
        owner = owner_repo.split("/")[0]
        repo  = owner_repo.split("/")[1]
        url   = f"https://{owner}.github.io/{repo}/"
        print(f"✅ GitHub Pages 업로드 완료 → {url}")
        return url
    except Exception as e:
        print(f"⚠️  GitHub Pages 저장 실패: {e}")
        return ""


# ───────────────────────────────────────────
# Gmail 발송 (다중 수신자 BCC)
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
    print(f"✅ 이메일 발송 완료 → {', '.join(recipients)}")


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
        post["summary"], post["category"] = summarize_post(client, post)

    # 웹용 HTML → GitHub Pages
    web_html = build_web_html(date_kor, posts)
    web_url  = save_to_github_pages(web_html, date_str)

    # 이메일용 HTML 발송
    email_html = build_email_html(date_kor, posts, web_url)
    subject    = f"📰 GeekNews 데일리 브리핑 | {date_kor}"
    send_email(subject, email_html, RECIPIENT_EMAIL)


if __name__ == "__main__":
    main()
