"""
Jira PR Status Agent — RAG Enhanced with Groq
    GitHub → Jira status sync via webhook-driven processing.

Uses Groq (free cloud LLM) to intelligently decide:
  - Whether a transition should happen
  - Which Jira status to transition to

Falls back to static mapping if Groq is unavailable.

Triggers:
  - Branch created (feature/bugfix/hotfix) → IN PROGRESS
  - PR opened to configured target branch  → LLM decides status
  - PR merged to configured target branch  → LLM decides status
"""

from __future__ import annotations

import re
import json
import logging
import requests
from requests.auth import HTTPBasicAuth
from dotenv import load_dotenv
from pathlib import Path

# Load .env located next to this script (works even when cwd is different)
env_path = Path(__file__).resolve().parent / ".env"
load_dotenv(dotenv_path=env_path)

# Import `Config` after loading .env so `os.getenv` sees the values
from config import Config

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

JIRA_KEY_RE = re.compile(r"\b([A-Z][A-Z0-9]+-\d+)\b")


def extract_ticket_keys(text: str) -> list:
    """Extract all Jira ticket keys from a string."""
    return list(dict.fromkeys(JIRA_KEY_RE.findall(text or "")))


def resolve_status_static(target_branch: str, status_map: list) -> str | None:
    """Fallback static status lookup from config map."""
    log.debug("resolve_status_static: target_branch='%s', status_map=%s", target_branch, status_map)
    for pattern, status in status_map:
        if pattern.endswith("/"):
            if target_branch.lower().startswith(pattern.lower()):
                log.debug("  ✓ matched prefix pattern '%s' → '%s'", pattern, status)
                return status
        else:
            if target_branch.lower() == pattern.lower():
                log.debug("  ✓ matched exact pattern '%s' → '%s'", pattern, status)
                return status
    log.debug("  ✗ no match found for '%s'", target_branch)
    return None


def is_watched_branch(branch_name: str) -> bool:
    """Return True if branch starts with a configured prefix."""
    return any(
        branch_name.lower().startswith(f"{prefix}/")
        for prefix in Config.BRANCH_PREFIXES
    )


def is_allowed_assignee(email: str | None) -> bool:
    """Return True if email is in the allowed list, or no filter is set."""
    if not Config.JIRA_ASSIGNEE_EMAILS:
        return True
    return (email or "").lower() in Config.JIRA_ASSIGNEE_EMAILS


def is_draft_or_wip_pr(pr: dict) -> bool:
    """Return True if the PR is a draft or the title contains WIP keywords."""
    title = pr.get("title", "") or ""
    draft_flag = pr.get("draft", False)
    if draft_flag:
        return True
    return bool(re.search(r"\b(WIP|DO NOT MERGE|DRAFT)\b", title, re.IGNORECASE))


# ---------------------------------------------------------------------------
# Groq LLM client
# ---------------------------------------------------------------------------

class GroqClient:
    """
    Cloud LLM client using Groq (free tier).
    Sends context about a PR and Jira ticket and asks the LLM
    to decide whether to transition and to which status.

    Sign up at https://console.groq.com
    Models: llama3-8b-8192 (fast), llama3-70b-8192 (smarter)
    """

    API_URL = "https://api.groq.com/openai/v1/chat/completions"

    def __init__(self):
        self.api_key = Config.GROQ_API_KEY
        self.model   = Config.GROQ_MODEL

    def is_available(self) -> bool:
        """Check if Groq API key is configured."""
        return bool(self.api_key)

    def decide_transition(self, context: dict) -> dict:
        """
        Ask the LLM to decide whether to transition a Jira ticket and to what status.

        context dict contains:
          - pr_title, pr_description, pr_draft, pr_labels
          - pr_review_state, target_branch, event (opened/merged)
          - ticket_key, ticket_type, ticket_priority, ticket_current_status
          - available_transitions (list of valid Jira status names)

        Returns:
          { "should_transition": bool, "target_status": str, "reason": str }
        """
        prompt = f"""You are a Jira workflow automation agent. Based on the context below, decide whether to transition a Jira ticket and to which status.

## Context

**Event:** PR {context['event']}
**PR Title:** {context['pr_title']}
**PR Description:** {context['pr_description'] or 'No description'}
**PR Draft:** {context['pr_draft']}
**PR Labels:** {', '.join(context['pr_labels']) or 'None'}
**PR Review State:** {context['pr_review_state']}
**Target Branch (where PR merges to):** {context['target_branch']}

**Jira Ticket:** {context['ticket_key']}
**Ticket Type:** {context['ticket_type']}
**Ticket Priority:** {context['ticket_priority']}
**Current Status:** {context['ticket_current_status']}
**Available Transitions:** {', '.join(context['available_transitions'])}

## Decision Rules
IMPORTANT: Use the Target Branch to decide the status. Match based on branch name patterns:
- If Target Branch starts with "develop" → transition to a dev ready status
- If Target Branch starts with "prerelease/" → transition to a prep/staging ready status
- If Target Branch starts with "release/" → transition to a staging ready status
- If Target Branch is "main" or "master" → transition to a prod ready status

ALSO: Never transition if:
- PR title contains "WIP", "DO NOT MERGE", or "DRAFT"
- PR is marked as draft
- PR has "changes requested" review state

## Response
Respond ONLY with a valid JSON object, no explanation, no markdown:
{{"should_transition": true, "target_status": "exact status name from available transitions", "reason": "one sentence explanation"}}"""

        try:
            resp = requests.post(
                self.API_URL,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": self.model,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.1,  # low temp for consistent decisions
                    "max_tokens": 200,
                },
                timeout=30,
            )
            resp.raise_for_status()

            raw = resp.json()["choices"][0]["message"]["content"].strip()
            log.debug("Groq raw response: %s", raw)

            # Extract JSON from response
            json_match = re.search(r'\{.*\}', raw, re.DOTALL)
            if json_match:
                result = json.loads(json_match.group())
                log.info("LLM decision: %s", result)
                return result

        except requests.HTTPError as exc:
            log.warning("Groq API error: %s — falling back to static map", exc)
        except json.JSONDecodeError:
            log.warning("Groq returned invalid JSON — falling back to static map")
        except Exception as exc:
            log.warning("Groq error: %s — falling back to static map", exc)

        return {"should_transition": False, "target_status": "", "reason": "LLM unavailable"}


# ---------------------------------------------------------------------------
# GitHub client
# ---------------------------------------------------------------------------

class GitHubClient:
    BASE = "https://api.github.com"

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {Config.GITHUB_TOKEN}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        })
        self.owner = Config.GITHUB_OWNER
        self.repo  = Config.GITHUB_REPO

    def get_branches(self) -> list:
        url  = f"{self.BASE}/repos/{self.owner}/{self.repo}/branches"
        resp = self.session.get(url, params={"per_page": 100})
        resp.raise_for_status()
        return [b["name"] for b in resp.json()]

    def get_pr_details(self, pr_number: int) -> dict:
        """Fetch full PR details including reviews and labels."""
        # Basic PR info
        url  = f"{self.BASE}/repos/{self.owner}/{self.repo}/pulls/{pr_number}"
        resp = self.session.get(url)
        resp.raise_for_status()
        pr = resp.json()

        # Reviews
        review_url  = f"{self.BASE}/repos/{self.owner}/{self.repo}/pulls/{pr_number}/reviews"
        review_resp = self.session.get(review_url)
        reviews     = review_resp.json() if review_resp.ok else []

        # Determine overall review state
        review_state = "no reviews"
        if reviews:
            states = [r["state"] for r in reviews]
            if "CHANGES_REQUESTED" in states:
                review_state = "changes requested"
            elif "APPROVED" in states:
                review_state = "approved"
            else:
                review_state = "pending"

        return {
            "id":            pr["number"],
            "title":         pr.get("title", ""),
            "description":   pr.get("body", "") or "",
            "source_branch": pr["head"]["ref"],
            "target_branch": pr["base"]["ref"],
            "draft":         pr.get("draft", False),
            "labels":        [l["name"] for l in pr.get("labels", [])],
            "review_state":  review_state,
            "merged":        pr.get("merged_at") is not None,
        }

    def _list_prs(self, state: str) -> list:
        url  = f"{self.BASE}/repos/{self.owner}/{self.repo}/pulls"
        resp = self.session.get(url, params={"state": state, "per_page": 100})
        resp.raise_for_status()
        return [
            {
                "id":            pr["number"],
                "title":         pr.get("title", ""),
                "source_branch": pr["head"]["ref"],
                "target_branch": pr["base"]["ref"],
                "merged":        pr.get("merged_at") is not None,
            }
            for pr in resp.json()
        ]

    def get_active_pull_requests(self) -> list:
        return self._list_prs("open")

    def get_completed_pull_requests(self) -> list:
        return self._list_prs("closed")


# ---------------------------------------------------------------------------
# Jira client
# ---------------------------------------------------------------------------

class JiraClient:
    def __init__(self):
        self.auth    = HTTPBasicAuth(Config.JIRA_EMAIL, Config.JIRA_API_TOKEN)
        self.headers = {"Accept": "application/json", "Content-Type": "application/json"}
        self.base    = Config.JIRA_BASE_URL.rstrip("/")

    def get_issue(self, key: str) -> dict | None:
        url  = f"{self.base}/rest/api/3/issue/{key}"
        resp = requests.get(url, auth=self.auth, headers=self.headers)
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        return resp.json()

    def get_full_context(self, key: str) -> dict | None:
        """Return full ticket context for LLM prompt in a single API call."""
        issue = self.get_issue(key)
        if not issue:
            return None
        fields   = issue.get("fields", {})
        assignee = fields.get("assignee") or {}
        return {
            "key":            key,
            "type":           fields.get("issuetype", {}).get("name", "Unknown"),
            "priority":       fields.get("priority", {}).get("name", "None") if fields.get("priority") else "None",
            "current_status": fields["status"]["name"],
            "assignee_email": assignee.get("emailAddress", "").lower() or None,
        }

    def get_transitions(self, key: str) -> list:
        url  = f"{self.base}/rest/api/3/issue/{key}/transitions"
        resp = requests.get(url, auth=self.auth, headers=self.headers)
        resp.raise_for_status()
        return resp.json().get("transitions", [])

    def get_transition_names(self, key: str) -> list:
        """Return list of available transition status names."""
        return [t["to"]["name"] for t in self.get_transitions(key)]

    def transition_issue(self, key: str, target_status: str) -> bool:
        """Transition a Jira issue to the named status."""
        transitions = self.get_transitions(key)
        match = next(
            (t for t in transitions if t["to"]["name"].lower() == target_status.lower()),
            None,
        )
        if not match:
            log.warning(
                "No transition to '%s' available for %s. Available: %s",
                target_status, key,
                [t["to"]["name"] for t in transitions],
            )
            return False

        url  = f"{self.base}/rest/api/3/issue/{key}/transitions"
        resp = requests.post(
            url,
            json={"transition": {"id": match["id"]}},
            auth=self.auth,
            headers=self.headers,
        )
        if resp.status_code in (200, 204):
            log.info("✓ %s → '%s'", key, target_status)
            return True
        log.error("Failed to transition %s: %s %s", key, resp.status_code, resp.text)
        return False


# ---------------------------------------------------------------------------
# State tracker
# ---------------------------------------------------------------------------

class SeenTracker:
    def __init__(self):
        self._branches: set[str] = set()
        self._opened:   set[int] = set()
        self._merged:   set[int] = set()

    def mark_branch(self, name: str):      self._branches.add(name)
    def has_seen_branch(self, name: str):  return name in self._branches
    def mark_opened(self, pr_id: int):     self._opened.add(pr_id)
    def has_seen_opened(self, pr_id: int): return pr_id in self._opened
    def mark_merged(self, pr_id: int):     self._merged.add(pr_id)
    def has_seen_merged(self, pr_id: int): return pr_id in self._merged


# ---------------------------------------------------------------------------
# Initialise clients
# ---------------------------------------------------------------------------

tracker = SeenTracker()
github  = GitHubClient()
jira    = JiraClient()
llm     = GroqClient()


# ---------------------------------------------------------------------------
# Core logic
# ---------------------------------------------------------------------------

def handle_pr_event(pr_id: int, event: str):
    """
    Handle a PR event (opened or merged) using LLM to decide the transition.
    Falls back to static map if Groq is unavailable.
    """
    # Fetch full PR details including reviews and labels
    pr = github.get_pr_details(pr_id)

    # Skip draft/WIP PRs immediately before any transition logic or LLM call.
    if is_draft_or_wip_pr(pr):
        log.info("PR #%s is draft/WIP — skipping transition", pr_id)
        return

    # Skip draft PRs immediately before any transition logic or LLM call.
    if pr.get("draft", False):
        log.info("PR #%s is a draft — skipping transition", pr_id)
        return

    # Extract ticket keys from title then source branch
    keys = extract_ticket_keys(pr["title"]) or extract_ticket_keys(pr["source_branch"])
    if not keys:
        log.debug("PR #%s: no Jira keys found — skipping", pr_id)
        return

    for key in keys:
        # Get full Jira context
        ticket = jira.get_full_context(key)
        if not ticket:
            log.warning("%s: not found in Jira — skipping", key)
            continue

        # Assignee check
        if not is_allowed_assignee(ticket["assignee_email"]):
            log.info("Skipping %s — assignee '%s' not in allowed list", key, ticket["assignee_email"])
            continue

        # Get available transitions
        available = jira.get_transition_names(key)

        # Static branch-prefix map should take precedence for known target branches.
        # This avoids the LLM choosing the wrong status for obvious branch patterns.
        status_map = Config.PR_OPENED_MAP if event == "opened" else Config.PR_MERGED_MAP
        static_status = resolve_status_static(pr["target_branch"], status_map)
        if static_status:
            log.info("PR #%s target_branch='%s' matched static map → '%s'", pr_id, pr["target_branch"], static_status)
            jira.transition_issue(key, static_status)
            continue

        # ── Groq LLM decision ────────────────────────────────────────────
        if llm.is_available():
            log.info("PR #%s | target_branch='%s' | asking Groq LLM to decide transition for %s", pr_id, pr["target_branch"], key)

            context = {
                "event":                 event,
                "pr_title":              pr["title"],
                "pr_description":        pr["description"],
                "pr_draft":              pr["draft"],
                "pr_labels":             pr["labels"],
                "pr_review_state":       pr["review_state"],
                "target_branch":         pr["target_branch"],
                "ticket_key":            key,
                "ticket_type":           ticket["type"],
                "ticket_priority":       ticket["priority"],
                "ticket_current_status": ticket["current_status"],
                "available_transitions": available,
            }
            log.debug("LLM context: %s", context)

            decision = llm.decide_transition(context)

            if not decision.get("should_transition"):
                log.info("LLM decided NOT to transition %s. Reason: %s", key, decision.get("reason"))
                continue

            target_status = decision.get("target_status", "")
            if not target_status:
                log.warning("LLM returned empty status for %s — skipping", key)
                continue

            log.info("LLM decision for %s: → '%s' | Reason: %s", key, target_status, decision.get("reason"))
            jira.transition_issue(key, target_status)

        # ── Static fallback ──────────────────────────────────────────────
        else:
            log.info("Groq not configured — using static map for PR #%s", pr_id)
            status_map = Config.PR_OPENED_MAP if event == "opened" else Config.PR_MERGED_MAP
            status     = resolve_status_static(pr["target_branch"], status_map)
            if not status:
                log.debug("PR #%s: target '%s' not in static map — skipping", pr_id, pr["target_branch"])
                continue
            log.info("PR #%s %s → '%s' | %s → '%s'", pr_id, event, pr["target_branch"], key, status)
            jira.transition_issue(key, status)


def process_branch(branch_name: str):
    """
    When a new feature/bugfix/hotfix branch is created,
    move the linked Jira ticket to IN PROGRESS.
    Branch creation always uses static rule — no LLM needed.
    """
    if tracker.has_seen_branch(branch_name):
        return
    tracker.mark_branch(branch_name)

    if not is_watched_branch(branch_name):
        return

    keys = extract_ticket_keys(branch_name)
    if not keys:
        return

    for key in keys:
        ticket = jira.get_full_context(key)
        if not ticket:
            log.warning("%s: not found in Jira — skipping", key)
            continue
        if not is_allowed_assignee(ticket["assignee_email"]):
            log.info("Skipping %s — assignee not in allowed list", key)
            continue
        # Only move to IN PROGRESS if ticket hasn't already moved forward
        if ticket["current_status"].upper() not in ["IDEA","TO DO", "READY TO DO", "IN REQUIREMENTS", "CREATED"]:
            log.info("Skipping %s — already at '%s', won't revert to IN PROGRESS", key, ticket["current_status"])
            continue
        log.info("Branch '%s' | %s → '%s'", branch_name, key, Config.STATUS_IN_PROGRESS)
        jira.transition_issue(key, Config.STATUS_IN_PROGRESS)


def process_pr_opened(pr: dict):
    """Process a newly opened PR."""
    pr_id = pr["id"]
    if tracker.has_seen_opened(pr_id):
        return
    full_pr = github.get_pr_details(pr_id)
    if is_draft_or_wip_pr(full_pr):
        log.info("PR #%s is draft/WIP at opened event — skipping until ready", pr_id)
        return
    tracker.mark_opened(pr_id)
    handle_pr_event(pr_id, "opened")


def process_pr_merged(pr: dict):
    """Process a merged PR."""
    pr_id = pr["id"]
    if tracker.has_seen_merged(pr_id):
        return
    tracker.mark_merged(pr_id)
    handle_pr_event(pr_id, "merged")

# This module contains core GitHub/Jira transition logic for `Jira PR RAG`.
# `webhook_server.py` is the intended entrypoint in webhook-driven mode.
