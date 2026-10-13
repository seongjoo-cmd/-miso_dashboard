# miso internet sales 통화 대시보드

아웃바운드 상담원의 녹취 파일을 통화 기록으로 모아 보여 주는 사내 대시보드. 사용자 안내·기능 설명은 README.md 참고.

## 구조
- 백엔드: Python 표준 라이브러리만(`http.server`). `cti/app.py` 가 라우팅 전부, 집계는 `cti/dashboard.py`, 적재는 `cti/ingest.py`.
- DB: 로컬은 SQLite(`data/cti.db`), Vercel 은 Postgres(`DATABASE_URL`). SQL 은 SQLite 문법으로 쓰고 `cti/db.py` 의 `_pg_sql` 이 변환한다. 새 열은 `SCHEMA` 와 `_ADDED_COLUMNS` 둘 다에 추가(기존 DB 마이그레이션).
- 프런트: 빌드 없는 바닐라 JS `static/app.js` 한 파일 + `static/app.css`. Chart.js 는 `static/vendor` 에 내장.
- 녹취 출처 두 가지(`CTI_SOURCE`): `folder` = 사무실 PC 동기화 폴더를 직접 읽음(로컬 기본), `drive` = Google Apps Script(`cti/drive_sync.gs`)가 드라이브 목록을 `/api/drive/push` 로 보냄(Vercel 기본).
- `drive_sync.gs` 를 고치면 사용자가 설정 > 스크립트 복사로 script.google.com 에 다시 붙여넣어야 반영된다. 예전 스크립트와도 동작하게 서버를 짤 것.

## 명령
- 실행: `python -m scripts.seed_demo --reset` 후 `python -m cti.app` → http://127.0.0.1:8080 (admin / admin1234). 드라이브 화면을 보려면 `CTI_SOURCE=drive`.
- 테스트: `python -m unittest discover -s tests -t .` (Postgres 테스트는 DB 없으면 건너뜀)

## 배포
- GitHub 에 push 하면 Vercel 이 자동 배포. `main` 과 `claude/dashboard-menu1` 을 같은 커밋으로 맞춰 둘 다 push 한다(`git push origin main main:claude/dashboard-menu1`).
- 배포 확인: `https://api.github.com/repos/seongjoo-cmd/-miso_dashboard/commits/<sha>/statuses` 의 Vercel 상태 2건이 success.
- 화면을 고치면 `static/index.html` 의 `app.js?v=` / `app.css?v=` 버전을 올린다(브라우저 캐시).
- 사용자가 "배포해" 라고 할 때만 커밋·push 한다.

## 정해진 규칙(사용자 결정)
- 처리 건수·평균 통화시간(ATT)·일별 처리건수 표는 짧은 통화까지 **전체 통화**를 센다(2026-10-13 결정). `min_call_sec` 는 짧은 통화 비율과 통화 목록 '짧은 통화 빼기' 필터에만 쓴다.
  리뷰에서 0초(안 받은) 통화도 처리로 잡힌다는 지적이 있었고 아직 사용자 결정 대기 중.
- 일별 처리건수 카드에는 설명 문구를 두지 않는다.
- 화면 문구는 토스 스타일 해요체, 짧고 친절하게. 사용자와의 대화도 한국어로 짧게.

## 주의할 점
- 시각은 모두 한국 시간 naive 문자열(`'YYYY-MM-DD HH:MM:SS'`). `cti/__init__.py` 가 Linux 에서만 `TZ=Asia/Seoul` 을 건다 — Windows 에서 `TZ` 를 넣으면 C 런타임이 UTC 로 바꿔 버린다.
- 드라이브 `createdTime` 은 UTC → `ingest.local_time()` 으로 변환해 `calls.uploaded_at` 에 저장. 업로드 현황은 `cti/uploads.py`.
- 슬랙 알림(`cti/slack.py`): 봇 토큰은 환경 변수 `SLACK_BOT_TOKEN` 우선, 없으면 설정 화면에서 DB 에 저장. 토큰은 API 응답·저장소에 절대 넣지 않는다. 받는 곳은 이메일(DM)·U…·C…·#채널.
- 고객 전화번호 원문은 저장하지 않는다(HMAC + 마스킹). 로그·화면에도 가운데를 가린다.
- Windows 의 curl 은 한글 본문을 CP949 로 보낸다 — 한글 JSON 으로 API 를 시험할 때는 Python 으로 보낼 것.
- 로컬 서버는 index.html 에 캐시 헤더가 없어 브라우저가 예전 화면을 보여 줄 수 있다(`/?r=1` 로 새로 받기).
