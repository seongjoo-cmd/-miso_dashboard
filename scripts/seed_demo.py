"""데모 데이터 생성(개발용).

- 과거 통화: 이미 적재·백업이 끝난 것으로 보고 DB에 직접 기록
- 오늘 통화 일부: 실제 녹취 파일(WAV)을 SYNC_ROOT/<에이전트 이름>/ 에 만들고 적재 과정을 그대로 거친다
- 사용자: admin / admin1234(관리자), viewer / viewer1234(조회)

사용: python -m scripts.seed_demo [--reset]
"""
from __future__ import annotations

import argparse
import shutil

from cti import demo
from cti.app import Config
from cti.db import connect


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reset", action="store_true", help="기존 DB와 동기화·백업 폴더를 지우고 다시 생성")
    ap.add_argument("--days", type=int, default=70)
    args = ap.parse_args()

    cfg = Config.from_env()
    cfg.db_path.parent.mkdir(parents=True, exist_ok=True)
    if args.reset and not cfg.db_url:
        cfg.db_path.unlink(missing_ok=True)
        shutil.rmtree(cfg.sync_root, ignore_errors=True)
        shutil.rmtree(cfg.backup_root, ignore_errors=True)
    conn = connect(cfg.db_target)
    r = demo.seed(conn, cfg.sync_root, args.days)
    demo.create_demo_users(conn)
    print(f"에이전트 {r['agents']}명, 통화 {r['calls']}건(녹취 파일 {r['files']}개 포함)")
    print(f"DB: {cfg.db_path}  녹취 폴더: {cfg.sync_root}")
    print("로그인: admin / admin1234 (관리자), viewer / viewer1234 (조회)")


if __name__ == "__main__":
    main()
