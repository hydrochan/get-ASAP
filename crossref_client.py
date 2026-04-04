"""CrossRef API를 통한 논문 제목 → DOI 조회 유틸리티.

CrossRef API는 무료이며 API 키가 필요 없는 공개 메타데이터 서비스다.
AI/ML을 사용하지 않는 단순 HTTP 메타데이터 조회 모듈.

참고: https://api.crossref.org/swagger-ui/index.html
"""
import logging
import re
import urllib.request
import urllib.parse
import json

logger = logging.getLogger(__name__)

# CrossRef 공개 API 엔드포인트
CROSSREF_API = "https://api.crossref.org/works"
DOI_RE = re.compile(r'10\.\d{4,9}/[^\s"<>#?&]+')

# Polite Pool 헤더: mailto 포함 시 더 빠른 응답 제공
_MAILTO = "asap-pipeline@example.com"


def lookup_doi(title: str, doi_prefix: str = "", timeout: int = 10) -> str:
    """논문 제목으로 CrossRef API에서 DOI를 조회한다.

    CrossRef의 /works?query.title=TITLE&rows=1 엔드포인트를 사용하여
    가장 관련성 높은 논문의 DOI를 반환한다.

    Args:
        title: 검색할 논문 제목 (영어 제목 권장)
        doi_prefix: 예상되는 DOI prefix (예: "10.1021/"). 있으면 결과 검증에 사용.
        timeout: HTTP 요청 타임아웃 (초)

    Returns:
        DOI 문자열 (예: "10.1021/jacs.5c01234"). 조회 실패 시 빈 문자열.
    """
    if not title or not title.strip():
        return ""
    try:
        # URL 인코딩하여 쿼리 파라미터 구성
        params = urllib.parse.urlencode({
            "query.title": title.strip(),
            "rows": 1,
            "mailto": _MAILTO,
        })
        url = f"{CROSSREF_API}?{params}"

        req = urllib.request.Request(
            url,
            headers={"User-Agent": f"get-ASAP/1.0 (mailto:{_MAILTO})"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))

        items = data.get("message", {}).get("items", [])
        if not items:
            logger.warning("CrossRef 조회 결과 없음: %r", title[:80])
            return ""

        # 첫 번째 결과의 제목과 입력 제목을 비교하여 정확도 검증
        item = items[0]
        doi = item.get("DOI", "")
        if not doi or not doi.startswith("10."):
            logger.warning("CrossRef DOI 형식 이상: %r", doi)
            return ""

        # CrossRef 결과의 제목과 입력 제목의 유사도 검증
        cr_titles = item.get("title", [])
        if cr_titles:
            cr_title = cr_titles[0].lower().strip()
            input_title = title.lower().strip()
            # 간단한 유사도: 입력 제목의 처음 50자가 CrossRef 제목에 포함되는지 확인
            prefix = re.sub(r'[^a-z0-9 ]', '', input_title)[:50].strip()
            cr_clean = re.sub(r'[^a-z0-9 ]', '', cr_title)
            if prefix and prefix not in cr_clean:
                logger.warning(
                    "CrossRef 제목 불일치 - 입력: %r / CrossRef: %r / DOI: %s",
                    title[:60], cr_titles[0][:60], doi
                )
                return ""

        # DOI prefix 검증: 출판사별 DOI 패턴과 일치하는지 확인
        if doi_prefix and not doi.startswith(doi_prefix):
            logger.warning(
                "CrossRef DOI prefix 불일치 - 예상: %s / 실제: %s (제목: %r)",
                doi_prefix, doi[:20], title[:60]
            )
            return ""

        return doi

    except Exception as e:
        logger.warning("CrossRef API 조회 실패 (title=%r): %s", title[:80], e)
        return ""
