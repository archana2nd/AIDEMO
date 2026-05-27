"""
Jira PR Status Agent — Configuration
Edit the values below to match your setup.
"""

import os


def _parse_map(raw: str) -> list:
    """Parse 'branch:status,branch:status' into list of (branch, status) tuples."""
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

    # ── Branch prefixes that trigger IN PROGRESS on branch creation ────────
    BRANCH_PREFIXES = ["feature", "bugfix", "hotfix"]

    # ── Status set when a watched branch is created ────────────────────────
    STATUS_IN_PROGRESS = "In Progress"

    # ── Groq LLM settings ─────────────────────────────────────────────────
    # Free API — sign up at https://console.groq.com
    # Get your key at https://console.groq.com/keys
    GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
    GROQ_MODEL   = os.getenv("GROQ_MODEL",   "llama-3.1-8b-instant")  # fast and free

    # ── Fallback static maps (used when Groq is unavailable) ──────────────
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
        "main:DONE / CLOSED"
    )
