#!/usr/bin/env bash
# ══════════════════════════════════════════════════════════════════════
#  Generate terraform.tfvars from the repo-root .env file.
#
#  Usage:
#    cd wrappers/http-service/deploy/terraform
#    bash gen-tfvars.sh              # reads ../../.env by default
#    bash gen-tfvars.sh /path/.env   # or specify a custom .env
# ══════════════════════════════════════════════════════════════════════
set -euo pipefail

# Prevent Git Bash (MSYS) from converting /subscriptions/... to C:/Program Files/Git/subscriptions/...
export MSYS_NO_PATHCONV=1

ENV_FILE="${1:-$(git rev-parse --show-toplevel 2>/dev/null || echo '../../../..')/.env}"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "ERROR: .env file not found at $ENV_FILE" >&2
  exit 1
fi

# Source .env (strip quotes)
set -a
# shellcheck disable=SC1090
source <(sed 's/\r$//' "$ENV_FILE" | grep -v '^\s*#' | grep '=')
set +a

# ── Set active subscription ──────────────────────────────────────────
if [[ -n "${AZURE_SUBSCRIPTION_ID:-}" ]]; then
  az account set --subscription "${AZURE_SUBSCRIPTION_ID}" -o none
fi

# ── Resolve ARM resource IDs via az CLI ──────────────────────────────
echo "Resolving Azure resource IDs …" >&2

BLOB_ACCOUNT_ID=$(az storage account show \
  --name "${AZ_STORAGE_NAME}" \
  --resource-group "${AZ_STORAGE_RG}" \
  --query id -o tsv 2>/dev/null | tr -d '\r')
BLOB_ACCOUNT_ID="${BLOB_ACCOUNT_ID:-}"

AOAI_ACCOUNT_ID=$(az cognitiveservices account show \
  --name "${AZ_AOAI_RESOURCE_NAME}" \
  --resource-group "${AZ_AOAI_RESOURCE_RG}" \
  --query id -o tsv 2>/dev/null | tr -d '\r')
AOAI_ACCOUNT_ID="${AOAI_ACCOUNT_ID:-}"

CONTAINER_APP_ENV_ID=$(az containerapp env show \
  --name "${AZ_CONTAINER_APP_ENV_NAME}" \
  --resource-group "${AZ_CONTAINER_APP_ENV_RG}" \
  --query id -o tsv 2>/dev/null | tr -d '\r')
CONTAINER_APP_ENV_ID="${CONTAINER_APP_ENV_ID:-}"

ACR_LOGIN_SERVER=$(az acr show \
  --name "${AZ_ACR_NAME}" \
  --resource-group "${AZ_ACR_RG}" \
  --query loginServer -o tsv 2>/dev/null | tr -d '\r')
ACR_LOGIN_SERVER="${ACR_LOGIN_SERVER:-}"

ACR_ID=$(az acr show \
  --name "${AZ_ACR_NAME}" \
  --resource-group "${AZ_ACR_RG}" \
  --query id -o tsv 2>/dev/null | tr -d '\r')
ACR_ID="${ACR_ID:-}"

# ── Resolve Container App FQDN (if it already exists) ────────────────
CONTAINER_APP_FQDN=$(az containerapp show \
  --name "${AZ_CONTAINER_APP_NAME:-awreason-http-service}" \
  --resource-group "${AZ_CONTAINER_APP_ENV_RG}" \
  --query "properties.configuration.ingress.fqdn" -o tsv 2>/dev/null | tr -d '\r')
CONTAINER_APP_FQDN="${CONTAINER_APP_FQDN:-}"

# ── Resolve image tag (read from build, fallback to git SHA / timestamp) ──
SCRIPT_DIR_GEN="$(cd "$(dirname "$0")/.." && pwd)"
REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || cd "$(dirname "$0")/../../../.." && pwd)"
if [[ -z "${AZ_IMAGE_TAG:-}" ]] || [[ "${AZ_IMAGE_TAG}" == "latest" ]]; then
  if [[ -f "${SCRIPT_DIR_GEN}/.last_image_tag" ]] && [[ -s "${SCRIPT_DIR_GEN}/.last_image_tag" ]]; then
    RESOLVED_IMAGE_TAG=$(< "${SCRIPT_DIR_GEN}/.last_image_tag")
  else
    # Fallback: query ACR for the latest real tag (not :latest)
    RESOLVED_IMAGE_TAG=$(az acr repository show-tags \
      --name "${AZ_ACR_NAME}" \
      --repository awreason-http-service \
      --orderby time_desc --top 1 -o tsv 2>/dev/null | tr -d '\r')
    RESOLVED_IMAGE_TAG="${RESOLVED_IMAGE_TAG:-latest}"
  fi
else
  RESOLVED_IMAGE_TAG="${AZ_IMAGE_TAG}"
fi

# ── Resolve Application Insights connection string ───────────────────
APPINSIGHTS_CONNECTION_STRING=""
if [[ -n "${AZ_APPINSIGHTS_NAME:-}" ]] && [[ -n "${AZ_APPINSIGHTS_RG:-}" ]]; then
  APPINSIGHTS_CONNECTION_STRING=$(az resource show \
    --resource-type "Microsoft.Insights/components" \
    --name "${AZ_APPINSIGHTS_NAME}" \
    --resource-group "${AZ_APPINSIGHTS_RG}" \
    --query "properties.ConnectionString" -o tsv 2>/dev/null | tr -d '\r')
  APPINSIGHTS_CONNECTION_STRING="${APPINSIGHTS_CONNECTION_STRING:-}"
fi

# ── Write terraform.tfvars ───────────────────────────────────────────
TFVARS="terraform.tfvars"
cat > "$TFVARS" <<EOF
# Auto-generated from .env on $(date -u +"%Y-%m-%dT%H:%M:%SZ")
# Do NOT commit this file (contains resource IDs).

resource_group_name   = "${AZ_CONTAINER_APP_ENV_RG}"
location              = "${AZ_LOCATION}"

# Container Apps
container_app_env_id  = "${CONTAINER_APP_ENV_ID}"
acr_login_server      = "${ACR_LOGIN_SERVER}"
acr_id                = "${ACR_ID}"
image_tag             = "${RESOLVED_IMAGE_TAG}"

# Blob Storage
az_storage_name       = "${AZ_STORAGE_NAME}"
az_storage_rg         = "${AZ_STORAGE_RG}"
blob_account_id       = "${BLOB_ACCOUNT_ID}"

# Azure OpenAI
azure_openai_endpoint = "${AZURE_OPENAI_ENDPOINT}"
aoai_account_id       = "${AOAI_ACCOUNT_ID}"
aoai_deployment       = "${AZURE_OPENAI_DEPLOYMENT_O1:-o3}"
aoai_api_version      = "${AZURE_OPENAI_API_VERSION:-2024-12-01-preview}"

# APIM (leave empty to call AOAI directly)
apim_aoai_base_url    = ""

# Entra ID – API auth
aad_issuer            = "https://login.microsoftonline.com/${AZURE_TENANT_ID}/v2.0"
aad_audience          = "${AAD_CLIENT_ID}"

# Entra ID – Streamlit UX auth
aad_client_id         = "${AAD_CLIENT_ID}"
aad_client_secret     = "${AAD_CLIENT_SECRET}"
azure_tenant_id       = "${AZURE_TENANT_ID}"

# Azure identity & misc
azure_subscription_id = "${AZURE_SUBSCRIPTION_ID:-}"
auth_mode             = "${AUTH_MODE:-none}"
api_key               = "${API_KEY:-}"
log_level             = "${LOG_LEVEL:-INFO}"
streamlit_redirect_uri = "https://${CONTAINER_APP_FQDN}/"

# Application Insights
appinsights_connection_string = "${APPINSIGHTS_CONNECTION_STRING}"
EOF

echo "✅ Generated $TFVARS" >&2

# Warn about missing values
[[ -z "$CONTAINER_APP_ENV_ID" ]] && echo "⚠️  AZ_CONTAINER_APP_ENV_NAME not found – set it in .env and re-run" >&2
[[ -z "$ACR_LOGIN_SERVER" ]]     && echo "⚠️  AZ_ACR_NAME not found – set it in .env and re-run" >&2
[[ -z "$BLOB_ACCOUNT_ID" ]]      && echo "⚠️  Storage account '${AZ_STORAGE_NAME}' not found" >&2
[[ -z "$AOAI_ACCOUNT_ID" ]]      && echo "⚠️  AOAI resource '${AZ_AOAI_RESOURCE_NAME}' not found" >&2

exit 0
