"""Tests that need no network: matching, validation, the paywall's 402, the page's config."""

import os

os.environ.setdefault("CAREERKIT_ADDRESS", "GB7CQMPD46MJ7D3MGIYTT6J5HEVPKJE6JLKLJNWI52YBK3P2IB4RQMCX")

import pytest
from fastapi.testclient import TestClient

from app import sources
from app.careerkit import JobIn, _keyword_match, _terms
from app.main import app
from app.runs import RunError, Runs, _amount

client = TestClient(app)
CV = open("app/sample_cv.txt", encoding="utf-8").read()


def test_terms_find_skills_and_aliases():
    assert {"python", "postgresql", "customer-support", "node.js"} <= _terms("Python, Postgres, customer support, Node")


def test_keyword_match_scores_what_the_cv_covers():
    job = JobIn(title="Backend Python Developer", description="FastAPI, PostgreSQL, Docker, Kubernetes, AWS")
    result = _keyword_match(CV, job)
    assert result["method"] == "keywords"
    assert "kubernetes" in result["missing_keywords"]
    assert 60 <= result["score"] < 100


def test_html_is_stripped_from_listings():
    text = sources._text("<p>Hello <b>world</b> &amp; co</p><p>next</p>")
    assert "<" not in text and "Hello world & co" in text and "next" in text


def test_amounts_are_bounded():
    assert _amount("1", "x", "0.05", "10") == "1"
    with pytest.raises(RunError):
        _amount("50", "x", "0.05", "10")
    with pytest.raises(RunError):
        _amount("abc", "x", "0.05", "10")


def test_runs_reject_bad_input():
    runs = Runs()
    with pytest.raises(RunError, match="CV"):
        runs.start({"cv": "too short", "role": "dev", "services": ["search"]}, "1.1.1.1")
    with pytest.raises(RunError, match="Job search"):
        runs.start({"cv": "x" * 100, "role": "dev", "services": ["match"]}, "1.1.1.1")
    with pytest.raises(RunError, match="agent secret"):
        runs.start({"cv": "x" * 100, "role": "dev", "services": ["search"], "mode": "own", "agentSecret": "nope"}, "1.1.1.1")


def test_paid_routes_answer_402_with_the_price():
    response = client.get("/careerkit/jobs", params={"q": "python"})
    assert response.status_code == 402
    accepts = response.json()["accepts"][0]
    assert accepts["maxAmountRequired"] == "500000"  # 0.05 USDC, 7 decimals
    assert accepts["payTo"] == os.environ["CAREERKIT_ADDRESS"]
    pack = client.post("/careerkit/application-pack", json={"cv": CV, "job": {"title": "Dev"}})
    assert pack.status_code == 402
    assert pack.json()["accepts"][0]["maxAmountRequired"] == "50000000"  # 5 USDC


def test_catalog_and_config_are_free():
    catalog = client.get("/careerkit/").json()
    assert [s["id"] for s in catalog["services"]] == ["search", "match", "cover_letter", "application_pack"]
    config = client.get("/api/config").json()
    assert config["sampleCv"].startswith("Amara")
