#!/usr/bin/env bash
# Deploy all 4 ClaimPulse agents (3 Specialists + 1 Supervisor) to Vertex AI Agent Runtime
# and seed the remote Serverless Cloud Firestore database.
set -euo pipefail

PROJECT_ID="${1:-${GOOGLE_CLOUD_PROJECT:-}}"
REGION="${2:-${GOOGLE_CLOUD_LOCATION:-us-central1}}"

if [[ -z "${PROJECT_ID}" ]]; then
  echo "Usage: $0 <GCP_PROJECT_ID> [REGION]"
  exit 1
fi

echo "==> [1/5] Seeding Serverless Cloud Firestore in project ${PROJECT_ID}..."
GOOGLE_CLOUD_PROJECT="${PROJECT_ID}" uv run python -m app.storage.seeder --backend firestore --project "${PROJECT_ID}"

for ROLE in intake triage dispatch; do
  SERVICE_NAME="claimpulse-${ROLE}-agent"
  echo "==> Deploying Specialist Reasoning Engine: ${SERVICE_NAME} (role=${ROLE})..."
  agents-cli deploy \
    --project "${PROJECT_ID}" \
    --region "${REGION}" \
    --service-name "${SERVICE_NAME}" \
    --labels "app=claimpulse,role=specialist,specialist_type=${ROLE}" \
    --update-env-vars "CLAIM_PULSE_AGENT_ROLE=${ROLE},CLAIM_PULSE_STORAGE_BACKEND=firestore,CLAIM_PULSE_DEPLOYMENT_MODE=agent_runtime" \
    --no-confirm-project
done

echo "==> [5/5] Deploying Supervisor Reasoning Engine (claimpulse-agent) with Dynamic Agent Discovery..."
agents-cli deploy \
  --project "${PROJECT_ID}" \
  --region "${REGION}" \
  --service-name "claimpulse-agent" \
  --labels "app=claimpulse,role=supervisor" \
  --update-env-vars "CLAIM_PULSE_AGENT_ROLE=supervisor,CLAIM_PULSE_STORAGE_BACKEND=firestore,CLAIM_PULSE_DEPLOYMENT_MODE=agent_runtime" \
  --no-confirm-project

echo "✅ All 4 ClaimPulse Reasoning Engines deployed to Vertex AI Agent Runtime!"
