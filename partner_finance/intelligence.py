from __future__ import annotations

from datetime import date
import hashlib
import json
import re
from urllib.parse import urlsplit
import urllib.request

from .schema import utc_now
from .workflow import log_action


def public_link(url):
    parsed = urlsplit(url.strip())
    return parsed.scheme == "https" and bool(parsed.hostname) and not parsed.username and not parsed.password


def merge_updates(project, candidates):
    rows = project.narrative.setdefault("partner_updates", [])
    known = {row["id"] for row in rows}
    count = 0
    for candidate in candidates:
        if not public_link(candidate.get("source_url", "")):
            raise ValueError("출처는 HTTPS URL이어야 합니다.")
        date.fromisoformat(candidate["published_at"])
        identity = hashlib.sha256(candidate["source_url"].strip().encode()).hexdigest()[:24]
        if identity in known:
            continue
        rows.append({**candidate, "id": identity, "status": "검토 대기", "collected_at": utc_now()})
        known.add(identity)
        count += 1
    project.narrative["last_monitor_check"] = utc_now()
    log_action(project, "동향 수집", f"신규 {count}건")
    return count


def review_update(project, identity, approved, reviewer, note):
    if not reviewer.strip() or not note.strip():
        raise ValueError("검토자와 검토의견이 필요합니다.")
    row = next(row for row in project.narrative["partner_updates"] if row["id"] == identity)
    row.update(status="승인" if approved else "제외", reviewer=reviewer.strip(), review_note=note.strip(), reviewed_at=utc_now())
    project.narrative.pop("final", None)
    project.narrative.pop("ai_cache", None)
    if project.status == "검토 완료":
        project.status = "재검토 필요"
    log_action(project, "동향 검토", f"{identity}: {row['status']} / {reviewer} / {note}")


def parse_sec_updates(payload, cik, start, end):
    recent = payload.get("filings", {}).get("recent", {})
    rows = []
    for i, filed in enumerate(recent.get("filingDate", [])):
        if not start.isoformat() <= filed <= end.isoformat():
            continue
        form = recent["form"][i]
        if form not in {"10-K", "10-K/A", "10-Q", "10-Q/A", "8-K", "8-K/A", "20-F", "6-K"}:
            continue
        accession = recent["accessionNumber"][i].replace("-", "")
        document = recent["primaryDocument"][i]
        if not re.fullmatch(r"\d+", accession) or not re.fullmatch(r"[A-Za-z0-9_.-]+", document):
            continue
        rows.append({"title": f"{payload.get('name', '')} {form} 제출", "published_at": filed,
                     "event_date": "", "source_name": "SEC EDGAR", "topic": "기업 공시", "severity": "검토",
                     "summary": "신규 공시 제출 확인. 내용·위험 판단은 원문 검토 전 미확정입니다.",
                     "source_url": f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession}/{document}"})
    return rows


def collect_sec_updates(cik, start, end, user_agent):
    if not re.fullmatch(r"\d{1,10}", str(cik)):
        raise ValueError("SEC CIK가 필요합니다.")
    if start > end:
        raise ValueError("검색 시작일이 종료일보다 늦습니다.")
    if not user_agent or "example.com" in user_agent:
        raise ValueError("배포 설정 SEC_USER_AGENT에 실제 조직명과 연락 이메일을 설정하십시오.")
    request = urllib.request.Request(f"https://data.sec.gov/submissions/CIK{str(cik).zfill(10)}.json", headers={"User-Agent": user_agent, "Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=20) as response:
        body = response.read(8_000_001)
    if len(body) > 8_000_000:
        raise ValueError("공시 목록 응답 크기 제한 초과")
    return parse_sec_updates(json.loads(body), cik, start, end)
