"""
Jira PR Status Agent — Configuration
Edit the values below to match your setup.
"""

import os

#  Helper function to parse branch-status mappings from config strings
def _parse_map(raw: str) -> list:
    #Read branch and Jira status mappings from a config string.
    result = []
    for entry in raw.split(","):
        if ":" in entry:
            branch, status = entry.strip().split(":", 1)
            result.append((branch.strip(), status.strip()))
    return result


class Config:
    # ── GitHub ─────────────────────────────────────────────────────────────
    GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")
    GITHUB_OWNER = os.getenv("GITHUB_OWNER", "")
    GITHUB_REPO  = os.getenv("GITHUB_REPO",  "")

    # ── Jira ───────────────────────────────────────────────────────────────
    JIRA_BASE_URL  = os.getenv("JIRA_BASE_URL",  "")
    JIRA_EMAIL     = os.getenv("JIRA_EMAIL",     "")
    JIRA_API_TOKEN = os.getenv("JIRA_API_TOKEN", "")

    # ── Assignee filter ────────────────────────────────────────────────────
    # Comma-separated Jira emails. Leave empty "" to update all tickets.
    JIRA_ASSIGNEE_EMAILS = {
        e.strip().lower()
        for e in os.getenv("JIRA_ASSIGNEE_EMAILS", "").split(",")
        if e.strip()
    }

    # ── Branch prefixes that trigger IN PROGRESS on branch creation 
    BRANCH_PREFIXES = ["feature", "bugfix", "hotfix"]

    # ── Status set when a watched branch is created 
    STATUS_IN_PROGRESS = "In Progress"

    # ── Groq LLM ─────────────────────────────────────────────────
    GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
    GROQ_MODEL   = os.getenv("GROQ_MODEL",   "llama-3.1-8b-instant")  # fast and free

    # ── Branch-to-Jira status mappings ───────────────────────────────────────
    PR_OPENED_MAP = _parse_map(
        "develop:READY TO DEV,"
        "prerelease/:READY TO PREP,"
        "release/:READY TO STAG,"
        "main:READY TO PROD"
    )

    PR_MERGED_MAP = _parse_map(
        "develop:TESTING DEV,"
        "prerelease/:TESTING QA (PREP),"
        "release/:TESTING UAT,"
    )
