"""허브스팟 '계약 완료' 고객 가져오기.

777. Internet Sales 파이프라인에서 '계약 완료(Closed won)' 단계인 딜 → 연결된 연락처의 전화번호 →
통화 목록과 같은 방식으로 HMAC(customer_key) 해서 hubspot_contracts 에 넣는다(번호 원문은 저장하지 않음).
고객 단위: 이 표에 있는 고객의 통화는 모두 '계약완료'로 보인다(calls.CONTRACTED_SQL).

토큰은 환경 변수 HUBSPOT_TOKEN 에서만 읽는다(비공개 앱, crm.objects.contacts.read · crm.objects.deals.read).
토큰은 DB·API 응답·로그에 넣지 않는다.

가져오기는 이어서 할 수 있다: 딜을 '마지막 수정 시각' 순으로 훑고, 시간이 다 되면 위치(state)를 저장해 다음 회차에 이어간다.
처음 한 번 다 가져온 뒤로는 지난 회차 이후 바뀐 딜만 본다.
"""
from __future__ import annotations

import json
import os
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime

from .db import get_setting, set_setting
from .ingest import customer_key, hash_key, parse_phone

API = "https://api.hubapi.com"
PIPELINE_ID = "17405288"      # 777. Internet Sales
WON_STAGE_ID = "44106420"     # 계약 완료(Closed won)
PAGE = 100
ASSOC_CHUNK = 500             # v4 연결 일괄 조회는 한 번에 1000개까지
CONTACT_CHUNK = 100           # 연락처 일괄 조회 한 번에 100개
RESTART_AFTER = 9000          # 검색은 10,000건 뒤로는 못 넘기므로 그 전에 '수정 시각'을 기준으로 다시 시작
OVERLAP_MS = 60_000           # 다음 회차는 1분 겹쳐 읽어 놓치는 딜이 없게(중복은 표가 거름)
_lock = threading.Lock()


class HubspotError(Exception):
    pass


def token() -> str | None:
    t = os.environ.get("HUBSPOT_TOKEN", "").strip()
    return t or None


def _request(method: str, path: str, body: dict | None = None) -> dict:
    """허브스팟 API 한 번 호출. 429 는 잠깐 쉬고 한 번 더."""
    tok = token()
    if not tok:
        raise HubspotError("HUBSPOT_TOKEN 환경 변수가 없어요.")
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(API + path, data=data, method=method,
                                 headers={"Authorization": f"Bearer {tok}", "Content-Type": "application/json"})
    for attempt in (0, 1):
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                return json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt == 0:
                time.sleep(min(float(e.headers.get("Retry-After") or 1), 3))
                continue
            if e.code in (401, 403):
                raise HubspotError("허브스팟 토큰이 맞지 않거나 권한이 부족해요(연락처·딜 읽기 권한 필요).")
            raise HubspotError(f"허브스팟 요청이 실패했어요 ({e.code}).")
        except (urllib.error.URLError, TimeoutError, OSError):
            raise HubspotError("허브스팟에 연결하지 못했어요.")
    raise HubspotError("허브스팟이 잠시 바빠요. 다음 회차에 이어서 해요.")


def _won_deals(since_ms: int, after: str | None) -> dict:
    return _request("POST", "/crm/v3/objects/deals/search", {
        "filterGroups": [{"filters": [
            {"propertyName": "pipeline", "operator": "EQ", "value": PIPELINE_ID},
            {"propertyName": "dealstage", "operator": "EQ", "value": WON_STAGE_ID},
            {"propertyName": "hs_lastmodifieddate", "operator": "GTE", "value": str(since_ms)},
        ]}],
        "sorts": [{"propertyName": "hs_lastmodifieddate", "direction": "ASCENDING"}],
        "properties": ["hs_lastmodifieddate"], "limit": PAGE, **({"after": after} if after else {}),
    })


def _contact_ids(deal_ids: list[str]) -> dict[str, list[str]]:
    """딜 ID → 연결된 연락처 ID 목록"""
    out: dict[str, list[str]] = {}
    for i in range(0, len(deal_ids), ASSOC_CHUNK):
        res = _request("POST", "/crm/v4/associations/deals/contacts/batch/read",
                       {"inputs": [{"id": d} for d in deal_ids[i:i + ASSOC_CHUNK]]})
        for r in res.get("results", []):
            out[str(r["from"]["id"])] = [str(t["toObjectId"]) for t in r.get("to", [])]
    return out


def _phones(contact_ids: list[str]) -> dict[str, list[str]]:
    """연락처 ID → 전화번호 글자들(phone, mobilephone)"""
    out: dict[str, list[str]] = {}
    for i in range(0, len(contact_ids), CONTACT_CHUNK):
        res = _request("POST", "/crm/v3/objects/contacts/batch/read", {
            "properties": ["phone", "mobilephone"],
            "inputs": [{"id": c} for c in contact_ids[i:i + CONTACT_CHUNK]]})
        for r in res.get("results", []):
            p = r.get("properties") or {}
            out[str(r["id"])] = [v for v in (p.get("phone"), p.get("mobilephone")) if v]
    return out


def _now_ms() -> int:
    return int(time.time() * 1000)


def _ms(value, default: int) -> int:
    """허브스팟 날짜 값(밀리초 숫자 또는 ISO 글자) → 밀리초"""
    try:
        v = str(value)
        return int(v) if v.isdigit() else int(datetime.fromisoformat(v.replace("Z", "+00:00")).timestamp() * 1000)
    except (ValueError, TypeError):
        return default


def _state(conn) -> dict:
    try:
        return json.loads(get_setting(conn, "hubspot_state") or "{}")
    except ValueError:
        return {}


def _save_state(conn, st: dict) -> None:
    set_setting(conn, "hubspot_state", json.dumps(st))


def sync(conn, budget: float = 20.0, full: bool = False) -> dict:
    """계약 완료 딜을 가져와 hubspot_contracts 를 채운다. 시간이 다 되면 위치를 저장하고 멈춘다(done=False)."""
    if not _lock.acquire(blocking=False):
        return {"busy": True, "done": False}
    started = time.time()
    try:
        if full:
            conn.execute("DELETE FROM hubspot_contracts")
            _save_state(conn, {})
        st = _state(conn)
        run_start = st.get("run_start") or _now_ms()
        since, after, seen = int(st.get("since") or 0), st.get("after"), int(st.get("seen") or 0)
        deals = keys = 0
        hk = hash_key(conn)
        try:
            while True:
                page = _won_deals(since, after)
                results = page.get("results", [])
                ids = [str(d["id"]) for d in results]
                assoc = _contact_ids(ids) if ids else {}
                phones = _phones(sorted({c for cs in assoc.values() for c in cs}))
                now = datetime.now().isoformat(sep=" ", timespec="seconds")
                rows = []
                for deal_id in ids:
                    for cid in assoc.get(deal_id, []):
                        for text in phones.get(cid, []):
                            ph = parse_phone(text)
                            if ph:
                                rows.append((customer_key(hk, ph[0]), deal_id, now))
                conn.executemany("INSERT OR IGNORE INTO hubspot_contracts(customer_key, deal_id, synced_at) "
                                 "VALUES (?, ?, ?)", rows)
                deals += len(ids)
                keys += len(rows)
                seen += len(ids)
                nxt = (page.get("paging") or {}).get("next", {}).get("after")
                if not nxt or not results:  # 끝까지 읽음 → 다음 회차는 이번 시작 시각(조금 겹치게)부터
                    _save_state(conn, {"since": max(run_start - OVERLAP_MS, 0)})
                    set_setting(conn, "hubspot_last_ok", datetime.now().isoformat(sep=" ", timespec="seconds"))
                    set_setting(conn, "hubspot_error", "")
                    conn.commit()
                    return {"done": True, "deals": deals, "keys": keys}
                after = nxt
                if seen >= RESTART_AFTER:  # 검색 한도(1만 건) 전에 수정 시각 기준으로 새로 시작
                    since, after, seen = _ms(results[-1].get("properties", {}).get("hs_lastmodifieddate"), since), None, 0
                _save_state(conn, {"since": since, "after": after, "seen": seen, "run_start": run_start})
                conn.commit()
                if time.time() - started > budget:
                    return {"done": False, "deals": deals, "keys": keys}
        except HubspotError as e:
            conn.commit()  # 여기까지 받은 건 남김(위치도 저장돼 있음)
            set_setting(conn, "hubspot_error", str(e))
            conn.commit()
            return {"done": False, "error": str(e), "deals": deals, "keys": keys}
    finally:
        _lock.release()


def maybe_sync(conn, min_interval: int = 600, budget: float = 12.0) -> dict | None:
    """스크립트가 10분마다 들를 때 얹어서 부르는 가벼운 가져오기. 토큰이 없거나 최근에 했으면 건너뜀."""
    if not token():
        return None
    last = get_setting(conn, "hubspot_last_try")
    if last and time.time() - float(last) < min_interval:
        return None
    set_setting(conn, "hubspot_last_try", str(time.time()))
    conn.commit()
    return sync(conn, budget)


def status(conn) -> dict:
    n = conn.execute("SELECT COUNT(*) FROM hubspot_contracts").fetchone()[0]
    return {"connected": bool(token()), "customers": n, "last_ok": get_setting(conn, "hubspot_last_ok") or None,
            "error": get_setting(conn, "hubspot_error") or None,
            "catching_up": bool(token()) and not get_setting(conn, "hubspot_last_ok")}
