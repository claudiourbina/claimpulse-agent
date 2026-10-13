#!/usr/bin/env bash
# Deploy all 4 ClaimPulse agents (3 Specialists + 1 Supervisor) as separate
# Reasoning Engines on Vertex AI Agent Runtime and seed Cloud Firestore.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${REPO_ROOT}"

if [[ -f "${REPO_ROOT}/.venv.local" ]]; then
  # shellcheck disable=SC1091
  source "${REPO_ROOT}/.venv.local"
fi

export PATH="${HOME}/.local/bin:${PATH}"

PROJECT_ID="${1:-${GOOGLE_CLOUD_PROJECT:-fde-onboarding-projects}}"
REGION="${2:-us-central1}"

echo "=================================================================="
echo " ClaimPulse Multi-Agent Runtime Deployment"
echo " Project: ${PROJECT_ID} | Region: ${REGION}"
echo "=================================================================="

# Mint access token directly from GOOGLE_APPLICATION_CREDENTIALS so gcloud
# works seamlessly even if only application-default login was performed.
echo "==> [0/6] Authenticating via GOOGLE_APPLICATION_CREDENTIALS..."
CLOUDSDK_AUTH_ACCESS_TOKEN="$(uv run python -c "
import google.auth
import google.auth.transport.requests
creds, _ = google.auth.default(scopes=['https://www.googleapis.com/auth/cloud-platform'])
creds.refresh(google.auth.transport.requests.Request())
print(creds.token)
")"
export CLOUDSDK_AUTH_ACCESS_TOKEN

echo "==> [1/6] Enabling required Google Cloud APIs..."
gcloud services enable \
  aiplatform.googleapis.com \
  firestore.googleapis.com \
  cloudbuild.googleapis.com \
  cloudtrace.googleapis.com \
  logging.googleapis.com \
  bigquery.googleapis.com \
  --project="${PROJECT_ID}"

FIRESTORE_DB="${CLAIM_PULSE_FIRESTORE_DB:-claimpulse-db}"

echo "==> [2/6] Ensuring Serverless Cloud Firestore (Native Mode) database '${FIRESTORE_DB}' exists..."
if ! gcloud firestore databases list --project="${PROJECT_ID}" --format="value(name)" | grep -q "/databases/${FIRESTORE_DB}$"; then
  gcloud firestore databases create \
    --database="${FIRESTORE_DB}" \
    --location="${REGION}" \
    --type=firestore-native \
    --project="${PROJECT_ID}"
else
  echo "    Firestore database '${FIRESTORE_DB}' already exists."
fi

echo "==> [3/6] Seeding Cloud Firestore ('${FIRESTORE_DB}') with ClaimPulse policies, adjusters, workshops & tow units..."
CLAIM_PULSE_FIRESTORE_DB="${FIRESTORE_DB}" GOOGLE_CLOUD_PROJECT="${PROJECT_ID}" uv run python -m app.storage.seeder --backend firestore --project "${PROJECT_ID}"

deploy_role_engine() {
  local role="$1"
  local service_name="$2"
  local extra_env="$3"
  local meta_backup="deployment_metadata.${role}.json"

  # Refresh token before each 5-8 min Reasoning Engine deployment
  CLOUDSDK_AUTH_ACCESS_TOKEN="$(uv run python -c "
import google.auth
import google.auth.transport.requests
creds, _ = google.auth.default(scopes=['https://www.googleapis.com/auth/cloud-platform'])
creds.refresh(google.auth.transport.requests.Request())
print(creds.token)
")"
  export CLOUDSDK_AUTH_ACCESS_TOKEN

  if [[ -f "${meta_backup}" ]] && grep -q '"remote_agent_runtime_id"' "${meta_backup}"; then
    cp "${meta_backup}" deployment_metadata.json
  else
    rm -f "${meta_backup}"
    echo "{}" > deployment_metadata.json
  fi

  echo "==> Deploying Reasoning Engine '${service_name}' (role=${role})..."
  agents-cli deploy \
    --project "${PROJECT_ID}" \
    --region "${REGION}" \
    --service-name "${service_name}" \
    --labels "app=claimpulse,role=${role}" \
    --update-env-vars "CLAIM_PULSE_AGENT_ROLE=${role},CLAIM_PULSE_STORAGE_BACKEND=firestore,CLAIM_PULSE_FIRESTORE_DB=${FIRESTORE_DB},CLAIM_PULSE_DEPLOYMENT_MODE=agent_runtime${extra_env}" \
    --no-confirm-project

  cp deployment_metadata.json "${meta_backup}"
}

echo "==> [4/6] Deploying the 3 Specialist Reasoning Engines to Vertex AI Agent Runtime..."
deploy_role_engine "intake" "claimpulse-intake-agent" ""
INTAKE_ID="$(uv run python -c "import json; print(json.load(open('deployment_metadata.intake.json')).get('remote_agent_runtime_id', ''))")"

deploy_role_engine "triage" "claimpulse-triage-agent" ""
TRIAGE_ID="$(uv run python -c "import json; print(json.load(open('deployment_metadata.triage.json')).get('remote_agent_runtime_id', ''))")"

deploy_role_engine "dispatch" "claimpulse-dispatch-agent" ""
DISPATCH_ID="$(uv run python -c "import json; print(json.load(open('deployment_metadata.dispatch.json')).get('remote_agent_runtime_id', ''))")"

echo "==> [5/6] Deploying Supervisor Reasoning Engine (claimpulse-agent) wired to discovered Specialists..."
SUPERVISOR_EXTRA_ENV=",CLAIM_PULSE_INTAKE_ENGINE_ID=${INTAKE_ID},CLAIM_PULSE_TRIAGE_ENGINE_ID=${TRIAGE_ID},CLAIM_PULSE_DISPATCH_ENGINE_ID=${DISPATCH_ID}"
deploy_role_engine "supervisor" "claimpulse-agent" "${SUPERVISOR_EXTRA_ENV}"

echo "==> [6/6] Deployment Summary:"
echo "  - Intake Specialist   : ${INTAKE_ID}"
echo "  - Triage Specialist   : ${TRIAGE_ID}"
echo "  - Dispatch Specialist : ${DISPATCH_ID}"
SUPERVISOR_ID="$(uv run python -c "import json; print(json.load(open('deployment_metadata.supervisor.json')).get('remote_agent_runtime_id', ''))")"
echo "  - Supervisor Agent    : ${SUPERVISOR_ID}"
echo "✅ All 4 ClaimPulse Reasoning Engines are live on Vertex AI Agent Runtime!"
