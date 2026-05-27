FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY core.py config.py webhook_server.py ./

# All secrets injected at runtime via environment variables — never bake tokens into the image
ENV ADO_ORG=""
ENV ADO_PROJECT=""
ENV ADO_PAT=""
ENV JIRA_BASE_URL=""
ENV JIRA_EMAIL=""
ENV JIRA_API_TOKEN=""
ENV POLL_INTERVAL_MINUTES="5"

CMD ["python", "webhook_server.py"]
