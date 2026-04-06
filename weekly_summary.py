"""주간 논문 서머리 생성 + Notion 기록.

매주 1회 cron으로 실행하여 지난 7일간의 논문 통계를 Notion 페이지에 기록.

사용법:
  python weekly_summary.py              # Notion에 서머리 기록
  python weekly_summary.py --dry-run    # 콘솔 출력만
  python weekly_summary.py --days 14    # 지난 14일 분석

cron 예시 (매주 월요일 09:00):
  0 9 * * 1 cd ~/get-ASAP && .venv/bin/python weekly_summary.py >> logs/weekly.log 2>&1
"""
import argparse
import csv
import logging
import os
import sys
from collections import Counter
from datetime import date, timedelta

import config
from notion_auth import get_notion_client

logger = logging.getLogger(__name__)

CACHE_DIR = os.path.join(os.path.dirname(__file__), "cache")

# 학술 불용어 (dashboard/index.html과 동기화)
STOPWORDS = {
    'the','and','for','are','but','not','you','all','any','her','was','his',
    'how','its','let','our','out','own','say','she','too','who','oil','old',
    'had','has','him','did','get','got','man','new','now','way','may',
    'day','been','from','have','into','more','most','only','over','such',
    'than','them','then','they','this','that','what','when','will','with',
    'each','make','like','long','look','many','some','time','very','your',
    'about','could','other','their','there','these','those','which','would',
    'after','being','between','both','during','here','where','does','through',
    'study','using','based','effect','effects','analysis','novel','high',
    'performance','enhanced','efficient','properties','via','towards','toward',
    'approach','method','methods','two','one','three','first','role','low',
    'improved','recent','large','small','different','various','general',
    'selective','driven','induced','mediated','assisted','controlled','strategy',
    'highly','simple','facile','rapid','direct','green','review','advances',
    'progress','perspective','insight','insights','investigation','evaluation',
    'design','development','application','applications','preparation','synthesis',
    'characterization','fabrication','co','de','non','re','pre','post','ex',
    'multi','sub','use','used','paper','research','results','show','data',
    'system','systems','type','model','total','state','process','part','case',
    'well','also','however','within','without','since','still','found',
    'made','good','much','even','just','can','able','under','upon',
}


def _tokenize(title: str) -> list[str]:
    """제목을 토큰화하여 유의미한 단어만 반환."""
    import re
    title = title.lower()
    title = re.sub(r'[^a-z0-9\s-]', '', title)
    title = title.replace('-', ' ')
    return [w for w in title.split() if len(w) > 2 and w not in STOPWORDS]


def _extract_bigrams(title: str) -> list[str]:
    """제목에서 바이그램 추출."""
    tokens = _tokenize(title)
    return [f"{tokens[i]} {tokens[i+1]}" for i in range(len(tokens)-1)]


def load_recent_papers(days: int = 7) -> list[dict]:
    """최근 N일간의 논문을 캐시 CSV에서 로드."""
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    papers = []

    # 관련 월 CSV 파일들 찾기
    if not os.path.isdir(CACHE_DIR):
        return papers

    for fname in sorted(os.listdir(CACHE_DIR)):
        if not fname.startswith("papers_") or not fname.endswith(".csv"):
            continue
        path = os.path.join(CACHE_DIR, fname)
        with open(path, encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for row in reader:
                if (row.get("date", "") or "") >= cutoff:
                    papers.append(row)

    return papers


def generate_summary(papers: list[dict], days: int = 7) -> dict:
    """논문 목록에서 주간 서머리 통계 생성."""
    total = len(papers)

    # 저널 빈도
    journal_freq = Counter(p["journal"] for p in papers if p.get("journal"))
    top_journals = journal_freq.most_common(10)

    # 유니그램 키워드 (문서 빈도)
    kw_freq = Counter()
    for p in papers:
        tokens = set(_tokenize(p.get("title", "")))
        kw_freq.update(tokens)
    top_keywords = kw_freq.most_common(15)

    # 바이그램 키워드
    bg_freq = Counter()
    for p in papers:
        bigrams = set(_extract_bigrams(p.get("title", "")))
        bg_freq.update(bigrams)
    # 최소 2회 이상
    top_bigrams = [(bg, c) for bg, c in bg_freq.most_common(30) if c >= 2][:10]

    # 일별 논문 수
    daily = Counter((p.get("date", "") or "")[:10] for p in papers)
    daily.pop("", None)
    busiest_day = daily.most_common(1)[0] if daily else ("N/A", 0)

    # AI 관심 논문
    interest = [p for p in papers if p.get("status") and p["status"] != "대기중"]

    return {
        "total": total,
        "days": days,
        "top_journals": top_journals,
        "top_keywords": top_keywords,
        "top_bigrams": top_bigrams,
        "busiest_day": busiest_day,
        "interest_count": len(interest),
        "journal_count": len(journal_freq),
        "daily_avg": round(total / max(1, len(daily)), 1),
    }


def format_summary_text(summary: dict) -> str:
    """서머리를 텍스트로 포맷."""
    today = date.today().isoformat()
    lines = [
        f"📊 get-ASAP Weekly Summary ({today})",
        f"Period: Last {summary['days']} days",
        "",
        f"📈 Overview",
        f"  Total papers: {summary['total']}",
        f"  Active journals: {summary['journal_count']}",
        f"  Daily average: {summary['daily_avg']}",
        f"  Busiest day: {summary['busiest_day'][0]} ({summary['busiest_day'][1]} papers)",
        f"  AI interest: {summary['interest_count']} papers",
        "",
        "🔑 Top Keywords (Unigram)",
    ]
    for kw, count in summary["top_keywords"]:
        lines.append(f"  {kw}: {count}")

    if summary["top_bigrams"]:
        lines.append("")
        lines.append("🔗 Top Keywords (Bigram)")
        for bg, count in summary["top_bigrams"]:
            lines.append(f"  {bg}: {count}")

    lines.append("")
    lines.append("📚 Top Journals")
    for j, count in summary["top_journals"]:
        lines.append(f"  {j}: {count}")

    return "\n".join(lines)


WEEKLY_PAGE_TITLE = "get-ASAP Weekly Summary"


def _find_weekly_page(client, parent_page_id: str) -> str | None:
    """parent 하위에서 'get-ASAP Weekly Summary' 페이지를 찾아 ID 반환."""
    cursor = None
    while True:
        kwargs = {"block_id": parent_page_id, "page_size": 100}
        if cursor:
            kwargs["start_cursor"] = cursor
        result = client.blocks.children.list(**kwargs)
        for block in result["results"]:
            if block["type"] != "child_page":
                continue
            title = block.get("child_page", {}).get("title", "")
            if title == WEEKLY_PAGE_TITLE:
                return block["id"]
        if not result.get("has_more"):
            break
        cursor = result.get("next_cursor")
    return None


def _create_weekly_page(client, parent_page_id: str) -> str:
    """'get-ASAP Weekly Summary' 전용 페이지 생성."""
    result = client.pages.create(
        parent={"page_id": parent_page_id},
        properties={
            "title": [{"type": "text", "text": {"content": WEEKLY_PAGE_TITLE}}]
        },
    )
    page_id = result["id"]
    logger.info("Weekly Summary 페이지 생성: %s", page_id)
    return page_id


def _build_summary_blocks(summary: dict) -> list[dict]:
    """서머리를 Notion 블록 배열로 변환."""
    today = date.today().isoformat()
    blocks = []

    # 제목 (H2)
    blocks.append({
        "type": "heading_2",
        "heading_2": {"rich_text": [{"type": "text", "text": {"content": f"Weekly Summary — {today}"}}]}
    })

    # 개요
    overview = (
        f"Total: {summary['total']} papers | "
        f"Journals: {summary['journal_count']} | "
        f"Daily avg: {summary['daily_avg']} | "
        f"Busiest: {summary['busiest_day'][0]} ({summary['busiest_day'][1]}건) | "
        f"AI interest: {summary['interest_count']}건"
    )
    blocks.append({
        "type": "paragraph",
        "paragraph": {"rich_text": [{"type": "text", "text": {"content": overview}}]}
    })

    # 구분선
    blocks.append({"type": "divider", "divider": {}})

    # Top 키워드 (유니그램)
    blocks.append({
        "type": "heading_3",
        "heading_3": {"rich_text": [{"type": "text", "text": {"content": "Top Keywords (Unigram)"}}]}
    })
    kw_text = "  |  ".join(f"{kw} ({c})" for kw, c in summary["top_keywords"][:10])
    blocks.append({
        "type": "paragraph",
        "paragraph": {"rich_text": [{"type": "text", "text": {"content": kw_text}}]}
    })

    # Top 키워드 (바이그램)
    if summary["top_bigrams"]:
        blocks.append({
            "type": "heading_3",
            "heading_3": {"rich_text": [{"type": "text", "text": {"content": "Top Keywords (Bigram)"}}]}
        })
        bg_text = "  |  ".join(f"{bg} ({c})" for bg, c in summary["top_bigrams"][:10])
        blocks.append({
            "type": "paragraph",
            "paragraph": {"rich_text": [{"type": "text", "text": {"content": bg_text}}]}
        })

    # Top 저널
    blocks.append({
        "type": "heading_3",
        "heading_3": {"rich_text": [{"type": "text", "text": {"content": "Top Journals"}}]}
    })
    for j, count in summary["top_journals"][:10]:
        blocks.append({
            "type": "bulleted_list_item",
            "bulleted_list_item": {"rich_text": [{"type": "text", "text": {"content": f"{j}: {count}건"}}]}
        })

    # 마지막 구분선
    blocks.append({"type": "divider", "divider": {}})

    return blocks


def post_to_notion(summary: dict) -> str | None:
    """'get-ASAP Weekly Summary' 전용 페이지에 주간 서머리 블록 추가.

    페이지가 없으면 자동 생성. 매주 서머리가 누적 기록됨.
    Returns: 페이지 ID 또는 None
    """
    parent_page_id = config.NOTION_PARENT_PAGE_ID
    if not parent_page_id:
        logger.error("NOTION_PARENT_PAGE_ID 없음")
        return None

    client = get_notion_client()

    # 전용 페이지 찾기/생성
    page_id = _find_weekly_page(client, parent_page_id)
    if not page_id:
        page_id = _create_weekly_page(client, parent_page_id)

    # 서머리 블록 추가 (페이지 맨 앞에 — 최신이 위)
    blocks = _build_summary_blocks(summary)
    client.blocks.children.append(block_id=page_id, children=blocks)

    logger.info("Weekly Summary 기록 완료: %s", page_id)
    return page_id


def main():
    parser = argparse.ArgumentParser(description="get-ASAP Weekly Summary")
    parser.add_argument("--days", type=int, default=7, help="분석 기간 (기본: 7일)")
    parser.add_argument("--dry-run", action="store_true", help="콘솔 출력만, Notion 기록 안함")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    papers = load_recent_papers(args.days)
    if not papers:
        logger.info("최근 %d일간 논문 없음", args.days)
        return

    summary = generate_summary(papers, args.days)
    text = format_summary_text(summary)

    if args.dry_run:
        sys.stdout.reconfigure(encoding="utf-8")
        print(text)
        return

    post_to_notion(summary)
    logger.info("주간 서머리 완료: %d건 분석", summary["total"])


if __name__ == "__main__":
    main()
