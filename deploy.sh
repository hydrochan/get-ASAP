#!/usr/bin/env bash
# get-ASAP 서버 배포 스크립트 (per D-07, D-09)
# 오라클 클라우드 Ubuntu에서 git clone 후 이 스크립트 한 번으로 환경 구성
#
# 사용법:
#   bash deploy.sh

set -euo pipefail

# 스크립트 위치 기준 절대 경로 설정
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "=== [1/7] Python 버전 확인 ==="
python3 --version
# Python 3.11+ 권장 (Notion SDK, typing 호환)
PYTHON_MINOR=$(python3 -c "import sys; print(sys.version_info.minor)")
if [ "${PYTHON_MINOR}" -lt 11 ]; then
    echo "[경고] Python 3.11 이상 권장. 현재 버전으로 계속 진행합니다."
fi

echo ""
echo "=== [2/7] 가상환경 생성 ==="
if [ -d "${SCRIPT_DIR}/.venv" ]; then
    echo ".venv 이미 존재 — 스킵"
else
    python3 -m venv "${SCRIPT_DIR}/.venv"
    echo ".venv 생성 완료"
fi

echo ""
echo "=== [3/7] 패키지 설치 ==="
# pip install: venv pip으로 requirements.txt 설치 (pip install -r 패턴)
"${SCRIPT_DIR}/.venv/bin/pip" install --upgrade pip -q
"${SCRIPT_DIR}/.venv/bin/pip" install -r "${SCRIPT_DIR}/requirements.txt"
echo "패키지 설치 완료"

echo ""
echo "=== [4/7] logs 디렉토리 생성 ==="
mkdir -p "${SCRIPT_DIR}/logs"
echo "logs/ 디렉토리 준비 완료"

echo ""
echo "=== [5/7] 환경 설정 파일 확인 ==="
if [ ! -f "${SCRIPT_DIR}/.env" ]; then
    echo "[필수] .env 파일이 없습니다."
    echo "  아래 명령으로 .env.example을 복사한 후 실제 값을 입력하세요:"
    echo "  cp ${SCRIPT_DIR}/.env.example ${SCRIPT_DIR}/.env"
    echo "  nano ${SCRIPT_DIR}/.env"
else
    echo ".env 존재 확인"
fi

echo ""
echo "=== [6/7] OAuth 인증 파일 확인 ==="
if [ ! -f "${SCRIPT_DIR}/token.json" ]; then
    echo "[필수] token.json 파일이 없습니다."
    echo "  로컬에서 인증 완료 후 SCP로 전송하세요 (per D-08):"
    echo "  scp token.json ubuntu@<서버IP>:${SCRIPT_DIR}/token.json"
fi
if [ ! -f "${SCRIPT_DIR}/credentials.json" ]; then
    echo "[필수] credentials.json 파일이 없습니다."
    echo "  Google Cloud Console에서 다운로드 후 SCP로 전송하세요:"
    echo "  scp credentials.json ubuntu@<서버IP>:${SCRIPT_DIR}/credentials.json"
fi
if [ -f "${SCRIPT_DIR}/token.json" ] && [ -f "${SCRIPT_DIR}/credentials.json" ]; then
    echo "token.json, credentials.json 존재 확인"
fi

echo ""
echo "=== [7/7] 다음 단계 안내 ==="

echo ""
echo "--- 테스트 실행 ---"
echo "  ${SCRIPT_DIR}/.venv/bin/python -m pytest tests/ -v"

echo ""
echo "--- 드라이런 (Notion 저장 없이 파싱 결과만 확인) ---"
echo "  ${SCRIPT_DIR}/.venv/bin/python main.py --dry-run --verbose"

echo ""
echo "--- crontab 설정 (매 6시간 자동 실행, per D-04, D-05) ---"
echo "  crontab -e 로 편집기를 열고 아래 줄을 추가하세요:"
echo "  0 */6 * * * cd ${SCRIPT_DIR} && ${SCRIPT_DIR}/.venv/bin/python main.py >> ${SCRIPT_DIR}/logs/cron.log 2>&1"

echo ""
echo "=== deploy.sh 완료 ==="
