"""
Jira PR Status Agent — Core Logic (Pure LLM)
RAG-enhanced with Groq LLM. No static branch mapping.

This module contains all GitHub/Jira/LLM logic.
webhook_server.py is the entry point.

Triggers:
  - Branch created (feature/bugfix/hotfix) → IN PROGRESS  (simple Python rule)
  - PR opened to any branch               → Groq LLM decides status
  - PR merged to any branch               → Groq LLM decides status

Draft/WIP PRs are skipped via Python check before LLM is called.
If Groq is unavailable, PR events are skipped and logged.
"""

from __future__ import annotations

import re
import json
import logging
import requests
from requests.auth import HTTPBasicAuth
from dotenv import load_dotenv
from pathlib import Path

# Load .env located next to this script
env_path = Path(__file__).resolve().parent / ".env"
load_dotenv(dotenv_path=env_path)

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

# Regex to find Jira ticket keys like SCRUM-5, AIDEMO-2, WLD-597
JIRA_KEY_RE = re.compile(r"\b([A-Z][A-Z0-9]+-\d+)\b")


def extract_ticket_keys(text: str) -> list:
    """Extract all Jira ticket keys from a string."""
    return list(dict.fromkeys(JIRA_KEY_RE.findall(text or "")))


def is_watched_branch(branch_name: str) -> bool:
    """Return True if branch starts with a configured prefix (feature/, bugfix/, hotfix/)."""
    return any(
        branch_name.lower().startswith(f"{prefix}/")
        for prefix in Config.BRANCH_PREFIXES
    )


def is_allowed_assignee(email: str | None) -> bool:
    """Return True if email is in the allowed list, or no filter is set."""
    if not Config.JIRA_ASSIGNEE_EMAILS:
        return True
    return (email or "").lower() in Config.JIRA_ASSIGNEE_EMAILS


def is_draft_or_wip(pr: dict) -> bool:
    """
    Return True if PR should be skipped.
    Checks two things via simple Python — no LLM needed:
      - PR is marked as draft on GitHub
      - PR title contains WIP, DO NOT MERGE, or DRAFT keywords
    """
    title = pr.get("title", "") or ""
    if pr.get("draft", False):
        return True
    return bool(re.search(r"\b(WIP|DO NOT MERGE|DRAFT)\b", title, re.IGNORECASE))


# ---------------------------------------------------------------------------
# Groq LLM client
# ---------------------------------------------------------------------------

class GroqClient:
    """
    Cloud LLM client using Groq (free tier, no installation needed).

    Implements the RAG pattern:
      RETRIEVE  → GitHub PR details + Jira ticket context (done before this call)
      AUGMENT   → Build a rich prompt with all retrieved context
      GENERATE  → LLM returns a JSON decision

    Sign up free at https://console.groq.com
    """

    API_URL = "https://api.groq.com/openai/v1/chat/completions"

    def __init__(self):
        self.api_key = Config.GROQ_API_KEY
        self.model   = Config.GROQ_MODEL

    def is_available(self) -> bool:
        """Return True if a Groq API key is configured."""
        return bool(self.api_key)

    def decide_transition(self, context: dict) -> dict:
        """
        Send PR and Jira context to the LLM and get a transition decision.

        context keys:
          event, pr_title, pr_description, pr_labels,
          pr_review_state, target_branch,
          ticket_key, ticket_type, ticket_priority,
          ticket_current_status, available_transitions

        Returns:
          { "should_transition": bool, "target_status": str, "reason": str }
        """
        prompt = f"""You are a Jira workflow automation agent. Based on the context below, decide whether to transition a Jira ticket and to which status.

## Context

**Event:** PR {context['event']}
**PR Title:** {context['pr_title']}
**PR Description:** {context['pr_description'] or 'No description'}
**PR Labels:** {', '.join(context['pr_labels']) or 'None'}
**PR Review State:** {context['pr_review_state']}
**Target Branch:** {context['target_branch']}

**Jira Ticket:** {context['ticket_key']}
**Ticket Type:** {context['ticket_type']}
**Ticket Priority:** {context['ticket_priority']}
**Current Status:** {context['ticket_current_status']}
**Available Transitions:** {', '.join(context['available_transitions'])}

## Decision Rules
- If PR has "changes requested" review state → do NOT transition
- If Target Branch is "develop" or starts with "dev" → transition to a dev ready status
- If Target Branch starts with "release/" or "prerelease/" → transition to a staging ready status
- If Target Branch is "main" or "master" → transition to prod ready or done status
- If PR is merged → transition to corresponding testing status
- Only choose from the available transitions listed above
- If none make sense → do NOT transition

## Response
Respond ONLY with a valid JSON object, no markdown, no explanation:
{{"should_transition": true, "target_status": "exact status name from available transitions", "reason": "one sentence explanation"}}"""

        try:
            resp = requests.post(
                self.API_URL,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model":       self.model,
                    "messages":    [{"role": "user", "content": prompt}],
                    "temperature": 0.1,
                    "max_tokens":  200,
                },
                timeout=30,
            )
            resp.raise_for_status()

            raw = resp.json()["choices"][0]["message"]["content"].strip()
            log.debug("Groq raw response: %s", raw)

            json_match = re.search(r'\{.*\}', raw, re.DOTALL)
            if json_match:
                result = json.loads(json_match.group())
                log.info("LLM decision: %s", result)
                return result

        except requests.HTTPError as exc:
            log.warning("Groq API error: %s — skipping transition", exc)
        except json.JSONDecodeError:
            log.warning("Groq returned invalid JSON — skipping transition")
        except Exception as exc:
            log.warning("Groq error: %s — skipping transition", exc)

        return {"should_transition": False, "target_status": "", "reason": "LLM unavailable"}


# ---------------------------------------------------------------------------
# GitHub client
# ---------------------------------------------------------------------------

class GitHubClient:
    """Fetches PR details and reviews from GitHub REST API."""

    BASE = "https://api.github.com"

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization":        f"Bearer {Config.GITHUB_TOKEN}",
            "Accept":               "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        })
        self.owner = Config.GITHUB_OWNER
        self.repo  = Config.GITHUB_REPO

    def get_pr_details(self, pr_number: int) -> dict:
        """
        Fetch full PR details including review state and labels.
        Makes two API calls: one for PR info, one for reviews.
        """
        # PR info
        pr_resp = self.session.get(
            f"{self.BASE}/repos/{self.owner}/{self.repo}/pulls/{pr_number}"
        )
        pr_resp.raise_for_status()
        pr = pr_resp.json()

        # Reviews — determine overall review state
        reviews_resp = self.session.get(
            f"{self.BASE}/repos/{self.owner}/{self.repo}/pulls/{pr_number}/reviews"
        )
        reviews      = reviews_resp.json() if reviews_resp.ok else []
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


# ---------------------------------------------------------------------------
# Jira client
# ---------------------------------------------------------------------------

class JiraClient:
    """Reads ticket context and applies transitions via Jira REST API."""

    def __init__(self):
        self.auth    = HTTPBasicAuth(Config.JIRA_EMAIL, Config.JIRA_API_TOKEN)
        self.headers = {"Accept": "application/json", "Content-Type": "application/json"}
        self.base    = Config.JIRA_BASE_URL.rstrip("/")

    def get_issue(self, key: str) -> dict | None:
        """Fetch a Jira issue. Returns None if not found."""
        resp = requests.get(
            f"{self.base}/rest/api/3/issue/{key}",
            auth=self.auth, headers=self.headers
        )
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        return resp.json()

    def get_full_context(self, key: str) -> dict | None:
        """
        Return ticket context for the LLM prompt in a single API call:
        assignee email, current status, ticket type, priority.
        """
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
        """Return available transitions for a ticket."""
        resp = requests.get(
            f"{self.base}/rest/api/3/issue/{key}/transitions",
            auth=self.auth, headers=self.headers
        )
        resp.raise_for_status()
        return resp.json().get("transitions", [])

    def get_transition_names(self, key: str) -> list:
        """Return list of available transition status names."""
        return [t["to"]["name"] for t in self.get_transitions(key)]

    def transition_issue(self, key: str, target_status: str) -> bool:
        """Transition a Jira issue to the named status. Returns True on success."""
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

        resp = requests.post(
            f"{self.base}/rest/api/3/issue/{key}/transitions",
            json={"transition": {"id": match["id"]}},
            auth=self.auth, headers=self.headers,
        )
        if resp.status_code in (200, 204):
            log.info("✓ %s → '%s'", key, target_status)
            return True
        log.error("Failed to transition %s: %s %s", key, resp.status_code, resp.text)
        return False


# ---------------------------------------------------------------------------
# State tracker — prevents duplicate processing in same session
# ---------------------------------------------------------------------------

class SeenTracker:
    """
    In-memory tracker to prevent the same branch or PR from being
    processed more than once per agent session.
    """
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
# Initialise shared clients
# ---------------------------------------------------------------------------

tracker = SeenTracker()
github  = GitHubClient()
jira    = JiraClient()
llm     = GroqClient()


# ---------------------------------------------------------------------------
# Core event handlers
# ---------------------------------------------------------------------------

def handle_pr_event(pr_id: int, event: str):
    """
    Central handler for PR opened and PR merged events.

    Flow:
      1. Fetch full PR details from GitHub (title, description, draft, labels, reviews)
      2. Skip immediately if PR is draft or WIP — no LLM needed
      3. Extract Jira ticket key from PR title or source branch
      4. Fetch full Jira ticket context (type, priority, status, assignee)
      5. Check assignee filter
      6. Get available Jira transitions
      7. Ask Groq LLM to decide — should_transition? target_status?
      8. Apply the transition if LLM says yes
    """
    # Step 1 — fetch full PR details
    pr = github.get_pr_details(pr_id)

    # Step 2 — skip draft/WIP before any Jira or LLM calls
    if is_draft_or_wip(pr):
        log.info("PR #%s is draft/WIP — skipping", pr_id)
        return

    # Step 3 — extract Jira ticket key
    keys = extract_ticket_keys(pr["title"]) or extract_ticket_keys(pr["source_branch"])
    if not keys:
        log.debug("PR #%s: no Jira keys found — skipping", pr_id)
        return

    # Step 4-8 — process each ticket key
    for key in keys:
        # Fetch Jira ticket context
        ticket = jira.get_full_context(key)
        if not ticket:
            log.warning("%s: not found in Jira — skipping", key)
            continue

        # Assignee filter
        if not is_allowed_assignee(ticket["assignee_email"]):
            log.info("Skipping %s — assignee '%s' not in allowed list", key, ticket["assignee_email"])
            continue

        # Check Groq is available
        if not llm.is_available():
            log.warning("Groq not configured — skipping PR #%s", pr_id)
            continue

        # Get available Jira transitions for this ticket
        available = jira.get_transition_names(key)

        # Build context for LLM
        context = {
            "event":                 event,
            "pr_title":              pr["title"],
            "pr_description":        pr["description"],
            "pr_labels":             pr["labels"],
            "pr_review_state":       pr["review_state"],
            "target_branch":         pr["target_branch"],
            "ticket_key":            key,
            "ticket_type":           ticket["type"],
            "ticket_priority":       ticket["priority"],
            "ticket_current_status": ticket["current_status"],
            "available_transitions": available,
        }

        # Ask LLM to decide
        log.info("PR #%s | asking Groq LLM for %s (event: %s)", pr_id, key, event)
        decision = llm.decide_transition(context)

        if not decision.get("should_transition"):
            log.info("LLM decided NOT to transition %s. Reason: %s", key, decision.get("reason"))
            continue

        target_status = decision.get("target_status", "")
        if not target_status:
            log.warning("LLM returned empty status for %s — skipping", key)
            continue

        log.info("LLM: %s → '%s' | Reason: %s", key, target_status, decision.get("reason"))
        jira.transition_issue(key, target_status)


def process_branch(branch_name: str):
    """
    Called when a new branch is created.
    Simple Python rule — no LLM needed:
    If branch name contains a Jira key and starts with a watched prefix,
    move the ticket to IN PROGRESS.
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
        # Only set IN PROGRESS if ticket hasn't already moved forward
        if ticket["current_status"].upper() not in ["IDEA", "TO DO", "READY TO DO", "IN REQUIREMENTS", "CREATED"]:
            log.info("Skipping %s — already at '%s', won't revert to IN PROGRESS", key, ticket["current_status"])
            continue
        log.info("Branch '%s' | %s → '%s'", branch_name, key, Config.STATUS_IN_PROGRESS)
        jira.transition_issue(key, Config.STATUS_IN_PROGRESS)


def process_pr_opened(pr: dict):
    """Called when a PR is opened, edited, or marked ready for review."""
    pr_id = pr["id"]
    if tracker.has_seen_opened(pr_id):
        return
    tracker.mark_opened(pr_id)
    handle_pr_event(pr_id, "opened")


def process_pr_merged(pr: dict):
    """Called when a PR is merged."""
    pr_id = pr["id"]
    if tracker.has_seen_merged(pr_id):
        return
    tracker.mark_merged(pr_id)
    handle_pr_event(pr_id, "merged")
