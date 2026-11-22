"""Real job listings from free public job boards.

Each board asks to be credited and linked to, so every job keeps its source and
its original URL, and applying always goes to that URL. CareerKit charges for
the work it does on listings (searching four boards at once, cleaning, ranking
and matching), not for the listings themselves.
"""

from __future__ import annotations

import hashlib
import html
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass

import httpx

UA = {"user-agent": "OryneJobAgent/0.1 (+https://oryne.org)"}


@dataclass
class Job:
    id: str
    title: str
    company: str
    location: str
    url: str
    source: str
    source_url: str
    posted: str
    salary: str
    tags: list[str]
    description: str

    def to_dict(self) -> dict:
        return asdict(self)


SOURCES = {
    "remotive": {"name": "Remotive", "home": "https://remotive.com", "about": "Remote jobs, worldwide"},
    "jobicy": {"name": "Jobicy", "home": "https://jobicy.com", "about": "Remote jobs, worldwide"},
    "remoteok": {"name": "Remote OK", "home": "https://remoteok.com", "about": "Remote jobs, tech-heavy"},
    "arbeitnow": {"name": "Arbeitnow", "home": "https://www.arbeitnow.com", "about": "Jobs in Europe, many in Germany"},
}


def _text(raw: str | None, limit: int = 4000) -> str:
    """HTML to plain text, trimmed."""
    text = re.sub(r"<(br|/p|/li|/h\d)[^>]*>", "\n", raw or "", flags=re.I)
    text = html.unescape(re.sub(r"<[^>]+>", " ", text))
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n", text).strip()
    return text[:limit]


def _id(source: str, key: str) -> str:
    return f"{source}-{hashlib.sha1(key.encode()).hexdigest()[:10]}"


def _remotive(http: httpx.Client, query: str) -> list[Job]:
    data = http.get("https://remotive.com/api/remote-jobs", params={"search": query, "limit": 30}).json()
    return [
        Job(
            id=_id("remotive", str(j.get("id"))),
            title=j.get("title") or "",
            company=j.get("company_name") or "",
            location=j.get("candidate_required_location") or "Remote",
            url=j.get("url") or "",
            source="Remotive",
            source_url="https://remotive.com",
            posted=(j.get("publication_date") or "")[:10],
            salary=j.get("salary") or "",
            tags=list(j.get("tags") or [])[:8],
            description=_text(j.get("description")),
        )
        for j in data.get("jobs", [])
    ]


def _jobicy(http: httpx.Client, query: str) -> list[Job]:
    data = http.get("https://jobicy.com/api/v2/remote-jobs", params={"count": 30, "tag": query}).json()
    jobs = []
    for j in data.get("jobs", []) or []:
        salary = ""
        if j.get("salaryMin") and j.get("salaryMax"):
            salary = f"{j.get('salaryCurrency', '')} {j['salaryMin']:,}–{j['salaryMax']:,} {j.get('salaryPeriod', '')}".strip()
        jobs.append(
            Job(
                id=_id("jobicy", str(j.get("id"))),
                title=html.unescape(j.get("jobTitle") or ""),
                company=j.get("companyName") or "",
                location=j.get("jobGeo") or "Remote",
                url=j.get("url") or "",
                source="Jobicy",
                source_url="https://jobicy.com",
                posted=(j.get("pubDate") or "")[:10],
                salary=salary,
                tags=list(j.get("jobIndustry") or [])[:8],
                description=_text(j.get("jobDescription")),
            )
        )
    return jobs


def _remoteok(http: httpx.Client, query: str) -> list[Job]:
    data = http.get("https://remoteok.com/api", headers={**UA, "user-agent": "Mozilla/5.0 " + UA["user-agent"]}).json()
    words = [w for w in query.lower().split() if w]
    jobs = []
    for j in data[1:]:  # the first entry is the API's legal notice
        haystack = " ".join([j.get("position", ""), " ".join(j.get("tags") or []), j.get("description", "")]).lower()
        if words and not all(w in haystack for w in words):
            continue
        salary = f"${j['salary_min']:,}–${j['salary_max']:,}" if j.get("salary_min") and j.get("salary_max") else ""
        jobs.append(
            Job(
                id=_id("remoteok", str(j.get("id"))),
                title=j.get("position") or "",
                company=j.get("company") or "",
                location=j.get("location") or "Remote",
                url=j.get("url") or "",
                source="Remote OK",
                source_url="https://remoteok.com",
                posted=(j.get("date") or "")[:10],
                salary=salary,
                tags=list(j.get("tags") or [])[:8],
                description=_text(j.get("description")),
            )
        )
    return jobs[:30]


def _arbeitnow(http: httpx.Client, query: str) -> list[Job]:
    words = [w for w in query.lower().split() if w]
    jobs = []
    for page in (1, 2, 3):
        data = http.get("https://www.arbeitnow.com/api/job-board-api", params={"page": page}).json()
        for j in data.get("data", []):
            haystack = " ".join([j.get("title", ""), " ".join(j.get("tags") or []), j.get("description", "")]).lower()
            if words and not all(w in haystack for w in words):
                continue
            jobs.append(
                Job(
                    id=_id("arbeitnow", j.get("slug", "")),
                    title=j.get("title") or "",
                    company=j.get("company_name") or "",
                    location=("Remote · " if j.get("remote") else "") + (j.get("location") or ""),
                    url=j.get("url") or "",
                    source="Arbeitnow",
                    source_url="https://www.arbeitnow.com",
                    posted="",
                    salary="",
                    tags=list(j.get("tags") or [])[:8],
                    description=_text(j.get("description")),
                )
            )
    return jobs[:30]


FETCHERS = {"remotive": _remotive, "jobicy": _jobicy, "remoteok": _remoteok, "arbeitnow": _arbeitnow}


def search(query: str, sources: list[str], location: str = "", limit: int = 25) -> tuple[list[Job], dict[str, str]]:
    """Searches the chosen boards at once. Returns the jobs, best first, and each board's outcome."""
    chosen = [s for s in sources if s in FETCHERS] or list(FETCHERS)
    outcome: dict[str, str] = {}
    found: list[Job] = []
    with httpx.Client(timeout=20, headers=UA, follow_redirects=True) as http, ThreadPoolExecutor(len(chosen)) as pool:
        futures = {name: pool.submit(FETCHERS[name], http, query) for name in chosen}
        for name, future in futures.items():
            try:
                jobs = future.result()
                outcome[name] = f"{len(jobs)} jobs"
                found += jobs
            except Exception as error:  # noqa: BLE001 - one board being down shouldn't fail the search
                outcome[name] = f"unavailable ({type(error).__name__})"

    words = [w for w in query.lower().split() if len(w) > 1]
    place = location.lower().strip()

    def rank(job: Job) -> float:
        title = job.title.lower()
        score = sum(3 for w in words if w in title) + sum(1 for w in words if w in job.description.lower())
        if place and (place in job.location.lower() or "worldwide" in job.location.lower() or "anywhere" in job.location.lower()):
            score += 2
        return score + (0.5 if job.salary else 0)

    unique: dict[tuple[str, str], Job] = {}
    for job in found:
        unique.setdefault((job.title.lower().strip(), job.company.lower().strip()), job)
    # Some boards return their latest jobs whatever the query: keep only ones that mention it.
    relevant = [job for job in unique.values() if rank(job) >= 1] if words else list(unique.values())
    ranked = sorted(relevant, key=rank, reverse=True)
    for job in ranked:
        if len(job.location) > 60:
            job.location = job.location[:57].rsplit(",", 1)[0] + ", …"
    return ranked[:limit], outcome
