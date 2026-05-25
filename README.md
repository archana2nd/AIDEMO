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
    ↓ (developer picks up ticket)
IN PROGRESS          ← agent sets this when branch is created

READY TO DEV         ← agent sets this when PR raised to develop

TESTING DEV          ← agent sets this when PR merged to develop
    
READY TO PREP        ← agent sets this when PR raised to prerelease/*

TESTING QA (PREP)    ← agent sets this when PR merged to prerelease/*
   
READY TO STAG        ← agent sets this when PR raised to release/*
    
TESTING UAT (STAG)  ← agent sets this when PR merged to release/*

READY TO PROD       ← agent sets this when PR raised to main/master
   
DONE / CLOSED       ←  (manual)


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

# Groq LLM
GROQ_API_KEY=your-groq-api-key
GROQ_MODEL=llama3-8b-8192

# Optional
JIRA_ASSIGNEE_EMAILS=dev1@example.com,dev2@example.com
```

If you'd rather not use a `.env` file, set the same variables in your shell environment before running the agent.


## How to Get Credentials

### GitHub Personal Access Token
1. Go to `github.com` → click your profile picture
2. Settings → Developer settings → Personal access tokens → Tokens (classic)
3. Generate new token → select `repo` scope → copy token

### Jira API Token
1. Go to https://id.atlassian.com/manage-profile/security/api-tokens
2. Create API token → give it a name (e.g. `jira-pr-agent`)
3. Copy the token immediately (shown only once)

### Groq api Key (Run AI models through API)
1. Create account and create api key here https://console.groq.com/keys

### Jira Base URL
Your Jira URL visible in the browser:
- Personal Jira: `https://your-name.atlassian.net`
- Company Jira: `https://puigdigital.atlassian.net`

### Jira Status Names
Go to your Jira board → Board settings → Columns — copy the exact status names into `config.py`. They are case sensitive.

## Groq LLM (Makes localhost public)
1. Install nggrok - https://ngrok.com/download
2. Create account - https://dashboard.ngrok.com/signup
3. Generate/copy ngrok auth token - https://dashboard.ngrok.com/get-started/your-authtoken
4. Add your Authtoken to the ngrok agent - 
Run command - ngrok config add-authtoken $YOUR_AUTHTOKEN
5. Expose local port - ngrok http 3000
6. Copy generated public ngrok URL - You’ll get URL like: https://abc123.ngrok-free.app
7. Open GitHub Repo - Repository → Settings → Webhooks → Add webhook →  In Payload URL → Paste: https://abc123.ngrok-free.app/webhook
8. Content Type - Choose:application/json
9. Select webhook events (usually Push event)
10. Save webhook
11. Push code chnages to GitHub repository
12. Receive webhook payload in local server terminal

---

## Setup & Run

```bash
# 1. Install dependencies
pip3 install -r requirements.txt

# 2. Edit config.py with your credentials

# 3. expose server - ngrok http 3000

# 3. Run
python3 webhook_server.py
```
---

## Limitations

- **In-memory tracker** — if the agent restarts, it re-evaluates all branches and PRs. Safe because Jira transitions are idempotent (moving to the same status twice is harmless).

- **Jira workflow restrictions** — the agent can only make transitions that your Jira workflow allows. If a transition isn't available, it logs a warning and skips.
