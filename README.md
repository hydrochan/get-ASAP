# get-ASAP

> **Gmail 저널 ASAP 알림 → Notion 자동 저장 → 웹 대시보드**
> 촉매·에너지 분야의 신규 논문(ASAP)을 놓치지 않기 위한 엔드투엔드 자동화.

**🔗 라이브 데모 — [get-asap.hydrochan.com](https://get-asap.hydrochan.com)** (로그인 없이 열람)

7개 출판사·60여 개 저널의 ASAP 알림 메일을 파싱해 Notion에 저장하고, Tailwind + Chart.js 대시보드로 시각화합니다. 매 6시간 cron이 새 메일만 증분 처리하고 제목 중복을 걸러 쌓습니다.

## 주요 기능

- **플러그인 파서** — `parsers/`에 파일 하나 추가하면 새 출판사 자동 등록
- **증분 동기화** — Gmail historyId + 처리 라벨로 중복 실행에 안전
- **대시보드 4뷰** — Home · Analytics(키워드 트렌드·워드클라우드) · Browse · Advanced Search
- **익명 공개 열람 + 관리자 무폼 로그인** — nginx Basic → 서버 세션 쿠키

## Quick Start

```bash
git clone https://github.com/hydrochan/get-ASAP.git && cd get-ASAP
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # NOTION_TOKEN 등 입력
python get_token_curl.py      # Gmail OAuth 1회
python main.py                # 파이프라인 실행
python dashboard/server.py    # 대시보드 로컬 실행
```

## Stack

Python 3.11 · Gmail/Notion API · BeautifulSoup4 · Tailwind · Chart.js · Nginx · Cloudflare · Oracle Cloud · SQLite/CSV

---

<sub>© 2026 Chan Kim. All rights reserved — see [LICENSE](LICENSE).</sub>
