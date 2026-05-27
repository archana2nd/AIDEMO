"""
Jira PR Status Agent — Configuration
Edit the values below or set via .env file.
"""

import os


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
    # Only branches starting with these prefixes are watched.
    BRANCH_PREFIXES = ["feature", "bugfix", "hotfix"]

    # ── Status set when a watched branch is created ────────────────────────
    # This is the only static rule — branch creation always means IN PROGRESS.
    STATUS_IN_PROGRESS = "In Progress"

    # ── Groq LLM ───────────────────────────────────────────────────────────
    # Free API — sign up at https://console.groq.com
    # All PR transition decisions are made by the LLM — no static maps.
    GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
    GROQ_MODEL   = os.getenv("GROQ_MODEL",   "llama-3.1-8b-instant")
