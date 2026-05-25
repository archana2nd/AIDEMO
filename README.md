# Jira PR RAG (RAG-enabled / AI-assisted)

A RAG-enabled, AI-assisted Python agent that automatically updates Jira ticket statuses based on branch creation and pull request activity in Azure DevOps or GitHub.

## The Problem It Solves

Developers raise a PR and forget to update the Jira ticket status manually. This agent does it for them — automatically, every 5 minutes.

## How It Works

### The Key Idea — Ticket Key in Branch/PR Name

Jira and GitHub/ADO have no built-in connection. The agent bridges them by looking for a **Jira ticket key** in the branch name or PR title.

```
branch name:  feature/WLD-597-add-login
                       ↑
                  agent finds WLD-597
                       ↓
              looks up WLD-597 in Jira
                       ↓
              updates the ticket status
```

A Jira ticket key looks like `WLD-597`, `TEST-1`, `PROJ-123` — uppercase letters, a dash, and a number.

**This is the only convention your team needs to follow:**
- Put the Jira ticket key in the branch name, OR
- Put the Jira ticket key in the PR title

---

## Full Flow

### Flow 1 — Branch Created → IN PROGRESS

```
Developer creates branch: feature/WLD-597-add-login
            ↓
GitHub webhook notifies the service
            ↓
Agent processes the branch creation event
            ↓
Agent extracts ticket key: WLD-597
            ↓
Agent checks Jira: is WLD-597 assigned to an allowed user?
            ↓ yes
Agent calls Jira API → transitions WLD-597 to "IN PROGRESS"
            ↓
Jira ticket is now IN PROGRESS ✅
```

Only fires for `feature/`, `bugfix/`, or `hotfix/` branches.

---

### Flow 2 — PR Opened → Status Based on Target Branch

```
Developer raises PR:
    Title:  "WLD-597 Add login page"
    From:   feature/WLD-597-add-login
    To:     develop
                        ↓
GitHub webhook notifies the service
                        ↓
Agent processes the PR event
            ↓
Agent extracts ticket key from title: WLD-597
(if not in title, tries branch name)
            ↓
Agent checks target branch → "develop" → maps to "READY TO DEV"
            ↓
Agent checks Jira: is WLD-597 assigned to an allowed user?
            ↓ yes
Agent calls Jira API → transitions WLD-597 to "READY TO DEV"
            ↓
Jira ticket is now READY TO DEV ✅
```

---

## Branch to Jira Status Mapping

| PR target branch | Jira status set |
|---|---|
| `develop` | READY TO DEV |
| `prerelease/*` | READY TO PREP |
| `release` | READY TO STAG |
| `main` / `master` | READY TO PROD |

---

## Full Jira Workflow

```
READY TO DO
    ↓ (manual — developer picks up ticket)
IN PROGRESS          ← agent sets this when branch is created
    ↓ (manual — developer codes)
READY TO DEV         ← agent sets this when PR raised to develop
    ↓ (manual — QA tests)
TESTING DEV
    ↓ (manual)
READY TO PREP        ← agent sets this when PR raised to prerelease/*
    ↓ (manual — QA tests)
TESTING QA (PREP)
    ↓ (manual)
READY TO STAG        ← agent sets this when PR raised to release/*
    ↓ (manual — UAT)
TESTING UAT (STAG)
    ↓ (manual)
READY TO PROD        ← agent sets this when PR raised to main/master
    ↓ (manual)
DONE / CLOSED
```

Agent updates are automatic. Everything else is manual.

---

## Naming Convention (Required)


Your team must include the Jira ticket key in the branch name or PR title:

```
✅ Branch names:
feature/WLD-597-add-login
bugfix/WLD-612-fix-null-crash
hotfix/WLD-700-urgent-payment-fix

✅ PR titles:
WLD-597 Add login page
feat: WLD-612 - fix null crash
[WLD-700] urgent payment fix

❌ agent will skip these:
feature/add-login             (no ticket key)
Fix the bug                   (no ticket key in title or branch)
```

---

## Project Structure

```
`core.py`        ← core logic, branch/PR processing rules
config.py       ← all configuration (credentials, status names)
requirements.txt
Dockerfile
README.md
.env

```

---

## Configuration

Edit `config.py` or set environment variables:

### Required environment variables

Create a `.env` file in the project root (next to `core.py`) with these values. DO NOT commit your `.env` — it's already ignored by `.gitignore`.

```
# GitHub
GITHUB_TOKEN=your-github-personal-access-token
GITHUB_OWNER=your-github-username
GITHUB_REPO=your-repo-name

# Jira
JIRA_BASE_URL=https://your-domain.atlassian.net
JIRA_EMAIL=you@example.com
JIRA_API_TOKEN=your-jira-api-token

# Optional (Groq LLM)
GROQ_API_KEY=your-groq-api-key
GROQ_MODEL=llama3-8b-8192

# Optional
JIRA_ASSIGNEE_EMAILS=dev1@example.com,dev2@example.com
```

If you'd rather not use a `.env` file, set the same variables in your shell environment before running the agent.


### For Demo (GitHub + Personal Jira)

```python
GITHUB_TOKEN = "your-github-pat"
GITHUB_OWNER = "your-github-username"
GITHUB_REPO  = "your-repo-name"

JIRA_BASE_URL  = "https://your-name.atlassian.net"
JIRA_EMAIL     = "your.personal@gmail.com"
JIRA_API_TOKEN = "your-jira-token"

JIRA_ASSIGNEE_EMAILS = ""   # empty = update all tickets
```

JIRA_ASSIGNEE_EMAILS = "dev1@cognizant.com,dev2@cognizant.com"
```

---

## How to Get Credentials

### GitHub Personal Access Token
1. Go to `github.com` → click your profile picture
2. Settings → Developer settings → Personal access tokens → Tokens (classic)
3. Generate new token → select `repo` scope → copy token

### Jira API Token
1. Go to https://id.atlassian.com/manage-profile/security/api-tokens
2. Create API token → give it a name (e.g. `jira-pr-agent`)
3. Copy the token immediately (shown only once)

### Jira Base URL
Your Jira URL visible in the browser:
- Personal Jira: `https://your-name.atlassian.net`
- Company Jira: `https://puigdigital.atlassian.net`

### Jira Status Names
Go to your Jira board → Board settings → Columns — copy the exact status names into `config.py`. They are case sensitive.

---

## Setup & Run

```bash
# 1. Install dependencies
pip3 install -r requirements.txt

# 2. Edit config.py with your credentials

# 3. Run
python3 webhook_server.py
```
---

## Limitations

- **In-memory tracker** — if the agent restarts, it re-evaluates all branches and PRs. Safe because Jira transitions are idempotent (moving to the same status twice is harmless).
- **5 minute delay** — not real-time. For real-time, a webhook-based approach is needed.
- **Jira workflow restrictions** — the agent can only make transitions that your Jira workflow allows. If a transition isn't available, it logs a warning and skips.
