# 📰 GeekNews 데일리 뉴스레터 (Gemini 버전)

매일 오전 9시(KST), 어제 GeekNews 게시물을 **Google Gemini AI**가 요약해서 이메일로 발송합니다.  
완전 무료 · GitHub Actions · Gmail SMTP

---

## 🚀 설치 가이드 (처음 한 번만)

### Step 1. GitHub 저장소 만들기

1. [github.com](https://github.com) → 로그인
2. 우측 상단 **`+`** → **New repository**
3. Repository name: `geeknews-newsletter`
4. **Public** 선택 (Actions 무제한 무료)
5. **Create repository** 클릭

---

### Step 2. 파일 업로드

```bash
git clone https://github.com/[내_아이디]/geeknews-newsletter
cd geeknews-newsletter

# 압축 해제한 파일들 복사
cp send_newsletter.py .
cp requirements.txt .
mkdir -p .github/workflows
cp newsletter.yml .github/workflows/

git add .
git commit -m "뉴스레터 자동화 초기 설정"
git push
```

---

### Step 3. Gemini API 키 발급 (무료)

1. [aistudio.google.com/apikey](https://aistudio.google.com/apikey) 접속
2. Google 계정으로 로그인
3. **Create API key** 클릭
4. 생성된 키(`AIza...` 로 시작) 복사

> 💡 신용카드 불필요, 하루 최대 1,500건 요청 가능 (이 프로젝트는 하루 10~30건)

---

### Step 4. Gmail 앱 비밀번호 발급

1. [myaccount.google.com/security](https://myaccount.google.com/security) 접속
2. **2단계 인증** 활성화 (필수)
3. 검색창에 **"앱 비밀번호"** 검색
4. 앱 이름 `newsletter` 입력 → **만들기**
5. 16자리 비밀번호 복사 (공백 제거)

---

### Step 5. GitHub Secrets 4개 등록

저장소 → **Settings** → **Secrets and variables** → **Actions** → **New repository secret**

| Secret 이름 | 값 |
|------------|-----|
| `GEMINI_API_KEY` | Step 3에서 발급한 Gemini API 키 |
| `GMAIL_USER` | 발신 Gmail 주소 (`xxx@gmail.com`) |
| `GMAIL_APP_PASSWORD` | Step 4에서 발급한 16자리 앱 비밀번호 |
| `RECIPIENT_EMAIL` | 뉴스레터를 받을 이메일 주소 |

---

### Step 6. 테스트 실행

1. 저장소 → **Actions** 탭
2. **GeekNews 데일리 뉴스레터** 클릭
3. **Run workflow** → **Run workflow** 클릭
4. 초록색 체크 ✅ 확인 → 메일함 확인

---

## ⏰ 발송 시간 변경

`.github/workflows/newsletter.yml` 의 cron 값을 수정하세요:

| 원하는 시간 (KST) | cron 값 |
|-----------------|---------|
| 오전 7시 | `0 22 * * *` |
| 오전 8시 | `0 23 * * *` |
| **오전 9시 (기본값)** | `0 0 * * *` |
| 오전 10시 | `0 1 * * *` |

---

## 📁 파일 구조

```
geeknews-newsletter/
├── .github/workflows/
│   └── newsletter.yml      ← 스케줄 및 실행 설정
├── send_newsletter.py       ← 메인 스크립트
├── requirements.txt         ← Python 패키지
└── README.md
```

---

## 🔧 문제 해결

| 증상 | 원인 | 해결 |
|------|------|------|
| 메일 미발송 | Gmail 앱 비밀번호 오류 | 앱 비밀번호 재발급, 공백 제거 |
| 429 오류 | Gemini 무료 한도 초과 | 거의 발생 안 함. 발생 시 다음 날 자동 리셋 |
| 게시물 0개 | 해당 날짜 게시물 없음 | 정상 (공휴일 등) |
| Actions 미실행 | 저장소 비활성화 | Actions 탭 → Enable 클릭 |
