@echo off
rem ============================================================
rem  miso internet sales 통화 대시보드 설정 - 경로를 바꾼 뒤 저장하고 start.bat 실행
rem ============================================================

rem 녹취 공유 폴더 - Google Drive 데스크톱 앱에서 보이는 경로
rem 이 폴더 바로 아래에 에이전트 이름 폴더들이 있어야 합니다.
rem 예: G:\공유 드라이브\녹취    또는    G:\내 드라이브\녹취
set "CTI_SYNC_ROOT=G:\공유 드라이브\녹취"

rem 백업 zip 을 저장할 곳 - 이 컴퓨터의 로컬 디스크 권장
set "CTI_BACKUP_ROOT=%~dp0data\backup"

rem 접속 허용 범위
rem   0.0.0.0   = 휴대폰, 다른 컴퓨터에서도 접속 가능 - 처음 실행 때 방화벽 창에서 [허용]
rem   127.0.0.1 = 이 컴퓨터에서만
rem 사무실 밖에서 휴대폰으로 보려면 이 컴퓨터와 휴대폰에 Tailscale 설치 - README 참고
set "CTI_HOST=0.0.0.0"
set "CTI_PORT=8080"

rem 녹취 폴더를 자동으로 읽는 주기 - 초
set "CTI_INGEST_INTERVAL=300"
