"""CareerKit: a paid API for job hunting. Agents pay per call in USDC on Stellar (x402).

Any x402 client can pay it; the job agent in this project pays it from an Oryne
wallet. Unpaid requests get 402 Payment Required with the price; paid ones are
settled through the Oryne facilitator before the handler runs.
"""

from __future__ import annotations

import re
from fastapi import APIRouter, HTTPException, Query, Request
from oryne.merchant.fastapi import paywall
from pydantic import BaseModel, Field

from . import llm, sources
from .config import settings

# What each service costs, in test USDC. The web page and the agent read this too.
SERVICES = {
    "search": {
        "name": "Job search",
        "price": "0.05",
        "path": "/jobs",
        "about": "Searches the job boards you pick at once, removes duplicates and ranks the results",
    },
    "match": {
        "name": "Fit score",
        "price": "0.02",
        "path": "/match",
        "about": "Scores your CV against one job: fit out of 100, strengths, gaps, missing keywords",
    },
    "cover_letter": {
        "name": "Cover letter",
        "price": "0.25",
        "path": "/cover-letter",
        "about": "A cover letter for one job, written only from what's in your CV",
    },
    "application_pack": {
        "name": "Application pack",
        "price": "5",
        "path": "/application-pack",
        "about": "For your best match: CV rewritten for the job, cover letter, likely interview questions and how to answer",
    },
}

router = APIRouter()

if not settings.careerkit_address:
    raise RuntimeError("CAREERKIT_ADDRESS is not set: run python setup_testnet.py first")
pay = paywall(pay_to=settings.careerkit_address, facilitator=settings.facilitator)


class JobIn(BaseModel):
    title: str = Field(max_length=300)
    company: str = Field(default="", max_length=200)
    description: str = Field(default="", max_length=6000)
    tags: list[str] = Field(default_factory=list, max_length=20)
    url: str = Field(default="", max_length=500)


class CvJobIn(BaseModel):
    cv: str = Field(min_length=50, max_length=15000)
    job: JobIn


def _settled(request: Request) -> dict:
    """The payment that let this request through: its Stellar transaction."""
    settlement = getattr(request.state, "oryne_settlement", None) or {}
    return {"transaction": settlement.get("transaction"), "payer": settlement.get("payer")}


@router.get("/")
def catalog() -> dict:
    """Free: what CareerKit sells, for how much, and who it's paid to. Agents read this first."""
    return {
        "name": "CareerKit",
        "about": "Job hunting services for AI agents, paid per call in USDC on Stellar (x402).",
        "payTo": settings.careerkit_address,
        "network": "stellar-testnet",
        "services": [
            {"id": key, **value, "url": f"{settings.careerkit_url}{value['path']}"} for key, value in SERVICES.items()
        ],
        "jobBoards": sources.SOURCES,
    }


@router.get("/jobs")
@pay(SERVICES["search"]["price"], "Job search across job boards")
def jobs(
    request: Request,
    q: str = Query(min_length=2, max_length=100),
    location: str = Query(default="", max_length=60),
    boards: str = Query(default="remotive,jobicy,remoteok,arbeitnow", max_length=100),
) -> dict:
    found, outcome = sources.search(q, boards.split(","), location)
    return {
        "query": q,
        "boards": outcome,
        "jobs": [job.to_dict() for job in found],
        "credit": "Listings from Remotive, Jobicy, Remote OK and Arbeitnow. Apply on the original posting.",
        "payment": _settled(request),
    }


# ---------------------------------------------------------------- matching

# Skills and tools that recur in job ads, across fields. Without a model, a match is how many
# of the ones a job asks for the CV also mentions.
SKILLS = set(
    """
python java javascript typescript go golang rust ruby php kotlin swift c++ c# scala r sql nosql
react vue angular next.js node node.js django flask fastapi spring rails laravel .net graphql rest api apis
postgresql postgres mysql mongodb redis kafka rabbitmq celery elasticsearch airflow spark hadoop dbt snowflake
aws gcp azure docker kubernetes terraform ansible linux ci/cd jenkins github gitlab devops sre microservices
machine-learning ml ai llm nlp pytorch tensorflow pandas numpy tableau powerbi excel analytics statistics
security cybersecurity soc siem networking cloud backend frontend full-stack fullstack mobile android ios
figma ux ui design photoshop illustrator branding copywriting content seo sem marketing social-media
email-marketing crm hubspot salesforce sales b2b b2c saas negotiation account-management lead-generation
customer-support customer-service zendesk intercom helpdesk onboarding retention success
finance accounting bookkeeping payroll tax audit budgeting forecasting fintech payments compliance kyc aml
project-management agile scrum jira product roadmap stakeholder operations logistics supply-chain procurement
recruiting hr people training mentoring leadership management writing editing translation research
english french spanish german portuguese swahili arabic
""".split()
)
ALIASES = {"postgres": "postgresql", "node": "node.js", "golang": "go", "fullstack": "full-stack", "apis": "api"}


def _terms(text: str) -> set[str]:
    lowered = text.lower()
    lowered = re.sub(r"(customer|social|email|account|lead|supply|machine|project) (support|service|media|marketing|management|generation|chain|learning)", r"\1-\2", lowered)
    lowered = lowered.replace("full stack", "full-stack").replace("power bi", "powerbi")
    words = set(re.findall(r"[a-z][a-z0-9+#./\-]{0,30}", lowered))
    return {ALIASES.get(w.rstrip(".,"), w.rstrip(".,")) for w in words} & SKILLS


def _keyword_match(cv: str, job: JobIn) -> dict:
    """Without a model: which of the skills the job asks for the CV mentions."""
    wanted = _terms(f"{job.title} {' '.join(job.tags)} {job.description}")
    have = sorted(wanted & _terms(cv))
    missing = sorted(wanted - set(have))
    score = round(100 * len(have) / len(wanted)) if wanted else 0
    return {
        "score": score,
        "verdict": "Strong match" if score >= 60 else "Partial match" if score >= 35 else "Weak match",
        "strengths": [f"Your CV mentions {w}" for w in have[:6]],
        "gaps": [f"The job asks for {w}" for w in missing[:4]],
        "missing_keywords": missing[:8],
        "method": "keywords",
    }


@router.post("/match")
@pay(SERVICES["match"]["price"], "Fit score for one job")
def match(payload: CvJobIn, request: Request) -> dict:
    if llm.enabled():
        try:
            result = llm.ask_json(
                "You are a strict, honest recruiter. Score how well this candidate fits this job, 0 to 100. "
                "Judge only from the CV; don't assume skills it doesn't show. Return keys: score (int), "
                "verdict (short phrase), strengths (up to 4 short strings), gaps (up to 4 short strings), "
                "missing_keywords (up to 8 words from the job the CV should mention if true).",
                f"JOB: {payload.job.title} at {payload.job.company}\nTags: {', '.join(payload.job.tags)}\n"
                f"{payload.job.description[:4000]}\n\nCV:\n{payload.cv[:8000]}",
            )
            result["score"] = max(0, min(100, int(result.get("score", 0))))
            result["method"] = "model"
        except (llm.LLMError, ValueError, TypeError):
            result = _keyword_match(payload.cv, payload.job)
    else:
        result = _keyword_match(payload.cv, payload.job)
    return {"job": payload.job.title, "company": payload.job.company, **result, "payment": _settled(request)}


# ---------------------------------------------------------------- writing

def _name(cv: str) -> str:
    first = next((line.strip() for line in cv.splitlines() if line.strip()), "")
    return first if 0 < len(first) <= 40 and not any(c.isdigit() for c in first) else ""


def _template_letter(cv: str, job: JobIn) -> str:
    matched = [s.removeprefix("Your CV mentions ") for s in _keyword_match(cv, job)["strengths"]][:3]
    skills = ", ".join(matched) if matched else "the skills in my CV"
    company = job.company or "your team"
    return (
        f"Dear {company} hiring team,\n\n"
        f"I'm applying for the {job.title} role. My background in {skills} lines up with what you describe, "
        f"and I'd like to bring that experience to {company}.\n\n"
        "My CV, attached, gives the detail of what I've done and the results I've delivered. "
        "I'd welcome the chance to talk about how I can help.\n\n"
        f"Kind regards,\n{_name(cv) or '[Your name]'}"
    )


HONEST = (
    "Write only from facts in the CV: never invent employers, titles, degrees, numbers or skills. "
    "If the CV lacks something the job wants, don't claim it."
)


@router.post("/cover-letter")
@pay(SERVICES["cover_letter"]["price"], "Cover letter for one job")
def cover_letter(payload: CvJobIn, request: Request) -> dict:
    letter, method = _template_letter(payload.cv, payload.job), "template"
    if llm.enabled():
        try:
            letter = llm.ask_text(
                "You write short, specific cover letters (under 230 words, plain text, no placeholders "
                "except the candidate's name if the CV has none). " + HONEST,
                f"JOB: {payload.job.title} at {payload.job.company}\n{payload.job.description[:4000]}\n\nCV:\n{payload.cv[:8000]}",
            )
            method = "model"
        except llm.LLMError:
            pass
    return {"job": payload.job.title, "company": payload.job.company, "letter": letter, "method": method, "payment": _settled(request)}


@router.post("/application-pack")
@pay(SERVICES["application_pack"]["price"], "Full application pack for one job")
def application_pack(payload: CvJobIn, request: Request) -> dict:
    job = payload.job
    if llm.enabled():
        try:
            pack = llm.ask_json(
                "You are a career coach preparing a candidate's application for one job. " + HONEST + " Return keys: "
                "summary (a 3-sentence professional summary for the top of the CV, tailored to the job), "
                "bullets (5 CV bullet points rewritten from the CV's real experience to fit the job), "
                "cover_letter (under 230 words), "
                "interview (5 objects with question and how_to_answer, drawing on the CV), "
                "keywords (up to 10 terms from the job to include where true).",
                f"JOB: {job.title} at {job.company}\n{job.description[:4000]}\n\nCV:\n{payload.cv[:9000]}",
            )
            return {"job": job.title, "company": job.company, **pack, "method": "model", "payment": _settled(request)}
        except llm.LLMError:
            pass
    fit = _keyword_match(payload.cv, job)
    return {
        "job": job.title,
        "company": job.company,
        "summary": f"Candidate for {job.title} at {job.company or 'the company'}, with experience in "
        + (", ".join(s.removeprefix("Your CV mentions ") for s in fit["strengths"][:4]) or "the areas the role needs")
        + ".",
        "bullets": [f"Add a CV bullet that shows {w}, if you have that experience" for w in fit["missing_keywords"][:5]],
        "cover_letter": _template_letter(payload.cv, job),
        "interview": [
            {"question": f"Why do you want to work at {job.company or 'this company'}?", "how_to_answer": "Tie the company's mission to a real project of yours."},
            {"question": f"What makes you a fit for {job.title}?", "how_to_answer": "Pick two results from your CV that match the job's top requirements."},
            {"question": "Tell me about a hard problem you solved.", "how_to_answer": "Situation, what you did, the result, with a number if you can."},
        ],
        "keywords": fit["missing_keywords"],
        "method": "template",
        "payment": _settled(request),
    }


@router.get("/health")
def health() -> dict:
    if not settings.careerkit_address:
        raise HTTPException(503, "Not set up")
    return {"ok": True, "model": settings.llm_model if llm.enabled() else None}
