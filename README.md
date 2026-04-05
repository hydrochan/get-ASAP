# get-ASAP

Gmail에서 학술 저널 ASAP(As Soon As Published) 알림 메일을 자동으로 감지하여 논문 제목을 추출하고, Notion 데이터���이스에 저장하는 자동화 파이프라인.

## Overview

```
Gmail 수신함                    Notion DB
┌──────────────┐              ┌───��──────────────┐
│ ACS          │              │ Title            │
│ Wiley        │  ──parse──>  │ Journal          │
│ Elsevier     │              │ Date             ��
│ Science      │              │ Status: 대기중    │
└──────────────┘              └──────────────────┘
     │                               │
     └── historyId 증분 동기화        └── 제목 기반 중복 방지
         (새 메일만 처리)                 (동일 논문 재저장 차단)
```

## Features

- **4개 출판사 지원**: ACS, Wiley, Elsevier, Science/Science Advances
- **플러그인 파서**: `parsers/` 디렉토리에 파일 추가만으로 새 출판사 등록
- **증분 동기화**: Gmail historyId 기반으로 새 메일만 처리 (중복 실행 안전)
- **Notion 자동 저장**: 논문 제목 + 저널명 + 상태("대기중") 자동 저장
- **중복 방지**: 제목 기반 중복 검사로 동일 논문 재저장 차단
- **cron 자동화**: 오라클 클라우드 Ubuntu에서 6시간 간격 자동 실행
- **로깅**: `logs/get-asap.log` 에 실행 결과 기록 (RotatingFileHandler)

## Quick Start

```bash
# 1. 클론 & 설치
git clone https://github.com/hydrochan/get-ASAP.git
cd get-ASAP
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# 2. 환경변수 설정
cp .env.example .env
# .env 파일에 NOTION_TOKEN, NOTION_DATABASE_ID 등 입력

# 3. Gmail OAuth 인증 (최초 1회)
python -c "from auth import get_gmail_service; get_gmail_service()"
# 브라우저에서 Google 계정 인증 → token.json 자동 생성

# 4. 실행
python main.py --dry-run --verbose  # 테스트 (Notion 저장 없이)
python main.py                      # 실제 실행
```

## Usage

```bash
python main.py                # 전체 파이프라인 실행
python main.py --dry-run      # Notion 저장 없이 파싱 결과만 출력
python main.py --verbose      # DEBUG 레벨 로그 활성화
```

## Architecture

```
get-ASAP/
├── main.py              # 파이프라인 오케스트레이터 (진입점)
├── auth.py              # Gmail OAuth 2.0 인증
├── gmail_client.py      # Gmail API 클라이언트 (필터링, 증분동기화, 라벨)
├── notion_auth.py       # Notion API 인증
├── notion_client_mod.py # Notion DB CRUD (생성, 저장, 중복방지)
├── parser_registry.py   # ���서 자동 디스커버리
├── models.py            # PaperMetadata 데이터 모��
├── config.py            # 환경변수 관리
├── publishers.json      # 출판사 ��정 (sender, journals, doi_prefix)
├── deploy.sh            # 서버 배포 스크립트
├── parsers/
│   ├── base.py          # BaseParser ABC
│   ├── acs.py           # ACS Publications 파서
│   ├── wiley.py         # Wiley 파서
│   ├── elsevier.py      # Elsevier 파서
│   └── science.py       # Science/Science Advances 파서
└── tests/               # 85개 단위 테스트 (pytest)
```

## Pipeline Flow

```
1. Gmail API 인증 (token.json 자동 갱신)
2. publishers.json에서 출판사 발신자 목록 로드
3. Gmail API로 ASAP 메일 검색 (from: 필터)
4. historyId 증분 동기화 (새 메일만)
5. 각 메일 → 발신자로 출판사 매칭 → 해당 파서 실행
6. BeautifulSoup4로 HTML 파싱 → 논문 제목 추출
7. 저널명 추론 (제목/발신자/publishers.json 매핑)
8. Notion DB에 저장 (제목 기반 중복 검사)
9. 처리 완료 메일에 "get-ASAP-processed" 라벨 부여
10. state.json에 historyId 저장 (다음 실행 기준점)
```

## Adding a New Publisher

기존 출판사(ACS, Wiley 등)의 **새 저널**은 `publishers.json`의 `journals` 배열에 이름만 추가:

```json
"journals": ["Angewandte Chemie", "Advanced Materials", "NEW JOURNAL NAME"]
```

**새 출판사**는 3단계:

1. `publishers.json`에 항목 추가
2. `parsers/` 에 파서 파일 생성 (`BaseParser` 상속)
3. 자동 등록 완료 (코드 수정 불필요)

## Server Deployment

```bash
# 오라클 클라우드 Ubuntu
git clone https://github.com/hydrochan/get-ASAP.git
cd get-ASAP
bash deploy.sh

# .env, token.json, credentials.json은 SCP로 별도 전송
scp .env token.json credentials.json ubuntu@SERVER:~/get-ASAP/

# cron 등록 (매 6시간)
crontab -e
# 0 */6 * * * cd /home/ubuntu/get-ASAP && .venv/bin/python main.py >> logs/cron.log 2>&1
```

## Tech Stack

| Component | Technology |
|-----------|-----------|
| Runtime | Python 3.11+ |
| Gmail API | google-api-python-client + google-auth-oauthlib |
| Notion API | notion-client 3.0.0 |
| HTML Parsing | BeautifulSoup4 + lxml |
| Testing | pytest (85 tests) |
| Deployment | Oracle Cloud Ubuntu + cron |
| Config | python-dotenv + .env |

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `NOTION_TOKEN` | Yes | Notion Integration Token |
| `NOTION_DATABASE_ID` | Yes* | 기존 Notion DB ID |
| `NOTION_PARENT_PAGE_ID` | Yes* | DB 신규 생성 시 부모 페이지 ID |
| `GMAIL_CREDENTIALS_PATH` | No | OAuth credentials 경로 (기본: credentials.json) |
| `GMAIL_TOKEN_PATH` | No | OAuth token 경�� (기본: token.json) |
| `CHECK_INTERVAL_HOURS` | No | 실행 간격 (기본: 6) |

*`NOTION_DATABASE_ID` 또는 `NOTION_PARENT_PAGE_ID` 중 하나 필수

## License

MIT
