#!/usr/bin/env bash
# ══════════════════════════════════════════════════════════════════════
#  Build, push, and deploy the awreason queue worker to Azure
#  Container Apps using wrappers/queue-worker/deploy/aca-containerapp.yaml.
#
#  Configuration is sourced from the repo-root .env_qa file.
#
#  Usage:
#    cd wrappers/queue-worker/deploy
#    bash deploy.sh preview      # show resolved deployment inputs
#    bash deploy.sh infra        # ensure Azure dependencies exist
#    bash deploy.sh build        # build & push worker image to ACR
#    bash deploy.sh yaml         # render and deploy the ACA YAML
#    bash deploy.sh all          # infra + build + yaml
# ══════════════════════════════════════════════════════════════════════
set -euo pipefail

export MSYS_NO_PATHCONV=1

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
ENV_FILE="${ENV_FILE:-${REPO_ROOT}/.env_qa}"
RESOLVED_YAML="${SCRIPT_DIR}/_resolved-aca.yaml"
LAST_TAG_FILE="${SCRIPT_DIR}/.last_image_tag"
IMAGE_NAME="awreason-queue-worker"
ACTION="${1:-all}"
RESOURCE_TOKEN="${RESOURCE_TOKEN:-}"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "ERROR: env file not found at $ENV_FILE" >&2
  exit 1
fi

set -a
# shellcheck disable=SC1090
source <(sed 's/\r$//' "$ENV_FILE" | grep -v '^\s*#' | grep '=')
set +a

require_command() {
  local command_name="$1"
  if ! command -v "$command_name" >/dev/null 2>&1; then
    echo "ERROR: required command not found: $command_name" >&2
    exit 1
  fi
}

require_env() {
  local var_name="$1"
  if [[ -z "${!var_name:-}" ]]; then
    echo "ERROR: $var_name must be set in $ENV_FILE" >&2
    exit 1
  fi
}

resource_exists() {
  "$@" &>/dev/null
}

is_reuse() {
  local val="${!1:-FALSE}"
  [[ "${val^^}" == "TRUE" ]]
}

reuse_mutations_allowed() {
  local val="${ALLOW_REUSE_RESOURCE_MUTATIONS:-FALSE}"
  [[ "${val^^}" == "TRUE" ]]
}

reuse_resource_protected() {
  local reuse_flag="$1"
  is_reuse "$reuse_flag" && ! reuse_mutations_allowed
}

is_vnet_enabled() {
  local val="${AZ_VNET_ENABLED:-FALSE}"
  [[ "${val^^}" == "TRUE" ]]
}

update_env_var() {
  local var_name="$1"
  local var_value="$2"
  if grep -q "^${var_name}=" "$ENV_FILE" 2>/dev/null; then
    sed -i "s|^${var_name}=.*|${var_name}=${var_value}|" "$ENV_FILE"
  else
    printf '%s=%s\n' "$var_name" "$var_value" >> "$ENV_FILE"
  fi
  export "${var_name}=${var_value}"
}

generate_token() {
  if [[ -n "$RESOURCE_TOKEN" ]]; then
    printf '%s' "$RESOURCE_TOKEN"
    return
  fi
  printf '%s' "$(echo -n "${AZURE_SUBSCRIPTION_ID:-$(date +%s)}-${IMAGE_NAME}" | md5sum | cut -c1-5)"
}

generate_name() {
  local prefix="$1"
  printf '%s%s' "$prefix" "$(generate_token)"
}

escape_sed_replacement() {
  printf '%s' "$1" | sed -e 's/[&|\\]/\\&/g'
}

require_command az
require_command git

require_env AZURE_SUBSCRIPTION_ID
az account set --subscription "${AZURE_SUBSCRIPTION_ID}" -o none

echo "Reuse protection: ALLOW_REUSE_RESOURCE_MUTATIONS=${ALLOW_REUSE_RESOURCE_MUTATIONS:-FALSE}"
echo "  Existing resources with *_REUSE=TRUE are never deleted by this script."
echo "  Mutations against reused resources are blocked unless this flag is TRUE."

ensure_resource_group() {
  local rg="$1"
  local loc="${2:-${AZ_LOCATION:-eastus2}}"
  if ! resource_exists az group show --name "$rg"; then
    echo "  Creating resource group: $rg in $loc"
    az group create --name "$rg" --location "$loc" --yes -o none
  fi
}

ensure_storage() {
  echo ""
  echo "── Storage Account ─────────────────────────────────────────"

  local sa_name="${AZ_STORAGE_NAME:-}"
  local sa_rg="${AZ_STORAGE_RG:-${AZ_CORE_RG_NAME:-}}"

  if is_reuse AZ_STORAGE_REUSE; then
    require_env AZ_STORAGE_NAME
    require_env AZ_STORAGE_RG
    if ! resource_exists az storage account show --name "$sa_name" --resource-group "$sa_rg"; then
      echo "ERROR: Storage account '$sa_name' not found in RG '$sa_rg' but AZ_STORAGE_REUSE=TRUE." >&2
      exit 1
    fi
    return
  fi

  if [[ -z "$sa_rg" ]]; then
    echo "ERROR: AZ_STORAGE_RG must be set when AZ_STORAGE_REUSE=FALSE" >&2
    exit 1
  fi
  if [[ -z "$sa_name" ]]; then
    sa_name="$(generate_name st)"
    echo "  No AZ_STORAGE_NAME set – generated: $sa_name"
  fi

  ensure_resource_group "$sa_rg" "${AZ_LOCATION}"

  if resource_exists az storage account show --name "$sa_name" --resource-group "$sa_rg"; then
    echo "  Storage account '$sa_name' already exists – reusing."
  else
    echo "  Creating storage account: $sa_name"
    az storage account create \
      --name "$sa_name" \
      --resource-group "$sa_rg" \
      --location "${AZ_LOCATION}" \
      --sku Standard_LRS \
      --kind StorageV2 \
      -o none
    echo "  ✅ Storage account created."
  fi

  update_env_var "AZ_STORAGE_NAME" "$sa_name"
  update_env_var "AZ_STORAGE_RG" "$sa_rg"
}

ensure_acr() {
  echo ""
  echo "── Azure Container Registry ───────────────────────────────"

  local acr_name="${AZ_ACR_NAME:-}"
  local acr_rg="${AZ_ACR_RG:-${AZ_CORE_RG_NAME:-}}"

  if is_reuse AZ_ACR_REUSE; then
    require_env AZ_ACR_NAME
    require_env AZ_ACR_RG
    if ! resource_exists az acr show --name "$acr_name" --resource-group "$acr_rg"; then
      echo "ERROR: ACR '$acr_name' not found in RG '$acr_rg' but AZ_ACR_REUSE=TRUE." >&2
      exit 1
    fi
    return
  fi

  if [[ -z "$acr_rg" ]]; then
    echo "ERROR: AZ_ACR_RG must be set when AZ_ACR_REUSE=FALSE" >&2
    exit 1
  fi
  if [[ -z "$acr_name" ]]; then
    acr_name="$(generate_name acr)"
    echo "  No AZ_ACR_NAME set – generated: $acr_name"
  fi

  ensure_resource_group "$acr_rg" "${AZ_LOCATION}"

  if resource_exists az acr show --name "$acr_name" --resource-group "$acr_rg"; then
    echo "  ACR '$acr_name' already exists – reusing."
  else
    echo "  Creating ACR: $acr_name"
    az acr create \
      --name "$acr_name" \
      --resource-group "$acr_rg" \
      --location "${AZ_LOCATION}" \
      --sku Basic \
      --admin-enabled true \
      -o none
    echo "  ✅ ACR created."
  fi

  update_env_var "AZ_ACR_NAME" "$acr_name"
  update_env_var "AZ_ACR_RG" "$acr_rg"
}

ensure_log_analytics() {
  echo ""
  echo "── Log Analytics Workspace ─────────────────────────────────"

  local law_name="${AZ_LOGANALYTICS_NAME:-}"
  local law_rg="${AZ_LOGANALYTICS_RG:-${AZ_CONTAINER_APP_ENV_RG:-${AZ_CORE_RG_NAME:-}}}"

  if is_reuse AZ_LOGANALYTICS_REUSE; then
    require_env AZ_LOGANALYTICS_NAME
    require_env AZ_LOGANALYTICS_RG
    if ! resource_exists az monitor log-analytics workspace show --workspace-name "$law_name" --resource-group "$law_rg"; then
      echo "ERROR: Log Analytics workspace '$law_name' not found in RG '$law_rg' but AZ_LOGANALYTICS_REUSE=TRUE." >&2
      exit 1
    fi
    return
  fi

  if [[ -z "$law_rg" ]]; then
    echo "ERROR: AZ_LOGANALYTICS_RG must be set when AZ_LOGANALYTICS_REUSE=FALSE" >&2
    exit 1
  fi
  if [[ -z "$law_name" ]]; then
    law_name="$(generate_name law)"
    echo "  No AZ_LOGANALYTICS_NAME set – generated: $law_name"
  fi

  ensure_resource_group "$law_rg" "${AZ_LOCATION}"

  if resource_exists az monitor log-analytics workspace show --workspace-name "$law_name" --resource-group "$law_rg"; then
    echo "  Log Analytics workspace '$law_name' already exists – reusing."
  else
    echo "  Creating Log Analytics workspace: $law_name"
    az monitor log-analytics workspace create \
      --workspace-name "$law_name" \
      --resource-group "$law_rg" \
      --location "${AZ_LOCATION}" \
      -o none
    echo "  ✅ Log Analytics workspace created."
  fi

  update_env_var "AZ_LOGANALYTICS_NAME" "$law_name"
  update_env_var "AZ_LOGANALYTICS_RG" "$law_rg"
}

ensure_vnet() {
  echo ""
  echo "── Virtual Network ─────────────────────────────────────────"

  if ! is_vnet_enabled; then
    echo "  AZ_VNET_ENABLED=FALSE → skipping VNet setup."
    return
  fi

  local vnet_name="${AZ_VNET_NAME:-}"
  local vnet_rg="${AZ_VNET_RG:-${AZ_CONTAINER_APP_ENV_RG:-${AZ_CORE_RG_NAME:-}}}"
  local subnet_name="${AZ_VNET_SUBNET_NAME:-snet-aca}"
  local vnet_prefix="${AZ_VNET_ADDRESS_PREFIX:-10.200.0.0/16}"
  local subnet_prefix="${AZ_VNET_SUBNET_PREFIX:-10.200.0.0/23}"

  if is_reuse AZ_VNET_REUSE; then
    require_env AZ_VNET_NAME
    if ! resource_exists az network vnet show --name "$vnet_name" --resource-group "$vnet_rg"; then
      echo "ERROR: VNet '$vnet_name' not found in RG '$vnet_rg' but AZ_VNET_REUSE=TRUE." >&2
      exit 1
    fi
    if ! resource_exists az network vnet subnet show --vnet-name "$vnet_name" --name "$subnet_name" --resource-group "$vnet_rg"; then
      echo "ERROR: subnet '$subnet_name' not found in VNet '$vnet_name'." >&2
      exit 1
    fi
    return
  fi

  if [[ -z "$vnet_rg" ]]; then
    echo "ERROR: AZ_VNET_RG must be set when AZ_VNET_REUSE=FALSE" >&2
    exit 1
  fi
  if [[ -z "$vnet_name" ]]; then
    vnet_name="$(generate_name vnet)"
    echo "  No AZ_VNET_NAME set – generated: $vnet_name"
  fi

  ensure_resource_group "$vnet_rg" "${AZ_LOCATION}"

  if resource_exists az network vnet show --name "$vnet_name" --resource-group "$vnet_rg"; then
    echo "  VNet '$vnet_name' already exists – reusing."
  else
    echo "  Creating VNet: $vnet_name"
    az network vnet create \
      --name "$vnet_name" \
      --resource-group "$vnet_rg" \
      --location "${AZ_LOCATION}" \
      --address-prefixes "$vnet_prefix" \
      -o none
  fi

  if resource_exists az network vnet subnet show --vnet-name "$vnet_name" --name "$subnet_name" --resource-group "$vnet_rg"; then
    echo "  Subnet '$subnet_name' already exists."
  else
    echo "  Creating ACA subnet: $subnet_name"
    az network vnet subnet create \
      --vnet-name "$vnet_name" \
      --name "$subnet_name" \
      --resource-group "$vnet_rg" \
      --address-prefixes "$subnet_prefix" \
      --delegations Microsoft.App/environments \
      --service-endpoints Microsoft.Storage \
      -o none
  fi

  update_env_var "AZ_VNET_NAME" "$vnet_name"
  update_env_var "AZ_VNET_RG" "$vnet_rg"
  update_env_var "AZ_VNET_SUBNET_NAME" "$subnet_name"
}

ensure_container_app_env() {
  echo ""
  echo "── Container Apps Environment ──────────────────────────────"

  local env_name="${AZ_CONTAINER_APP_ENV_NAME:-}"
  local env_rg="${AZ_CONTAINER_APP_ENV_RG:-${AZ_CORE_RG_NAME:-}}"

  if is_reuse AZ_CONTAINER_APP_ENV_REUSE; then
    require_env AZ_CONTAINER_APP_ENV_NAME
    require_env AZ_CONTAINER_APP_ENV_RG
    if ! resource_exists az containerapp env show --name "$env_name" --resource-group "$env_rg"; then
      echo "ERROR: Container Apps Environment '$env_name' not found in RG '$env_rg' but AZ_CONTAINER_APP_ENV_REUSE=TRUE." >&2
      exit 1
    fi
    return
  fi

  if [[ -z "$env_rg" ]]; then
    echo "ERROR: AZ_CONTAINER_APP_ENV_RG must be set when AZ_CONTAINER_APP_ENV_REUSE=FALSE" >&2
    exit 1
  fi
  if [[ -z "$env_name" ]]; then
    env_name="$(generate_name cae)"
    echo "  No AZ_CONTAINER_APP_ENV_NAME set – generated: $env_name"
  fi

  ensure_resource_group "$env_rg" "${AZ_LOCATION}"

  local law_customer_id=""
  local law_shared_key=""
  if [[ -n "${AZ_LOGANALYTICS_NAME:-}" ]] && [[ -n "${AZ_LOGANALYTICS_RG:-}" ]]; then
    law_customer_id=$(az monitor log-analytics workspace show \
      --workspace-name "${AZ_LOGANALYTICS_NAME}" \
      --resource-group "${AZ_LOGANALYTICS_RG}" \
      --query customerId -o tsv 2>/dev/null | tr -d '\r' || true)
    law_shared_key=$(az monitor log-analytics workspace get-shared-keys \
      --workspace-name "${AZ_LOGANALYTICS_NAME}" \
      --resource-group "${AZ_LOGANALYTICS_RG}" \
      --query primarySharedKey -o tsv 2>/dev/null | tr -d '\r' || true)
  fi

  if resource_exists az containerapp env show --name "$env_name" --resource-group "$env_rg"; then
    echo "  Container Apps Environment '$env_name' already exists – reusing."
  else
    echo "  Creating Container Apps Environment: $env_name"
    local create_args=(
      az containerapp env create
      --name "$env_name"
      --resource-group "$env_rg"
      --location "${AZ_LOCATION}"
      -o none
    )
    if [[ -n "$law_customer_id" ]] && [[ -n "$law_shared_key" ]]; then
      create_args+=(--logs-workspace-id "$law_customer_id" --logs-workspace-key "$law_shared_key")
    fi
    if is_vnet_enabled; then
      local subnet_id=""
      subnet_id=$(az network vnet subnet show \
        --vnet-name "${AZ_VNET_NAME}" \
        --name "${AZ_VNET_SUBNET_NAME:-snet-aca}" \
        --resource-group "${AZ_VNET_RG:-$env_rg}" \
        --query id -o tsv 2>/dev/null | tr -d '\r' || true)
      if [[ -n "$subnet_id" ]]; then
        create_args+=(--infrastructure-subnet-resource-id "$subnet_id")
      fi
    fi
    "${create_args[@]}"
    echo "  ✅ Container Apps Environment created."
  fi

  update_env_var "AZ_CONTAINER_APP_ENV_NAME" "$env_name"
  update_env_var "AZ_CONTAINER_APP_ENV_RG" "$env_rg"
}

ensure_servicebus() {
  echo ""
  echo "── Azure Service Bus ───────────────────────────────────────"

  local sb_name="${AZ_SERVICE_BUS_NAME:-}"
  local sb_rg="${AZ_SERVICE_BUS_RG:-${AZ_CORE_RG_NAME:-}}"

  if is_reuse AZ_SERVICE_BUS_REUSE; then
    require_env AZ_SERVICE_BUS_NAME
    require_env AZ_SERVICE_BUS_RG
    if ! resource_exists az servicebus namespace show --name "$sb_name" --resource-group "$sb_rg"; then
      echo "ERROR: Service Bus namespace '$sb_name' not found in RG '$sb_rg' but AZ_SERVICE_BUS_REUSE=TRUE." >&2
      exit 1
    fi
  else
    if [[ -z "$sb_rg" ]]; then
      echo "ERROR: AZ_SERVICE_BUS_RG must be set when AZ_SERVICE_BUS_REUSE=FALSE" >&2
      exit 1
    fi
    if [[ -z "$sb_name" ]]; then
      sb_name="$(generate_name sb)"
      echo "  No AZ_SERVICE_BUS_NAME set – generated: $sb_name"
    fi
    ensure_resource_group "$sb_rg" "${AZ_LOCATION}"
    if resource_exists az servicebus namespace show --name "$sb_name" --resource-group "$sb_rg"; then
      echo "  Service Bus namespace '$sb_name' already exists – reusing."
    else
      echo "  Creating Service Bus namespace: $sb_name"
      az servicebus namespace create \
        --name "$sb_name" \
        --resource-group "$sb_rg" \
        --location "${AZ_LOCATION}" \
        --sku Standard \
        -o none
      echo "  ✅ Service Bus namespace created."
    fi
    update_env_var "AZ_SERVICE_BUS_NAME" "$sb_name"
    update_env_var "AZ_SERVICE_BUS_RG" "$sb_rg"
    update_env_var "SB_NAMESPACE" "${sb_name}.servicebus.windows.net"
    update_env_var "SbConnection__fullyQualifiedNamespace" "${sb_name}.servicebus.windows.net"
  fi

  require_env SB_QUEUE
  if resource_exists az servicebus queue show --name "$SB_QUEUE" --namespace-name "$sb_name" --resource-group "$sb_rg"; then
    echo "  Queue '$SB_QUEUE' already exists."
  else
    echo "  Creating queue: $SB_QUEUE"
    az servicebus queue create \
      --name "$SB_QUEUE" \
      --namespace-name "$sb_name" \
      --resource-group "$sb_rg" \
      -o none
  fi

  if [[ "${REPORT_MODE:-}" == "servicebus" ]]; then
    require_env SB_RESULTS_QUEUE
    if resource_exists az servicebus queue show --name "$SB_RESULTS_QUEUE" --namespace-name "$sb_name" --resource-group "$sb_rg"; then
      echo "  Queue '$SB_RESULTS_QUEUE' already exists."
    else
      echo "  Creating queue: $SB_RESULTS_QUEUE"
      az servicebus queue create \
        --name "$SB_RESULTS_QUEUE" \
        --namespace-name "$sb_name" \
        --resource-group "$sb_rg" \
        -o none
    fi
  fi
}

ensure_managed_identity() {
  echo ""
  echo "── User-Assigned Managed Identity ──────────────────────────"

  local mi_name="${AZ_MI_NAME:-id-awreason-http-service}"
  local mi_rg="${AZ_CONTAINER_APP_ENV_RG:-${AZ_CORE_RG_NAME:-}}"

  ensure_resource_group "$mi_rg" "${AZ_LOCATION}"

  if resource_exists az identity show --name "$mi_name" --resource-group "$mi_rg"; then
    echo "  Managed identity '$mi_name' already exists – reusing."
  else
    echo "  Creating managed identity: $mi_name"
    az identity create \
      --name "$mi_name" \
      --resource-group "$mi_rg" \
      --location "${AZ_LOCATION}" \
      -o none
    echo "  ✅ Managed identity created."
  fi

  local mi_resource_id mi_client_id mi_principal_id
  mi_resource_id=$(az identity show --name "$mi_name" --resource-group "$mi_rg" --query id -o tsv | tr -d '\r')
  mi_client_id=$(az identity show --name "$mi_name" --resource-group "$mi_rg" --query clientId -o tsv | tr -d '\r')
  mi_principal_id=$(az identity show --name "$mi_name" --resource-group "$mi_rg" --query principalId -o tsv | tr -d '\r')

  update_env_var "AZ_MI_NAME" "$mi_name"
  update_env_var "AZ_MI_RESOURCE_ID" "$mi_resource_id"
  update_env_var "AZ_MI_CLIENT_ID" "$mi_client_id"
  update_env_var "AZ_MI_PRINCIPAL_ID" "$mi_principal_id"
}

ensure_role_assignment() {
  local principal_id="$1"
  local role_name="$2"
  local scope="$3"
  local reuse_flag="${4:-}"

  if az role assignment list \
    --assignee-object-id "$principal_id" \
    --assignee-principal-type ServicePrincipal \
    --scope "$scope" \
    --role "$role_name" \
    --query "[0].id" -o tsv 2>/dev/null | tr -d '\r' | grep -q .; then
    echo "  Role already assigned: $role_name"
  else
    if [[ -n "$reuse_flag" ]] && reuse_resource_protected "$reuse_flag"; then
      echo "ERROR: role '$role_name' is missing on a reused resource guarded by $reuse_flag. Set ALLOW_REUSE_RESOURCE_MUTATIONS=TRUE to let deploy.sh add it, or create the role assignment manually." >&2
      exit 1
    fi
    az role assignment create \
      --assignee-object-id "$principal_id" \
      --assignee-principal-type ServicePrincipal \
      --role "$role_name" \
      --scope "$scope" \
      -o none
    echo "  ✅ Role assigned: $role_name"
  fi
}

ensure_identity_roles() {
  echo ""
  echo "── Managed Identity Role Assignments ───────────────────────"

  require_env AZ_MI_PRINCIPAL_ID
  require_env AZ_MI_RESOURCE_ID
  require_env AZ_ACR_NAME
  require_env AZ_ACR_RG
  require_env AZ_STORAGE_NAME
  require_env AZ_STORAGE_RG
  require_env AZ_SERVICE_BUS_NAME
  require_env AZ_SERVICE_BUS_RG

  local acr_id storage_id sb_id aoai_id
  acr_id=$(az acr show --name "${AZ_ACR_NAME}" --resource-group "${AZ_ACR_RG}" --query id -o tsv | tr -d '\r')
  storage_id=$(az storage account show --name "${AZ_STORAGE_NAME}" --resource-group "${AZ_STORAGE_RG}" --query id -o tsv | tr -d '\r')
  sb_id=$(az servicebus namespace show --name "${AZ_SERVICE_BUS_NAME}" --resource-group "${AZ_SERVICE_BUS_RG}" --query id -o tsv | tr -d '\r')

  ensure_role_assignment "${AZ_MI_PRINCIPAL_ID}" "AcrPull" "$acr_id" "AZ_ACR_REUSE"
  ensure_role_assignment "${AZ_MI_PRINCIPAL_ID}" "Storage Blob Data Contributor" "$storage_id" "AZ_STORAGE_REUSE"
  ensure_role_assignment "${AZ_MI_PRINCIPAL_ID}" "Azure Service Bus Data Receiver" "$sb_id" "AZ_SERVICE_BUS_REUSE"

  if [[ "${REPORT_MODE:-}" == "servicebus" ]]; then
    ensure_role_assignment "${AZ_MI_PRINCIPAL_ID}" "Azure Service Bus Data Sender" "$sb_id" "AZ_SERVICE_BUS_REUSE"
  fi

  if [[ -n "${AZ_AOAI_RESOURCE_NAME:-}" ]] && [[ -n "${AZ_AOAI_RESOURCE_RG:-}" ]]; then
    aoai_id=$(az cognitiveservices account show \
      --name "${AZ_AOAI_RESOURCE_NAME}" \
      --resource-group "${AZ_AOAI_RESOURCE_RG}" \
      --query id -o tsv 2>/dev/null | tr -d '\r' || true)
    if [[ -n "$aoai_id" ]]; then
      ensure_role_assignment "${AZ_MI_PRINCIPAL_ID}" "Cognitive Services OpenAI User" "$aoai_id" "AZ_AOAI_REUSE"
    fi
  fi
}

ensure_results_container() {
  echo ""
  echo "── Blob Container ──────────────────────────────────────────"

  require_env AZ_STORAGE_NAME
  require_env AZ_STORAGE_RG
  require_env BLOB_RESULTS_CONTAINER

  local container_exists=""
  container_exists=$(az storage container exists \
    --auth-mode login \
    --account-name "${AZ_STORAGE_NAME}" \
    --name "${BLOB_RESULTS_CONTAINER}" \
    --query exists -o tsv 2>/dev/null | tr -d '\r' || true)

  if [[ "$container_exists" == "true" ]]; then
    echo "  Blob container '${BLOB_RESULTS_CONTAINER}' already exists."
  else
    if reuse_resource_protected AZ_STORAGE_REUSE; then
      echo "ERROR: blob container '${BLOB_RESULTS_CONTAINER}' is missing in reused storage account '${AZ_STORAGE_NAME}'. Set ALLOW_REUSE_RESOURCE_MUTATIONS=TRUE to let deploy.sh create it, or create it manually first." >&2
      exit 1
    fi
    az storage container create \
      --auth-mode login \
      --account-name "${AZ_STORAGE_NAME}" \
      --name "${BLOB_RESULTS_CONTAINER}" \
      --public-access off \
      -o none
    echo "  ✅ Blob container created: ${BLOB_RESULTS_CONTAINER}"
  fi
}

ensure_storage_firewall_allows_aca() {
  echo ""
  echo "── Storage Firewall: allow ACA access ──────────────────────"

  if [[ -z "${AZ_STORAGE_NAME:-}" ]] || [[ -z "${AZ_CONTAINER_APP_ENV_NAME:-}" ]]; then
    echo "  Storage or ACA environment not set – skipping firewall rule."
    return
  fi

  if reuse_resource_protected AZ_STORAGE_REUSE || reuse_resource_protected AZ_VNET_REUSE || reuse_resource_protected AZ_CONTAINER_APP_ENV_REUSE; then
    echo "  Reuse protection is active – skipping storage firewall mutations for reused resources."
    return
  fi

  if is_vnet_enabled; then
    local subnet_id=""
    subnet_id=$(az network vnet subnet show \
      --vnet-name "${AZ_VNET_NAME}" \
      --name "${AZ_VNET_SUBNET_NAME:-snet-aca}" \
      --resource-group "${AZ_VNET_RG:-${AZ_CONTAINER_APP_ENV_RG}}" \
      --query id -o tsv 2>/dev/null | tr -d '\r' || true)
    if [[ -z "$subnet_id" ]]; then
      echo "  VNet subnet not found – skipping storage firewall rule."
      return
    fi

    local existing_vnet_rule=""
    existing_vnet_rule=$(az storage account network-rule list \
      --account-name "${AZ_STORAGE_NAME}" \
      --resource-group "${AZ_STORAGE_RG}" \
      --query "virtualNetworkRules[?virtualNetworkResourceId=='${subnet_id}'].virtualNetworkResourceId" \
      -o tsv 2>/dev/null | tr -d '\r' || true)
    if [[ -z "$existing_vnet_rule" ]]; then
      az storage account network-rule add \
        --account-name "${AZ_STORAGE_NAME}" \
        --resource-group "${AZ_STORAGE_RG}" \
        --subnet "$subnet_id" \
        -o none
      echo "  ✅ Added subnet rule for ACA access."
    else
      echo "  ACA subnet rule already exists."
    fi

    local default_action=""
    default_action=$(az storage account show \
      --name "${AZ_STORAGE_NAME}" \
      --resource-group "${AZ_STORAGE_RG}" \
      --query "networkRuleSet.defaultAction" -o tsv 2>/dev/null | tr -d '\r' || true)
    if [[ "$default_action" != "Deny" ]]; then
      az storage account update \
        --name "${AZ_STORAGE_NAME}" \
        --resource-group "${AZ_STORAGE_RG}" \
        --default-action Deny \
        -o none
      echo "  ✅ Storage firewall set to Deny with ACA subnet allowlist."
    else
      echo "  Storage firewall already set to Deny."
    fi
  else
    echo "  AZ_VNET_ENABLED=FALSE → skipping storage firewall changes."
  fi
}

refresh_derived() {
  CONTAINER_APP_NAME="${AZ_CONTAINER_APP_NAME_Q_WORKER:-awreason-queue-worker}"

  ACR_LOGIN_SERVER=$(az acr show \
    --name "${AZ_ACR_NAME}" \
    --resource-group "${AZ_ACR_RG}" \
    --query loginServer -o tsv 2>/dev/null | tr -d '\r' || true)
  ACR_LOGIN_SERVER="${ACR_LOGIN_SERVER:-${AZ_ACR_NAME}.azurecr.io}"

  if [[ -n "${AZ_IMAGE_TAG:-}" ]] && [[ "${AZ_IMAGE_TAG}" != "latest" ]]; then
    IMAGE_TAG="${AZ_IMAGE_TAG}"
  elif [[ -s "$LAST_TAG_FILE" ]]; then
    IMAGE_TAG="$(< "$LAST_TAG_FILE")"
  else
    IMAGE_TAG=$(git -C "$REPO_ROOT" rev-parse --short=8 HEAD 2>/dev/null || date -u +"%Y%m%dT%H%M%S")
  fi
  IMAGE_TAG="${IMAGE_TAG//$'\r'/}"
  IMAGE_TAG="${IMAGE_TAG//[$'\t\n ']/}"
  if [[ -z "$IMAGE_TAG" ]]; then
    IMAGE_TAG=$(git -C "$REPO_ROOT" rev-parse --short=8 HEAD 2>/dev/null || date -u +"%Y%m%dT%H%M%S")
  fi

  CONTAINER_APP_ENV_ID=$(az containerapp env show \
    --name "${AZ_CONTAINER_APP_ENV_NAME}" \
    --resource-group "${AZ_CONTAINER_APP_ENV_RG}" \
    --query id -o tsv 2>/dev/null | tr -d '\r' || true)

  MANAGED_IDENTITY_RESOURCE_ID="${AZ_MI_RESOURCE_ID:-}"
  if [[ -z "$MANAGED_IDENTITY_RESOURCE_ID" ]]; then
    MANAGED_IDENTITY_RESOURCE_ID=$(az identity show \
      --name "${AZ_MI_NAME}" \
      --resource-group "${AZ_CONTAINER_APP_ENV_RG}" \
      --query id -o tsv 2>/dev/null | tr -d '\r' || true)
  fi

  BLOB_ACCOUNT_URL="${BLOB_ACCOUNT_URL:-https://${AZ_STORAGE_NAME}.blob.core.windows.net}"
  SB_NAMESPACE_NAME="${SB_NAMESPACE_NAME:-${SB_NAMESPACE%.servicebus.windows.net}}"
  REPORT_MODE="${REPORT_MODE:-${ENGINE_REPORT_MODE:-servicebus}}"
  SB_QUEUE="${SB_QUEUE:-${SB_RUNS_QUEUE:-engine-runs}}"
  BLOB_RESULTS_CONTAINER="${BLOB_RESULTS_CONTAINER:-results}"
  BLOB_RESULTS_PREFIX="${BLOB_RESULTS_PREFIX:-runs}"
  AOAI_DEPLOYMENT="${AOAI_DEPLOYMENT:-${AZURE_OPENAI_DEPLOYMENT_O1:-}}"
  AOAI_API_VERSION="${AOAI_API_VERSION:-${AZURE_OPENAI_API_VERSION:-}}"
  LOG_LEVEL="${LOG_LEVEL:-INFO}"
  PER_REPLICA_CONCURRENCY="${PER_REPLICA_CONCURRENCY:-1}"
  AWREASON_CLI_TIMEOUT="${AWREASON_CLI_TIMEOUT:-300}"
  AWREASON_MAX_RETRIES="${AWREASON_MAX_RETRIES:-3}"
  AWREASON_RETRY_BACKOFF="${AWREASON_RETRY_BACKOFF:-10}"
  QUEUE_WORKER_MIN_REPLICAS="${QUEUE_WORKER_MIN_REPLICAS:-1}"
  QUEUE_WORKER_MAX_REPLICAS="${QUEUE_WORKER_MAX_REPLICAS:-50}"
  QUEUE_WORKER_POLLING_INTERVAL="${QUEUE_WORKER_POLLING_INTERVAL:-30}"
  QUEUE_WORKER_COOLDOWN_PERIOD="${QUEUE_WORKER_COOLDOWN_PERIOD:-300}"
  QUEUE_WORKER_MESSAGE_COUNT="${QUEUE_WORKER_MESSAGE_COUNT:-5}"
  PLATFORM_API_BASE_URL="${PLATFORM_API_BASE_URL:-}"
  PLATFORM_AUDIENCE="${PLATFORM_AUDIENCE:-}"
  APIM_AOAI_BASE_URL="${APIM_AOAI_BASE_URL:-}"
  OTEL_EXPORTER_OTLP_ENDPOINT="${OTEL_EXPORTER_OTLP_ENDPOINT:-}"
  PLATFORM_CONTRACT_URL="${PLATFORM_CONTRACT_URL:-}"
  FULL_IMAGE="${ACR_LOGIN_SERVER}/${IMAGE_NAME}:${IMAGE_TAG}"
  DOCKERFILE="${REPO_ROOT}/docker/worker.Dockerfile"
}

validate_worker_settings() {
  require_env AZ_LOCATION
  require_env AZ_ACR_NAME
  require_env AZ_ACR_RG
  require_env AZ_CONTAINER_APP_ENV_NAME
  require_env AZ_CONTAINER_APP_ENV_RG
  require_env SB_NAMESPACE
  require_env AZ_MI_CLIENT_ID
  require_env AZ_STORAGE_NAME
  require_env AZ_STORAGE_RG

  if [[ -z "$CONTAINER_APP_ENV_ID" ]]; then
    echo "ERROR: could not resolve CONTAINER_APP_ENV_ID from ${AZ_CONTAINER_APP_ENV_NAME}" >&2
    exit 1
  fi
  if [[ -z "$MANAGED_IDENTITY_RESOURCE_ID" ]]; then
    echo "ERROR: could not resolve MANAGED_IDENTITY_RESOURCE_ID from ${AZ_MI_NAME:-<unset>}" >&2
    exit 1
  fi
  if [[ -z "$ACR_LOGIN_SERVER" ]]; then
    echo "ERROR: could not resolve ACR login server for ${AZ_ACR_NAME}" >&2
    exit 1
  fi
  if [[ -z "$SB_NAMESPACE_NAME" ]]; then
    echo "ERROR: SB_NAMESPACE_NAME is empty; SB_NAMESPACE must be a Service Bus namespace FQDN." >&2
    exit 1
  fi
  if [[ "$REPORT_MODE" == "servicebus" ]] && [[ -z "${SB_RESULTS_QUEUE:-}" ]]; then
    echo "ERROR: SB_RESULTS_QUEUE must be set when REPORT_MODE=servicebus" >&2
    exit 1
  fi
  if [[ "$REPORT_MODE" == "http" ]]; then
    if [[ -z "$PLATFORM_API_BASE_URL" ]]; then
      echo "ERROR: PLATFORM_API_BASE_URL must be set when REPORT_MODE=http" >&2
      exit 1
    fi
    if [[ -z "$PLATFORM_AUDIENCE" ]]; then
      echo "ERROR: PLATFORM_AUDIENCE must be set when REPORT_MODE=http" >&2
      exit 1
    fi
  fi
}

print_preview() {
  refresh_derived
  validate_worker_settings

  echo ""
  echo "═══════════════════════════════════════════════════════════"
  echo "  awreason Queue Worker – Deployment Preview"
  echo "═══════════════════════════════════════════════════════════"
  echo "  Env file:           $ENV_FILE"
  echo "  Location:           ${AZ_LOCATION}"
  echo "  Container App:      ${CONTAINER_APP_NAME}"
  echo "  ACA Environment:    ${AZ_CONTAINER_APP_ENV_NAME}"
  echo "  Managed Identity:   ${AZ_MI_NAME:-<resolved>}"
  echo "  ACR Login Server:   ${ACR_LOGIN_SERVER}"
  echo "  Image:              ${FULL_IMAGE}"
  echo "  Service Bus FQDN:   ${SB_NAMESPACE}"
  echo "  Service Bus Name:   ${SB_NAMESPACE_NAME}"
  echo "  Queue:              ${SB_QUEUE}"
  echo "  Results Queue:      ${SB_RESULTS_QUEUE:-<none>}"
  echo "  Report Mode:        ${REPORT_MODE}"
  echo "  Blob Account URL:   ${BLOB_ACCOUNT_URL}"
  echo "  Results Container:  ${BLOB_RESULTS_CONTAINER}"
  echo "  Results Prefix:     ${BLOB_RESULTS_PREFIX}"
  echo "  Min Replicas:       ${QUEUE_WORKER_MIN_REPLICAS}"
  echo "  Max Replicas:       ${QUEUE_WORKER_MAX_REPLICAS}"
  echo "  Message Count:      ${QUEUE_WORKER_MESSAGE_COUNT}"
  echo "═══════════════════════════════════════════════════════════"
}

render_yaml() {
  local -a sed_args=()
  local escaped=""

  append_replacement() {
    local placeholder="$1"
    local value="$2"
    escaped=$(escape_sed_replacement "$value")
    sed_args+=(-e "s|{{ ${placeholder} }}|${escaped}|g")
  }

  append_replacement "LOCATION" "${AZ_LOCATION}"
  append_replacement "CONTAINER_APP_NAME" "${CONTAINER_APP_NAME}"
  append_replacement "MANAGED_IDENTITY_RESOURCE_ID" "${MANAGED_IDENTITY_RESOURCE_ID}"
  append_replacement "CONTAINER_APP_ENV_ID" "${CONTAINER_APP_ENV_ID}"
  append_replacement "ACR_LOGIN_SERVER" "${ACR_LOGIN_SERVER}"
  append_replacement "IMAGE_TAG" "${IMAGE_TAG}"
  append_replacement "LOG_LEVEL" "${LOG_LEVEL}"
  append_replacement "PER_REPLICA_CONCURRENCY" "${PER_REPLICA_CONCURRENCY}"
  append_replacement "SB_NAMESPACE" "${SB_NAMESPACE}"
  append_replacement "SB_QUEUE" "${SB_QUEUE}"
  append_replacement "SB_RESULTS_QUEUE" "${SB_RESULTS_QUEUE:-}"
  append_replacement "REPORT_MODE" "${REPORT_MODE}"
  append_replacement "PLATFORM_API_BASE_URL" "${PLATFORM_API_BASE_URL}"
  append_replacement "PLATFORM_AUDIENCE" "${PLATFORM_AUDIENCE}"
  append_replacement "BLOB_ACCOUNT_URL" "${BLOB_ACCOUNT_URL}"
  append_replacement "BLOB_RESULTS_CONTAINER" "${BLOB_RESULTS_CONTAINER}"
  append_replacement "BLOB_RESULTS_PREFIX" "${BLOB_RESULTS_PREFIX}"
  append_replacement "APIM_AOAI_BASE_URL" "${APIM_AOAI_BASE_URL}"
  append_replacement "AOAI_DEPLOYMENT" "${AOAI_DEPLOYMENT}"
  append_replacement "AOAI_API_VERSION" "${AOAI_API_VERSION}"
  append_replacement "AZURE_TENANT_ID" "${AZURE_TENANT_ID}"
  append_replacement "AZURE_CLIENT_ID" "${AZ_MI_CLIENT_ID}"
  append_replacement "AWREASON_CLI_TIMEOUT" "${AWREASON_CLI_TIMEOUT}"
  append_replacement "AWREASON_MAX_RETRIES" "${AWREASON_MAX_RETRIES}"
  append_replacement "AWREASON_RETRY_BACKOFF" "${AWREASON_RETRY_BACKOFF}"
  append_replacement "OTEL_EXPORTER_OTLP_ENDPOINT" "${OTEL_EXPORTER_OTLP_ENDPOINT}"
  append_replacement "PLATFORM_CONTRACT_URL" "${PLATFORM_CONTRACT_URL}"
  append_replacement "SB_NAMESPACE_NAME" "${SB_NAMESPACE_NAME}"
  append_replacement "QUEUE_WORKER_MIN_REPLICAS" "${QUEUE_WORKER_MIN_REPLICAS}"
  append_replacement "QUEUE_WORKER_MAX_REPLICAS" "${QUEUE_WORKER_MAX_REPLICAS}"
  append_replacement "QUEUE_WORKER_POLLING_INTERVAL" "${QUEUE_WORKER_POLLING_INTERVAL}"
  append_replacement "QUEUE_WORKER_COOLDOWN_PERIOD" "${QUEUE_WORKER_COOLDOWN_PERIOD}"
  append_replacement "QUEUE_WORKER_MESSAGE_COUNT" "${QUEUE_WORKER_MESSAGE_COUNT}"

  sed "${sed_args[@]}" "${SCRIPT_DIR}/aca-containerapp.yaml" > "$RESOLVED_YAML"
  echo "Generated: $RESOLVED_YAML"
}

do_infra() {
  echo ""
  echo "═══════════════════════════════════════════════════════════"
  echo "  Ensuring Azure infrastructure for queue worker …"
  echo "═══════════════════════════════════════════════════════════"

  refresh_derived
  ensure_storage
  ensure_acr
  ensure_log_analytics
  ensure_vnet
  ensure_container_app_env
  ensure_servicebus
  ensure_managed_identity
  refresh_derived
  ensure_identity_roles
  ensure_results_container
  ensure_storage_firewall_allows_aca
  refresh_derived
  validate_worker_settings

  echo ""
  echo "✅ Infrastructure ready."
}

do_build() {
  refresh_derived

  if [[ -z "${AZ_IMAGE_TAG:-}" ]] || [[ "${AZ_IMAGE_TAG}" == "latest" ]]; then
    IMAGE_TAG=$(date -u +"%Y%m%dT%H%M%S")
    FULL_IMAGE="${ACR_LOGIN_SERVER}/${IMAGE_NAME}:${IMAGE_TAG}"
  fi

  echo ""
  echo "── Building & pushing worker image via ACR ────────────────"

  local build_context_mode="${BUILD_CONTEXT_MODE:-staged}"
  local build_context_path="$REPO_ROOT"
  local dockerfile_path="$DOCKERFILE"
  local tmp_ctx=""

  if [[ "$build_context_mode" == "staged" ]]; then
    tmp_ctx=$(mktemp -d)
    trap '[[ -n "$tmp_ctx" && -d "$tmp_ctx" ]] && rm -rf "$tmp_ctx"' RETURN

    # Stage only files required by docker/worker.Dockerfile to avoid slow repo-wide tar packing.
    tar -C "$REPO_ROOT" -cf - \
      --exclude='.git' \
      --exclude='.venv' \
      --exclude='.claude' \
      --exclude='logs' \
      --exclude='experiments' \
      --exclude='o1-assessment/batch_results' \
      --exclude='o1-assessment/sample_grading_results' \
      --exclude='o1-assessment/wcg_out' \
      --exclude='o1-assessment/imagedir' \
      --exclude='o1-assessment/sample_pdfs' \
      --exclude='o1-assessment/test_images' \
      --exclude='o1-assessment/testing' \
      --exclude='o1-assessment/grading_results*' \
      docker/worker.Dockerfile \
      docker/worker-requirements.txt \
      contracts runtime engine_core o1-assessment wrappers/queue-worker \
      | tar -xf - -C "$tmp_ctx"

    build_context_path="$tmp_ctx"
    dockerfile_path="$tmp_ctx/docker/worker.Dockerfile"
    echo "  Build context mode: staged"
  else
    echo "  Build context mode: repo"
  fi

  local win_repo_root=""
  local win_dockerfile=""
  win_repo_root=$(cygpath -w "$build_context_path" 2>/dev/null || echo "$build_context_path")
  win_dockerfile=$(cygpath -w "$dockerfile_path" 2>/dev/null || echo "$dockerfile_path")

  az acr build \
    --registry "${AZ_ACR_NAME}" \
    --resource-group "${AZ_ACR_RG}" \
    --image "${IMAGE_NAME}:${IMAGE_TAG}" \
    --image "${IMAGE_NAME}:latest" \
    --file "$win_dockerfile" \
    "$win_repo_root"

  printf '%s\n' "$IMAGE_TAG" > "$LAST_TAG_FILE"
  echo "✅ Image pushed: ${ACR_LOGIN_SERVER}/${IMAGE_NAME}:${IMAGE_TAG}"
}

do_yaml() {
  refresh_derived
  validate_worker_settings
  render_yaml

  local yaml_path="${RESOLVED_YAML}"
  if command -v cygpath >/dev/null 2>&1; then
    yaml_path=$(cygpath -w "$RESOLVED_YAML" 2>/dev/null || echo "$RESOLVED_YAML")
  fi

  echo ""
  echo "── Deploying queue worker via ACA YAML ─────────────────────"

  if resource_exists az containerapp show --name "$CONTAINER_APP_NAME" --resource-group "${AZ_CONTAINER_APP_ENV_RG}"; then
    az containerapp update \
      --name "$CONTAINER_APP_NAME" \
      --resource-group "${AZ_CONTAINER_APP_ENV_RG}" \
      --yaml "$yaml_path"
  else
    az containerapp create \
      --name "$CONTAINER_APP_NAME" \
      --resource-group "${AZ_CONTAINER_APP_ENV_RG}" \
      --yaml "$yaml_path"
  fi

  echo "✅ Queue worker deployed."
}

case "$ACTION" in
  preview)
    print_preview
    ;;
  infra)
    do_infra
    ;;
  build)
    do_build
    ;;
  yaml)
    do_yaml
    ;;
  all)
    do_infra
    do_build
    do_yaml
    ;;
  *)
    echo "Usage: $0 {preview|infra|build|yaml|all}" >&2
    exit 1
    ;;
esac