from __future__ import annotations

import logging
from flask import Flask, request, jsonify
from dotenv import load_dotenv
from pathlib import Path

# Load .env before importing agent logic
env_path = Path(__file__).resolve().parent / ".env"
load_dotenv(dotenv_path=env_path)

# Import agent logic and helpers
from core import (
    process_branch, process_pr_opened, process_pr_merged,
    log
)

app = Flask(__name__)

# GitHub webhook endpoint 
@app.route("/webhook", methods=["POST"])
def github_webhook():
    event = request.headers.get("X-GitHub-Event")
    payload = request.json

    log.info(f"Received GitHub event: {event}")
    log.debug(f"Payload: {payload}")

    try:
        # Branch created event
        if event == "create" and payload.get("ref_type") == "branch":
            branch_name = payload.get("ref")
            base_ref = payload.get("base_ref") or payload.get("base") or payload.get("default_branch")
            log.info(f"Processing branch creation: {branch_name}, base_ref: {base_ref}")
            # If created from develop, force In Progress status
            if base_ref and base_ref.lower() == "develop":
                log.info(f"Branch {branch_name} created from develop: setting Jira status to In Progress")
                process_branch(branch_name)  # process_branch already sets to In Progress if logic matches
            else:
                process_branch(branch_name)

        # Pull request events
        elif event == "pull_request":
            action = payload.get("action")
            pr_number = payload.get("pull_request", {}).get("number")
            log.info(f"Processing PR event: action={action}, pr_number={pr_number}")
            if action in ("opened", "ready_for_review", "edited", "reopened", "synchronize"):
                log.info(f"Calling process_pr_opened for PR {pr_number}")
                process_pr_opened({"id": pr_number})
            elif action == "closed":
                pr_data = payload.get("pull_request", {})
                if pr_data.get("merged"):
                    log.info(f"Calling process_pr_merged for PR {pr_number}")
                    process_pr_merged({"id": pr_number, "merged": True})

        return jsonify({"status": "processed"})
    except Exception as e:
        log.exception("Webhook error:")
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    app.run(port=3000, debug=True)