"""miso internet sales 통화 대시보드."""
import os
import time

# 날짜 집계('오늘', 일별)는 한국 시간 기준. 서버(예: Vercel)가 UTC여도 맞도록 프로세스 시간대를 고정한다.
# Windows 에는 tzset 이 없지만 사무실 PC는 이미 한국 시간이다.
# (AWS Lambda 기반인 Vercel 은 TZ=:UTC 를 미리 넣어 두므로 덮어쓴다)
# Windows 에서는 TZ 를 넣으면 C 런타임이 'Asia/Seoul' 을 못 읽고 UTC 로 바꿔 버리므로 건드리지 않는다.
if hasattr(time, "tzset"):
    os.environ["TZ"] = os.environ.get("CTI_TZ", "Asia/Seoul")
    time.tzset()
