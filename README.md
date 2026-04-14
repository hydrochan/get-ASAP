# get-ASAP

Gmail에서 학술 저널 ASAP(As Soon As Published) 알림 메일을 자동으로 감지하여 논문 제목을 추출하고, Notion 데이터베이스에 저장하는 자동화 파이프라인.

## Overview

```mermaid
flowchart LR
    subgraph GMAIL["Gmail 수신함"]
        G1["ACS"]
        G2["Wiley"]
        G3["Elsevier"]
        G4["Science"]
    end
    subgraph NOTION["Notion DB"]
        N1["Title"]
        N2["Journal"]
        N3["Date"]
        N4["Status: 대기중"]
    end
    GMAIL -->|"parse"| NOTION
    GMAIL -.->|"historyId 증분 동기화<br/>새 메일만 처리"| GMAIL
    NOTION -.->|"제목 기반 중복 방지<br/>동일 논문 재저장 차단"| NOTION
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

```mermaid
flowchart LR
    ROOT["get-ASAP/"]
    ROOT --> M["main.py<br/>파이프라인 오케스트레이터 (진입점)"]
    ROOT --> AU["auth.py<br/>Gmail OAuth 2.0 인증"]
    ROOT --> GC["gmail_client.py<br/>필터링, 증분동기화, 라벨"]
    ROOT --> NA["notion_auth.py<br/>Notion API 인증"]
    ROOT --> NC["notion_client_mod.py<br/>DB CRUD, 중복방지"]
    ROOT --> PR["parser_registry.py<br/>파서 자동 디스커버리"]
    ROOT --> MD["models.py<br/>PaperMetadata 데이터 모델"]
    ROOT --> CFG["config.py<br/>환경변수 관리"]
    ROOT --> PJ["publishers.json<br/>sender, journals, doi_prefix"]
    ROOT --> DP["deploy.sh<br/>서버 배포"]
    ROOT --> P["parsers/"]
    P --> P0["base.py — BaseParser ABC"]
    P --> P1["acs.py"]
    P --> P2["wiley.py"]
    P --> P3["elsevier.py"]
    P --> P4["science.py"]
    ROOT --> T["tests/<br/>85개 단위 테스트 (pytest)"]
```

## Pipeline Flow

```mermaid
flowchart TD
    S1["1. Gmail API 인증<br/>token.json 자동 갱신"] --> S2["2. publishers.json 로드<br/>출판사 발신자 목록"]
    S2 --> S3["3. Gmail API ASAP 메일 검색<br/>from: 필터"]
    S3 --> S4["4. historyId 증분 동기화<br/>새 메일만"]
    S4 --> S5["5. 발신자 → 출판사 매칭<br/>→ 해당 파서 실행"]
    S5 --> S6["6. BeautifulSoup4 HTML 파싱<br/>→ 논문 제목 추출"]
    S6 --> S7["7. 저널명 추론<br/>제목/발신자/publishers.json 매핑"]
    S7 --> S8["8. Notion DB 저장<br/>제목 기반 중복 검사"]
    S8 --> S9["9. 처리 메일에 라벨 부여<br/>get-ASAP-processed"]
    S9 --> S10["10. state.json에 historyId 저장<br/>다음 실행 기준점"]
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
| `GMAIL_TOKEN_PATH` | No | OAuth token 경로 (기본: token.json) |
| `CHECK_INTERVAL_HOURS` | No | 실행 간격 (기본: 6) |

*`NOTION_DATABASE_ID` 또는 `NOTION_PARENT_PAGE_ID` 중 하나 필수

## License

MIT
