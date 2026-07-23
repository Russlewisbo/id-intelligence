"""ClinicalTrials.gov collector (API v2).

Surfaces trials whose record changed inside the lookback window — new
registrations, status changes and posted results all show up as an update.
"""

from __future__ import annotations

from datetime import timedelta

import httpx

from .. import util
from ..net import request_with_retry
from ..record import Record

API = "https://clinicaltrials.gov/api/v2/studies"
MAX_PAGES = 20


def _to_record(study: dict, query_name: str) -> Record | None:
    section = study.get("protocolSection") or {}
    ident = section.get("identificationModule") or {}
    nct = ident.get("nctId")
    title = ident.get("briefTitle") or ident.get("officialTitle")
    if not title or not nct:
        return None

    design = section.get("designModule") or {}
    status = section.get("statusModule") or {}
    sponsor = ((section.get("sponsorCollaboratorsModule") or {}).get("leadSponsor") or {})
    conditions = (section.get("conditionsModule") or {}).get("conditions") or []
    arms = (section.get("armsInterventionsModule") or {}).get("interventions") or []
    summary = (section.get("descriptionModule") or {}).get("briefSummary") or ""

    phases = design.get("phases") or []
    overall = status.get("overallStatus") or ""
    interventions = [
        f"{i.get('type', '').title()}: {i.get('name')}" for i in arms if i.get("name")
    ]

    # Fold the trial's structured metadata into the abstract so the scorer and
    # the summariser see phase, status and interventions as text.
    context = [
        f"Status: {overall}." if overall else "",
        f"Phase: {', '.join(phases)}." if phases else "",
        f"Sponsor: {sponsor.get('name')}." if sponsor.get("name") else "",
        f"Conditions: {', '.join(conditions)}." if conditions else "",
        f"Interventions: {'; '.join(interventions)}." if interventions else "",
        f"Enrollment: {(design.get('enrollmentInfo') or {}).get('count')}."
        if (design.get("enrollmentInfo") or {}).get("count") else "",
    ]
    abstract = " ".join(p for p in context if p)
    if summary:
        abstract = f"{abstract} {util.strip_html(summary)}".strip()

    updated = util.parse_date(
        (status.get("lastUpdatePostDateStruct") or {}).get("date")
    )

    pub_types = ["Clinical Trial"] + [f"Phase {p.replace('PHASE', '')}" for p in phases]

    return Record(
        title=title,
        source=f"ClinicalTrials.gov: {query_name}",
        kind="trial",
        nct=nct,
        abstract=abstract or None,
        journal="ClinicalTrials.gov",
        url=f"https://clinicaltrials.gov/study/{nct}",
        published=updated,
        pub_types=pub_types,
    )


def _run_query(client: httpx.Client, query: dict, since: str) -> tuple[list[Record], str | None]:
    params = {
        "query.term": query["term"],
        "filter.advanced": f"AREA[LastUpdatePostDate]RANGE[{since},MAX]",
        "pageSize": 100,
        "countTotal": "true",
    }
    if query.get("status"):
        params["filter.overallStatus"] = "|".join(query["status"])

    records: list[Record] = []
    token = None
    try:
        for _ in range(MAX_PAGES):
            if token:
                params["pageToken"] = token
            resp = request_with_retry(client, "GET", API, params=params)
            payload = resp.json()
            for study in payload.get("studies") or []:
                record = _to_record(study, query["name"])
                if record and record.is_usable():
                    records.append(record)
            token = payload.get("nextPageToken")
            if not token:
                break
    except Exception as exc:
        return records, f"{type(exc).__name__}: {exc}"

    return records, None


def collect_trials(cfg, db) -> list[Record]:
    conf = cfg.settings.trials
    if not conf.get("enabled", True):
        return []
    queries = conf.get("queries") or []
    if not queries:
        return []

    since = (util.today() - timedelta(days=cfg.settings.lookback_days)).isoformat()
    when = util.now_iso()
    out: list[Record] = []
    headers = {"User-Agent": cfg.settings.user_agent}

    with httpx.Client(timeout=cfg.settings.fetch_timeout, headers=headers,
                      follow_redirects=True) as client:
        for query in queries:
            if query.get("enabled") is False:
                continue
            key = f"trials:{query['name']}"
            records, error = _run_query(client, query, since)
            if error:
                db.record_fetch_error(key, when, error)
            else:
                db.record_fetch_ok(key, None, None, when, len(records))
            out.extend(records)

    return out
