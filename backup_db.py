#!/usr/bin/env python
"""정본 SQLite의 일일 gzip 백업 (cron 하루 1회). data/backups/asap-YYYYMMDD-HHMMSS.db.gz, 최근 14개 보관.

SQLite backup API로 일관된 사본을 만든 뒤(store.backup) gzip으로 압축하고, 완성된 파일만 최종 이름으로
os.replace 한다(동기화 API의 /backup/latest가 쓰다 만 파일을 내려보내지 않도록).
VM 밖 사본은 KIST PC의 paper_autodown이 이 파일을 매일 받아 간다.

  python backup_db.py                          # 백업 생성 + 오래된 것 정리
  python backup_db.py --keep 14                # 보관 개수
  python backup_db.py --restore FILE.db.gz --to PATH   # 복원 (PATH가 이미 있으면 거부)
"""
import argparse
import contextlib
import datetime
import glob
import gzip
import logging
import os
import re
import shutil
import sqlite3
import sys

import store

logger = logging.getLogger(__name__)

KEEP_DEFAULT = 14
_NAME_RE = re.compile(r"^asap-\d{8}-\d{6}\.db\.gz$")


def prune(dest_dir: str, keep: int = KEEP_DEFAULT) -> list[str]:
    """이름(타임스탬프) 순으로 최신 keep개만 남기고 지운다. 지운 파일명 목록을 반환."""
    names = sorted(n for n in (os.path.basename(p) for p in glob.glob(os.path.join(dest_dir, "asap-*.db.gz")))
                   if _NAME_RE.match(n))
    removed = names[:-keep] if keep > 0 else names
    for name in removed:
        os.remove(os.path.join(dest_dir, name))
    return removed


def make_backup(db_path: str | None = None, dest_dir: str | None = None, keep: int = KEEP_DEFAULT,
                now: datetime.datetime | None = None) -> str:
    """백업 파일을 만들고 최종 경로를 반환한다."""
    db_path = db_path or store.db_path()
    dest_dir = dest_dir or store.backup_dir(db_path)
    os.makedirs(dest_dir, exist_ok=True)
    stamp = (now or datetime.datetime.now(datetime.timezone.utc)).strftime("%Y%m%d-%H%M%S")
    final = os.path.join(dest_dir, f"asap-{stamp}.db.gz")
    raw = os.path.join(dest_dir, f".asap-{stamp}.{os.getpid()}.db.tmp")
    gz_tmp = f"{final}.{os.getpid()}.tmp"
    conn = store.connect(db_path)
    try:
        store.backup(raw, conn)
        with open(raw, "rb") as src, gzip.open(gz_tmp, "wb", compresslevel=6) as dst:
            shutil.copyfileobj(src, dst, 1024 * 1024)
        os.replace(gz_tmp, final)
    finally:
        conn.close()
        for leftover in (raw, gz_tmp):
            with contextlib.suppress(OSError):
                os.remove(leftover)
    removed = prune(dest_dir, keep)
    logger.info("백업 완료: %s (정리 %d개)", final, len(removed))
    return final


def restore_backup(gz_path: str, dest_path: str) -> str:
    """gzip 백업을 dest_path로 복원한다. 무결성 검사를 통과한 뒤에만 os.replace 하며,
    이미 파일이 있으면 덮어쓰지 않고 거부한다(정본을 실수로 덮는 사고 방지).
    복원본의 meta.db_epoch 는 새로 발급한다(클라이언트가 서버가 되돌려졌음을 알아채도록)."""
    if os.path.exists(dest_path):
        raise FileExistsError(f"복원 대상이 이미 있습니다: {dest_path}")
    os.makedirs(os.path.dirname(os.path.abspath(dest_path)), exist_ok=True)
    tmp = f"{dest_path}.{os.getpid()}.restore.tmp"
    try:
        with gzip.open(gz_path, "rb") as src, open(tmp, "wb") as dst:
            shutil.copyfileobj(src, dst, 1024 * 1024)
        check = sqlite3.connect(tmp)
        try:
            ok = check.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            version = check.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
            if ok and version is not None:
                # 복원본은 seq 번호가 이미 클라이언트가 지나온 값일 수 있다 - 새 db_epoch 를 발급해
                # 클라이언트가 커서를 0으로 되돌리게 한다(교체 전에 임시 파일에서 끝내 원자적으로 반영).
                check.execute(
                    "INSERT INTO meta(key, value) VALUES('db_epoch', ?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (store._new_epoch(),),
                )
                check.commit()
        finally:
            check.close()
        if not ok or version is None:
            raise RuntimeError("백업 파일이 손상되었거나 get-ASAP DB가 아닙니다.")
        os.replace(tmp, dest_path)
    finally:
        with contextlib.suppress(OSError):
            os.remove(tmp)
    return dest_path


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="get-ASAP SQLite gzip backup")
    parser.add_argument("--keep", type=int, default=KEEP_DEFAULT, help="보관 개수 (기본 14)")
    parser.add_argument("--dest-dir", help="백업 폴더 (기본: DB 옆 backups/)")
    parser.add_argument("--restore", metavar="FILE.db.gz", help="이 백업을 복원")
    parser.add_argument("--to", metavar="PATH", help="--restore 대상 DB 경로")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    try:
        if args.restore:
            if not args.to:
                print("--restore에는 --to PATH가 필요합니다.", file=sys.stderr)
                return 2
            print(f"복원 완료: {restore_backup(args.restore, args.to)}")
        else:
            print(f"백업 완료: {make_backup(dest_dir=args.dest_dir, keep=args.keep)}")
    except Exception as e:
        print(f"실패: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
