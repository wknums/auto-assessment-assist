#!/usr/bin/env bash
# ══════════════════════════════════════════════════════════════════════
#  Build, push, and deploy the awreason HTTP Service container.
#
#  Sources all configuration from the repo-root .env file.
#  Automatically creates missing Azure resources UNLESS the
#  corresponding AZ_*_REUSE flag is set to TRUE.
#
#  REUSE=TRUE  → resource MUST already exist; script will NOT create,
#                drop, or recreate it.
#  REUSE=FALSE → resource is created if it doesn't exist.  If a name
#                is provided in .env that name is used; otherwise a
#                sensible default is generated.
#
#  Usage:
#    cd wrappers/http-service/deploy
#    bash deploy.sh                # full deploy (infra + build + apply)
#    bash deploy.sh infra          # ensure infra only (ACR, ACA env)
#    bash deploy.sh build          # build & push only
#    bash deploy.sh apply          # terraform apply only
#    bash deploy.sh yaml           # deploy via ACA YAML (no Terraform)
# ══════════════════════════════════════════════════════════════════════
set -euo pipefail

# Prevent Git Bash (MSYS) from converting /subscriptions/... to C:/Program Files/Git/subscriptions/...
export MSYS_NO_PATHCONV=1

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
ENV_FILE="${DEPLOY_ENV_FILE:-${REPO_ROOT}/.env}"

# Allow relative DEPLOY_ENV_FILE paths (resolved from repo root)
if [[ "$ENV_FILE" != /* ]]; then
  ENV_FILE="${REPO_ROOT}/${ENV_FILE}"
fi

if [[ ! -f "$ENV_FILE" ]]; then
  echo "ERROR: .env file not found at $ENV_FILE" >&2
  exit 1
fi

echo "Using environment file: $ENV_FILE"

# ── Load .env (strip quotes) ─────────────────────────────────────────
set -a
# shellcheck disable=SC1090
source <(sed 's/\r$//' "$ENV_FILE" | grep -v '^\s*#' | grep '=')
set +a

# Terraform's AzureRM provider also reads ARM_* variables. Override stale values
# inherited from another shell/subscription with the selected deployment env.
export ARM_SUBSCRIPTION_ID="${AZURE_SUBSCRIPTION_ID:-}"
export ARM_TENANT_ID="${AZURE_TENANT_ID:-}"

# Backward-compatible Entra defaults. setup-identity.sh app writes the
# dedicated API values for new deployments.
AAD_API_CLIENT_ID="${AAD_API_CLIENT_ID:-${AAD_CLIENT_ID:-}}"
AAD_AUDIENCE="${AAD_AUDIENCE:-${AAD_API_CLIENT_ID}}"
AAD_API_SCOPE="${AAD_API_SCOPE:-}"
if [[ -z "$AAD_API_SCOPE" && -n "$AAD_API_CLIENT_ID" ]]; then
  AAD_API_SCOPE="api://${AAD_API_CLIENT_ID}/access_as_user"
fi

# ── Set active subscription so all az commands target the right one ───
if [[ -n "${AZURE_SUBSCRIPTION_ID:-}" ]]; then
  az account set --subscription "${AZURE_SUBSCRIPTION_ID}" -o none
else
  echo "ERROR: AZURE_SUBSCRIPTION_ID is not set in .env" >&2
  exit 1
fi

# ── Helpers ───────────────────────────────────────────────────────────

is_reuse() {
  # Usage: is_reuse "AZ_ACR_REUSE"
  local val
  val="${!1:-FALSE}"
  [[ "${val^^}" == "TRUE" ]]
}

generate_name() {
  # Generate a short unique suffix: first 8 chars of a hash
  local prefix="$1"
  local seed="${AZURE_SUBSCRIPTION_ID:-$(date +%s)}"
  local hash
  hash=$(echo -n "${seed}-${prefix}" | md5sum | cut -c1-8)
  echo "${prefix}${hash}"
}

resource_exists() {
  # Usage: resource_exists <az-show-command ...>
  # Returns 0 if the resource exists, 1 otherwise.
  "$@" &>/dev/null
}

update_env_var() {
  # Write a variable back to .env so subsequent runs reuse it.
  local var_name="$1" var_value="$2"
  if grep -q "^${var_name}=" "$ENV_FILE" 2>/dev/null; then
    sed -i "s|^${var_name}=.*|${var_name}=${var_value}|" "$ENV_FILE"
  else
    echo "${var_name}=${var_value}" >> "$ENV_FILE"
  fi
  # Also export into the current shell
  export "${var_name}=${var_value}"
}

# ── Ensure infrastructure ────────────────────────────────────────────

ensure_resource_group() {
  local rg="$1" loc="${AZ_LOCATION:-eastus2}"
  if ! resource_exists az group show --name "$rg"; then
    echo "  Creating resource group: $rg in $loc"
    az group create --name "$rg" --location "$loc" -o none
  fi
}

ensure_acr() {
  echo ""
  echo "── ACR ─────────────────────────────────────────────────────"

  if is_reuse AZ_ACR_REUSE; then
    echo "  AZ_ACR_REUSE=TRUE → using existing ACR: ${AZ_ACR_NAME}"
    if [[ -z "${AZ_ACR_NAME}" ]]; then
      echo "ERROR: AZ_ACR_REUSE=TRUE but AZ_ACR_NAME is empty." >&2
      exit 1
    fi
    if ! resource_exists az acr show --name "${AZ_ACR_NAME}" --resource-group "${AZ_ACR_RG}"; then
      echo "ERROR: ACR '${AZ_ACR_NAME}' not found in RG '${AZ_ACR_RG}' but REUSE=TRUE." >&2
      exit 1
    fi
    return
  fi

  # REUSE=FALSE → create if needed
  local acr_name="${AZ_ACR_NAME}"
  local acr_rg="${AZ_ACR_RG:-}"
  if [[ -z "$acr_rg" ]]; then
    echo "ERROR: AZ_ACR_RG is not set in .env" >&2
    exit 1
  fi

  if [[ -z "$acr_name" ]]; then
    acr_name=$(generate_name "acrawr")
    echo "  No AZ_ACR_NAME set – generated: $acr_name"
  fi

  ensure_resource_group "$acr_rg"

  if resource_exists az acr show --name "$acr_name" --resource-group "$acr_rg"; then
    echo "  ACR '$acr_name' already exists – reusing."
  else
    echo "  Creating ACR: $acr_name (SKU=Basic) in $acr_rg"
    az acr create \
      --name "$acr_name" \
      --resource-group "$acr_rg" \
      --sku Basic \
      --admin-enabled true \
      --location "${AZ_LOCATION}" \
      -o none
    echo "  ✅ ACR created: $acr_name"
  fi

  # Persist back to .env
  update_env_var "AZ_ACR_NAME" "$acr_name"
  update_env_var "AZ_ACR_RG" "$acr_rg"
  AZ_ACR_NAME="$acr_name"
  AZ_ACR_RG="$acr_rg"
}

ensure_log_analytics() {
  echo ""
  echo "── Log Analytics Workspace ─────────────────────────────────"

  if is_reuse AZ_LOGANALYTICS_REUSE; then
    echo "  AZ_LOGANALYTICS_REUSE=TRUE → using existing: ${AZ_LOGANALYTICS_NAME:-<not set>}"
    if [[ -z "${AZ_LOGANALYTICS_NAME:-}" ]]; then
      echo "ERROR: AZ_LOGANALYTICS_REUSE=TRUE but AZ_LOGANALYTICS_NAME is empty." >&2
      exit 1
    fi
    if ! resource_exists az monitor log-analytics workspace show \
        --workspace-name "${AZ_LOGANALYTICS_NAME}" \
        --resource-group "${AZ_LOGANALYTICS_RG}"; then
      echo "ERROR: Log Analytics workspace '${AZ_LOGANALYTICS_NAME}' not found in RG '${AZ_LOGANALYTICS_RG}' but REUSE=TRUE." >&2
      exit 1
    fi
    return
  fi

  # REUSE=FALSE → create if needed
  local law_name="${AZ_LOGANALYTICS_NAME:-}"
  local law_rg="${AZ_LOGANALYTICS_RG:-${AZ_CONTAINER_APP_ENV_RG}}"

  if [[ -z "$law_name" ]]; then
    law_name=$(generate_name "law-awr-")
    echo "  No AZ_LOGANALYTICS_NAME set – generated: $law_name"
  fi

  ensure_resource_group "$law_rg"

  if resource_exists az monitor log-analytics workspace show --workspace-name "$law_name" --resource-group "$law_rg"; then
    echo "  Log Analytics workspace '$law_name' already exists – reusing."
  else
    echo "  Creating Log Analytics workspace: $law_name in $law_rg"
    az monitor log-analytics workspace create \
      --workspace-name "$law_name" \
      --resource-group "$law_rg" \
      --location "${AZ_LOCATION}" \
      -o none
    echo "  ✅ Log Analytics workspace created: $law_name"
  fi

  update_env_var "AZ_LOGANALYTICS_NAME" "$law_name"
  update_env_var "AZ_LOGANALYTICS_RG" "$law_rg"
  AZ_LOGANALYTICS_NAME="$law_name"
  AZ_LOGANALYTICS_RG="$law_rg"
}

ensure_appinsights() {
  echo ""
  echo "── Application Insights ────────────────────────────────────"

  # Use az resource (core CLI) instead of 'az monitor app-insights'
  # which requires the application-insights extension (pip install often fails).
  local appi_type="Microsoft.Insights/components"
  local api_ver="2020-02-02"

  if is_reuse AZ_APPINSIGHTS_REUSE; then
    echo "  AZ_APPINSIGHTS_REUSE=TRUE → using existing: ${AZ_APPINSIGHTS_NAME:-<not set>}"
    if [[ -z "${AZ_APPINSIGHTS_NAME:-}" ]]; then
      echo "ERROR: AZ_APPINSIGHTS_REUSE=TRUE but AZ_APPINSIGHTS_NAME is empty." >&2
      exit 1
    fi
    if ! resource_exists az resource show \
        --resource-type "$appi_type" \
        --name "${AZ_APPINSIGHTS_NAME}" \
        --resource-group "${AZ_APPINSIGHTS_RG}"; then
      echo "ERROR: Application Insights '${AZ_APPINSIGHTS_NAME}' not found in RG '${AZ_APPINSIGHTS_RG}' but REUSE=TRUE." >&2
      exit 1
    fi
    # Retrieve the connection string
    AZ_APPINSIGHTS_CONNECTION_STRING=$(az resource show \
      --resource-type "$appi_type" \
      --name "${AZ_APPINSIGHTS_NAME}" \
      --resource-group "${AZ_APPINSIGHTS_RG}" \
      --query "properties.ConnectionString" -o tsv | tr -d '\r')
    return
  fi

  # REUSE=FALSE → create if needed
  local appi_name="${AZ_APPINSIGHTS_NAME:-}"
  local appi_rg="${AZ_APPINSIGHTS_RG:-${AZ_CONTAINER_APP_ENV_RG}}"

  if [[ -z "$appi_name" ]]; then
    appi_name=$(generate_name "appi-awr-")
    echo "  No AZ_APPINSIGHTS_NAME set – generated: $appi_name"
  fi

  # Resolve Log Analytics workspace ID for linking
  local law_id=""
  if [[ -n "${AZ_LOGANALYTICS_NAME:-}" ]]; then
    law_id=$(az monitor log-analytics workspace show \
      --workspace-name "${AZ_LOGANALYTICS_NAME}" \
      --resource-group "${AZ_LOGANALYTICS_RG}" \
      --query id -o tsv 2>/dev/null | tr -d '\r')
  fi

  ensure_resource_group "$appi_rg"

  if resource_exists az resource show --resource-type "$appi_type" --name "$appi_name" --resource-group "$appi_rg"; then
    echo "  Application Insights '$appi_name' already exists – reusing."
  else
    echo "  Creating Application Insights: $appi_name in $appi_rg"
    local props='{"Application_Type":"web"}'
    if [[ -n "$law_id" ]]; then
      props="{\"Application_Type\":\"web\",\"WorkspaceResourceId\":\"${law_id}\"}"
    fi
    az resource create \
      --resource-type "$appi_type" \
      --name "$appi_name" \
      --resource-group "$appi_rg" \
      --location "${AZ_LOCATION}" \
      --properties "$props" \
      -o none
    echo "  ✅ Application Insights created: $appi_name"
  fi

  # Retrieve the connection string
  AZ_APPINSIGHTS_CONNECTION_STRING=$(az resource show \
    --resource-type "$appi_type" \
    --name "$appi_name" \
    --resource-group "$appi_rg" \
    --query "properties.ConnectionString" -o tsv | tr -d '\r')

  update_env_var "AZ_APPINSIGHTS_NAME" "$appi_name"
  update_env_var "AZ_APPINSIGHTS_RG" "$appi_rg"
  AZ_APPINSIGHTS_NAME="$appi_name"
  AZ_APPINSIGHTS_RG="$appi_rg"
}

# ── VNet (optional – controlled by AZ_VNET_ENABLED) ──────────────────

is_vnet_enabled() {
  [[ "${AZ_VNET_ENABLED:-FALSE}" == "TRUE" ]]
}

ensure_vnet() {
  echo ""
  echo "── Virtual Network ─────────────────────────────────────────"

  if ! is_vnet_enabled; then
    echo "  AZ_VNET_ENABLED=FALSE → skipping VNet."
    return
  fi

  if is_reuse AZ_VNET_REUSE; then
    echo "  AZ_VNET_REUSE=TRUE → using existing VNet: ${AZ_VNET_NAME}"
    if [[ -z "${AZ_VNET_NAME:-}" ]]; then
      echo "ERROR: AZ_VNET_REUSE=TRUE but AZ_VNET_NAME is empty." >&2
      exit 1
    fi
    local vnet_rg="${AZ_VNET_RG:-${AZ_CONTAINER_APP_ENV_RG}}"
    if ! resource_exists az network vnet show --name "${AZ_VNET_NAME}" --resource-group "$vnet_rg"; then
      echo "ERROR: VNet '${AZ_VNET_NAME}' not found in RG '${vnet_rg}' but REUSE=TRUE." >&2
      exit 1
    fi
    # Verify subnet exists
    if ! resource_exists az network vnet subnet show \
        --vnet-name "${AZ_VNET_NAME}" --name "${AZ_VNET_SUBNET_NAME:-snet-aca}" \
        --resource-group "$vnet_rg"; then
      echo "ERROR: Subnet '${AZ_VNET_SUBNET_NAME:-snet-aca}' not found in VNet '${AZ_VNET_NAME}'." >&2
      exit 1
    fi
    AZ_VNET_RG="$vnet_rg"
    return
  fi

  # REUSE=FALSE → create if needed
  local vnet_name="${AZ_VNET_NAME:-}"
  local vnet_rg="${AZ_VNET_RG:-${AZ_CONTAINER_APP_ENV_RG}}"
  local subnet_name="${AZ_VNET_SUBNET_NAME:-snet-aca}"
  local vnet_prefix="${AZ_VNET_ADDRESS_PREFIX:-10.200.0.0/16}"
  local subnet_prefix="${AZ_VNET_SUBNET_PREFIX:-10.200.0.0/23}"

  if [[ -z "$vnet_name" ]]; then
    vnet_name="vnet-$(echo "${AZ_CONTAINER_APP_ENV_NAME:-aca}" | head -c 20)"
    echo "  No AZ_VNET_NAME set – generated: $vnet_name"
  fi

  ensure_resource_group "$vnet_rg"

  if resource_exists az network vnet show --name "$vnet_name" --resource-group "$vnet_rg"; then
    echo "  VNet '$vnet_name' already exists – reusing."
  else
    echo "  Creating VNet: $vnet_name ($vnet_prefix) in $vnet_rg"
    az network vnet create \
      --name "$vnet_name" \
      --resource-group "$vnet_rg" \
      --location "${AZ_LOCATION}" \
      --address-prefixes "$vnet_prefix" \
      -o none
    echo "  ✅ VNet created: $vnet_name"
  fi

  # Ensure subnet with delegation + service endpoint
  if resource_exists az network vnet subnet show \
      --vnet-name "$vnet_name" --name "$subnet_name" --resource-group "$vnet_rg"; then
    echo "  Subnet '$subnet_name' already exists."
  else
    echo "  Creating subnet: $subnet_name ($subnet_prefix)"
    az network vnet subnet create \
      --vnet-name "$vnet_name" \
      --name "$subnet_name" \
      --resource-group "$vnet_rg" \
      --address-prefixes "$subnet_prefix" \
      --delegations "Microsoft.App/environments" \
      --service-endpoints "Microsoft.Storage" \
      -o none
    echo "  ✅ Subnet created with ACA delegation + Storage service endpoint."
  fi

  # Ensure service endpoint is present (idempotent – covers pre-existing subnets)
  local existing_endpoints
  existing_endpoints=$(az network vnet subnet show \
    --vnet-name "$vnet_name" --name "$subnet_name" --resource-group "$vnet_rg" \
    --query "serviceEndpoints[?service=='Microsoft.Storage'].service" -o tsv 2>/dev/null | tr -d '\r' || true)
  if [[ -z "$existing_endpoints" ]]; then
    echo "  Adding Microsoft.Storage service endpoint to subnet …"
    az network vnet subnet update \
      --vnet-name "$vnet_name" --name "$subnet_name" --resource-group "$vnet_rg" \
      --service-endpoints "Microsoft.Storage" \
      -o none
    echo "  ✅ Service endpoint added."
  fi

  update_env_var "AZ_VNET_NAME" "$vnet_name"
  update_env_var "AZ_VNET_RG" "$vnet_rg"
  update_env_var "AZ_VNET_SUBNET_NAME" "$subnet_name"
  AZ_VNET_NAME="$vnet_name"
  AZ_VNET_RG="$vnet_rg"
  AZ_VNET_SUBNET_NAME="$subnet_name"
}

ensure_container_app_env() {
  echo ""
  echo "── Container Apps Environment ──────────────────────────────"

  if is_reuse AZ_CONTAINER_APP_ENV_REUSE; then
    echo "  AZ_CONTAINER_APP_ENV_REUSE=TRUE → using existing: ${AZ_CONTAINER_APP_ENV_NAME}"
    if [[ -z "${AZ_CONTAINER_APP_ENV_NAME}" ]]; then
      echo "ERROR: AZ_CONTAINER_APP_ENV_REUSE=TRUE but AZ_CONTAINER_APP_ENV_NAME is empty." >&2
      exit 1
    fi
    if ! resource_exists az containerapp env show \
        --name "${AZ_CONTAINER_APP_ENV_NAME}" \
        --resource-group "${AZ_CONTAINER_APP_ENV_RG}"; then
      echo "ERROR: Container Apps Env '${AZ_CONTAINER_APP_ENV_NAME}' not found in RG '${AZ_CONTAINER_APP_ENV_RG}' but REUSE=TRUE." >&2
      exit 1
    fi
    return
  fi

  # REUSE=FALSE → create if needed
  local env_name="${AZ_CONTAINER_APP_ENV_NAME}"
  local env_rg="${AZ_CONTAINER_APP_ENV_RG:-}"
  if [[ -z "$env_rg" ]]; then
    echo "ERROR: AZ_CONTAINER_APP_ENV_RG is not set in .env" >&2
    exit 1
  fi

  if [[ -z "$env_name" ]]; then
    env_name=$(generate_name "cae-awr-")
    echo "  No AZ_CONTAINER_APP_ENV_NAME set – generated: $env_name"
  fi

  ensure_resource_group "$env_rg"

  # Resolve Log Analytics workspace ID for the environment
  local law_customer_id="" law_shared_key=""
  if [[ -n "${AZ_LOGANALYTICS_NAME:-}" ]]; then
    law_customer_id=$(az monitor log-analytics workspace show \
      --workspace-name "${AZ_LOGANALYTICS_NAME}" \
      --resource-group "${AZ_LOGANALYTICS_RG}" \
      --query customerId -o tsv 2>/dev/null | tr -d '\r')
    law_shared_key=$(az monitor log-analytics workspace get-shared-keys \
      --workspace-name "${AZ_LOGANALYTICS_NAME}" \
      --resource-group "${AZ_LOGANALYTICS_RG}" \
      --query primarySharedKey -o tsv 2>/dev/null | tr -d '\r')
  fi

  if resource_exists az containerapp env show --name "$env_name" --resource-group "$env_rg"; then
    local env_state
    env_state=$(az containerapp env show \
      --name "$env_name" \
      --resource-group "$env_rg" \
      --query "properties.provisioningState" -o tsv | tr -d '\r')
    if [[ "$env_state" != "Succeeded" ]]; then
      echo "ERROR: Container Apps Environment '$env_name' exists in state '$env_state'." >&2
      echo "       Delete or replace the failed environment before continuing." >&2
      exit 1
    fi
    echo "  Container Apps Environment '$env_name' already exists – reusing."
  else
    echo "  Creating Container Apps Environment: $env_name in $env_rg"
    local create_args=(
      az containerapp env create
      --name "$env_name"
      --resource-group "$env_rg"
      --location "${AZ_LOCATION}"
      -o none
    )
    if [[ -n "$law_customer_id" ]] && [[ -n "$law_shared_key" ]]; then
      create_args+=(--logs-workspace-id "$law_customer_id" --logs-workspace-key "$law_shared_key")
      echo "  Linking to Log Analytics workspace: ${AZ_LOGANALYTICS_NAME}"
    fi

    # VNet integration (optional)
    if is_vnet_enabled; then
      local vnet_rg="${AZ_VNET_RG:-${AZ_CONTAINER_APP_ENV_RG}}"
      local subnet_id
      subnet_id=$(az network vnet subnet show \
        --vnet-name "${AZ_VNET_NAME}" \
        --name "${AZ_VNET_SUBNET_NAME:-snet-aca}" \
        --resource-group "$vnet_rg" \
        --query id -o tsv 2>/dev/null | tr -d '\r')
      if [[ -n "$subnet_id" ]]; then
        create_args+=(--infrastructure-subnet-resource-id "$subnet_id")
        echo "  VNet integration: ${AZ_VNET_NAME}/${AZ_VNET_SUBNET_NAME:-snet-aca}"
      else
        echo "  ⚠️  VNet enabled but subnet not found – creating without VNet."
      fi
    fi

    "${create_args[@]}"
    echo "  ✅ Container Apps Environment created: $env_name"
  fi

  # Persist back to .env
  update_env_var "AZ_CONTAINER_APP_ENV_NAME" "$env_name"
  update_env_var "AZ_CONTAINER_APP_ENV_RG" "$env_rg"
  AZ_CONTAINER_APP_ENV_NAME="$env_name"
  AZ_CONTAINER_APP_ENV_RG="$env_rg"
}

ensure_storage_firewall_allows_aca() {
  echo ""
  echo "── Storage Firewall: allow ACA access ──────────────────────"

  if [[ -z "${AZ_STORAGE_NAME:-}" ]]; then
    echo "  ⚠️  AZ_STORAGE_NAME not set – skipping firewall rule."
    return
  fi
  if [[ -z "${AZ_CONTAINER_APP_ENV_NAME:-}" ]]; then
    echo "  ⚠️  AZ_CONTAINER_APP_ENV_NAME not set – skipping firewall rule."
    return
  fi

  if is_vnet_enabled; then
    # ── VNet mode: add subnet rule → restore Deny ───────────────────
    local vnet_rg="${AZ_VNET_RG:-${AZ_CONTAINER_APP_ENV_RG}}"
    local subnet_id
    subnet_id=$(az network vnet subnet show \
      --vnet-name "${AZ_VNET_NAME}" \
      --name "${AZ_VNET_SUBNET_NAME:-snet-aca}" \
      --resource-group "$vnet_rg" \
      --query id -o tsv 2>/dev/null | tr -d '\r' || true)

    if [[ -z "$subnet_id" ]]; then
      echo "  ⚠️  VNet enabled but subnet not found – cannot add VNet rule."
      return
    fi

    # Check if VNet rule already exists
    local existing_vnet_rule
    existing_vnet_rule=$(az storage account network-rule list \
      --account-name "${AZ_STORAGE_NAME}" \
      --resource-group "${AZ_STORAGE_RG}" \
      --query "virtualNetworkRules[?virtualNetworkResourceId=='${subnet_id}'].virtualNetworkResourceId" \
      -o tsv 2>/dev/null | tr -d '\r' || true)

    if [[ -n "$existing_vnet_rule" ]]; then
      echo "  VNet rule for ${AZ_VNET_SUBNET_NAME:-snet-aca} already exists."
    else
      echo "  Adding VNet rule for subnet ${AZ_VNET_SUBNET_NAME:-snet-aca} …"
      az storage account network-rule add \
        --account-name "${AZ_STORAGE_NAME}" \
        --resource-group "${AZ_STORAGE_RG}" \
        --subnet "$subnet_id" \
        -o none
      echo "  ✅ VNet rule added."
    fi

    # Ensure defaultAction is Deny (firewall active)
    local default_action
    default_action=$(az storage account show \
      --name "${AZ_STORAGE_NAME}" \
      --resource-group "${AZ_STORAGE_RG}" \
      --query "networkRuleSet.defaultAction" -o tsv 2>/dev/null | tr -d '\r' || true)

    if [[ "${default_action}" != "Deny" ]]; then
      echo "  Setting storage firewall defaultAction=Deny …"
      az storage account update \
        --name "${AZ_STORAGE_NAME}" \
        --resource-group "${AZ_STORAGE_RG}" \
        --default-action Deny \
        -o none
      echo "  ✅ Storage firewall enabled (defaultAction=Deny)."
    else
      echo "  Storage firewall already set to Deny."
    fi
  else
    # ── No VNet: best-effort IP rule (may not work for Consumption tier) ─
    local default_action
    default_action=$(az storage account show \
      --name "${AZ_STORAGE_NAME}" \
      --resource-group "${AZ_STORAGE_RG}" \
      --query "networkRuleSet.defaultAction" -o tsv 2>/dev/null | tr -d '\r' || true)

    if [[ "${default_action}" != "Deny" ]]; then
      echo "  Storage firewall defaultAction=${default_action:-Allow} – no IP rule needed."
      return
    fi

    echo "  ⚠️  No VNet – Consumption-tier ACA uses shared outbound IPs."
    echo "     Storage firewall may block ACA blob access."
    echo "     Consider AZ_VNET_ENABLED=TRUE for reliable firewall + ACA."

    # Try static IP as best-effort
    local aca_static_ip
    aca_static_ip=$(az containerapp env show \
      --name "${AZ_CONTAINER_APP_ENV_NAME}" \
      --resource-group "${AZ_CONTAINER_APP_ENV_RG}" \
      --query "properties.staticIp" -o tsv 2>/dev/null | tr -d '\r' || true)
    aca_static_ip="${aca_static_ip:-}"

    if [[ -z "$aca_static_ip" ]]; then
      echo "  ⚠️  Could not resolve ACA environment static IP – skipping."
      return
    fi

    local existing
    existing=$(az storage account network-rule list \
      --account-name "${AZ_STORAGE_NAME}" \
      --resource-group "${AZ_STORAGE_RG}" \
      --query "ipRules[?ipAddressOrRange=='${aca_static_ip}'].ipAddressOrRange" \
      -o tsv 2>/dev/null | tr -d '\r' || true)

    if [[ -n "$existing" ]]; then
      echo "  ACA static IP ${aca_static_ip} already in allow list (best-effort)."
    else
      az storage account network-rule add \
        --account-name "${AZ_STORAGE_NAME}" \
        --resource-group "${AZ_STORAGE_RG}" \
        --ip-address "${aca_static_ip}" \
        -o none
      echo "  ✅ Added ACA static IP ${aca_static_ip} (best-effort – may not work for Consumption tier)."
    fi
  fi
}

ensure_storage() {
  echo ""
  echo "── Storage Account ─────────────────────────────────────────"

  if is_reuse AZ_STORAGE_REUSE; then
    echo "  AZ_STORAGE_REUSE=TRUE → using existing: ${AZ_STORAGE_NAME}"
    if [[ -z "${AZ_STORAGE_NAME}" ]]; then
      echo "ERROR: AZ_STORAGE_REUSE=TRUE but AZ_STORAGE_NAME is empty." >&2
      exit 1
    fi
    if ! resource_exists az storage account show --name "${AZ_STORAGE_NAME}" --resource-group "${AZ_STORAGE_RG}"; then
      echo "ERROR: Storage account '${AZ_STORAGE_NAME}' not found in RG '${AZ_STORAGE_RG}' but REUSE=TRUE." >&2
      exit 1
    fi
    return
  fi

  # REUSE=FALSE → create if needed
  local sa_name="${AZ_STORAGE_NAME}"
  local sa_rg="${AZ_STORAGE_RG:-}"
  if [[ -z "$sa_rg" ]]; then
    echo "ERROR: AZ_STORAGE_RG is not set in .env" >&2
    exit 1
  fi

  if [[ -z "$sa_name" ]]; then
    sa_name=$(generate_name "stawrea")
    echo "  No AZ_STORAGE_NAME set – generated: $sa_name"
  fi

  ensure_resource_group "$sa_rg"

  if resource_exists az storage account show --name "$sa_name" --resource-group "$sa_rg"; then
    echo "  Storage account '$sa_name' already exists – reusing."
  else
    echo "  Creating storage account: $sa_name in $sa_rg"
    az storage account create \
      --name "$sa_name" \
      --resource-group "$sa_rg" \
      --location "${AZ_LOCATION}" \
      --sku Standard_LRS \
      --kind StorageV2 \
      -o none
    echo "  ✅ Storage account created: $sa_name"
  fi

  update_env_var "AZ_STORAGE_NAME" "$sa_name"
  update_env_var "AZ_STORAGE_RG" "$sa_rg"
  AZ_STORAGE_NAME="$sa_name"
  AZ_STORAGE_RG="$sa_rg"
}

do_infra() {
  echo ""
  echo "═══════════════════════════════════════════════════════════"
  echo "  Ensuring Azure infrastructure …"
  echo "═══════════════════════════════════════════════════════════"

  ensure_storage
  ensure_acr
  ensure_log_analytics
  ensure_appinsights
  ensure_vnet
  ensure_container_app_env
  ensure_storage_firewall_allows_aca

  echo ""
  echo "✅ Infrastructure ready."
  echo "   ACR:      ${AZ_ACR_NAME}"
  echo "   ACA Env:  ${AZ_CONTAINER_APP_ENV_NAME}"
  echo "   Storage:  ${AZ_STORAGE_NAME}"
  echo "   Log Ana:  ${AZ_LOGANALYTICS_NAME:-<none>}"
  echo "   AppInsig: ${AZ_APPINSIGHTS_NAME:-<none>}"
}

# ── Derived values (re-evaluate after infra) ─────────────────────────

refresh_derived() {
  ACR_LOGIN_SERVER="${AZ_ACR_NAME}.azurecr.io"
  IMAGE_NAME="awreason-http-service"
  # Use the tag from the last build if available; otherwise git SHA; fallback to timestamp
  if [[ -z "${AZ_IMAGE_TAG:-}" ]] || [[ "${AZ_IMAGE_TAG}" == "latest" ]]; then
    if [[ -f "${SCRIPT_DIR}/.last_image_tag" ]]; then
      IMAGE_TAG=$(< "${SCRIPT_DIR}/.last_image_tag")
    else
      IMAGE_TAG=$(git -C "$REPO_ROOT" rev-parse --short=8 HEAD 2>/dev/null || date -u +"%Y%m%dT%H%M%S")
    fi
  else
    IMAGE_TAG="${AZ_IMAGE_TAG}"
  fi
  FULL_IMAGE="${ACR_LOGIN_SERVER}/${IMAGE_NAME}:${IMAGE_TAG}"
  DOCKERFILE="${REPO_ROOT}/wrappers/http-service/Dockerfile"
}

print_banner() {
  echo ""
  echo "═══════════════════════════════════════════════════════════"
  echo "  awreason HTTP Service – Deployment"
  echo "═══════════════════════════════════════════════════════════"
  echo "  ACR:        ${ACR_LOGIN_SERVER}"
  echo "  Image:      ${FULL_IMAGE}"
  echo "  ACA Env:    ${AZ_CONTAINER_APP_ENV_NAME}"
  echo "  App Name:   ${AZ_CONTAINER_APP_NAME}"
  echo "  Location:   ${AZ_LOCATION}"
  echo "  AOAI:       ${AZURE_OPENAI_ENDPOINT}"
  echo "  Deployment: ${AZURE_OPENAI_DEPLOYMENT_O1}"
  echo "  Storage:    ${AZ_STORAGE_NAME} (REUSE=${AZ_STORAGE_REUSE:-FALSE})"
  echo "  AppInsig:   ${AZ_APPINSIGHTS_NAME:-<none>} (REUSE=${AZ_APPINSIGHTS_REUSE:-FALSE})"
  echo "  ACR REUSE:  ${AZ_ACR_REUSE:-FALSE}"
  echo "  ACA REUSE:  ${AZ_CONTAINER_APP_ENV_REUSE:-FALSE}"
  echo "═══════════════════════════════════════════════════════════"
}

do_build() {
  # Always generate a fresh tag for builds (don't read .last_image_tag)
  ACR_LOGIN_SERVER="${AZ_ACR_NAME}.azurecr.io"
  IMAGE_NAME="awreason-http-service"
  if [[ -z "${AZ_IMAGE_TAG:-}" ]] || [[ "${AZ_IMAGE_TAG}" == "latest" ]]; then
    IMAGE_TAG=$(date -u +"%Y%m%dT%H%M%S")
  else
    IMAGE_TAG="${AZ_IMAGE_TAG}"
  fi
  FULL_IMAGE="${ACR_LOGIN_SERVER}/${IMAGE_NAME}:${IMAGE_TAG}"
  DOCKERFILE="${REPO_ROOT}/wrappers/http-service/Dockerfile"

  echo ""
  echo "── Preparing minimal build context ─────────────────────────"
  local build_ctx
  build_ctx="$(mktemp -d 2>/dev/null || mktemp -d -t awr-http-buildctx)"

  echo "── performing cleanup of prior builds ─────────────────────────"
  cleanup_build_ctx() {
    rm -rf "$build_ctx"
  }
  trap cleanup_build_ctx RETURN

  echo "── completed cleanup of prior builds ─────────────────────────"

  mkdir -p "$build_ctx/wrappers/http-service"

  echo "── copying code to build context ─────────────────────────"
  # Copy only files required by wrappers/http-service/Dockerfile.
  cp "$REPO_ROOT/wrappers/http-service/Dockerfile" "$build_ctx/wrappers/http-service/Dockerfile"
  cp "$REPO_ROOT/wrappers/http-service/requirements.txt" "$build_ctx/wrappers/http-service/requirements.txt"
  cp "$REPO_ROOT/wrappers/http-service/supervisord.conf" "$build_ctx/wrappers/http-service/supervisord.conf"
  cp "$REPO_ROOT/wrappers/http-service/nginx.conf" "$build_ctx/wrappers/http-service/nginx.conf"
  cp -R "$REPO_ROOT/wrappers/http-service/app" "$build_ctx/wrappers/http-service/"
  cp -R "$REPO_ROOT/o1-assessment" "$build_ctx/"

  echo "── completed copying code to build context ─────────────────────────"
  DOCKERFILE="$build_ctx/wrappers/http-service/Dockerfile"

  echo ""
  echo "── Building & pushing image via ACR ────────────────────────"
  # Convert Git Bash paths to Windows paths for az CLI (a Windows process)
  local win_repo_root win_dockerfile
  win_repo_root=$(cygpath -w "$build_ctx" 2>/dev/null || echo "$build_ctx")
  win_dockerfile=$(cygpath -w "$DOCKERFILE" 2>/dev/null || echo "$DOCKERFILE")
  az acr build \
    --registry "${AZ_ACR_NAME}" \
    --resource-group "${AZ_ACR_RG}" \
    --image "${IMAGE_NAME}:${IMAGE_TAG}" \
    --image "${IMAGE_NAME}:latest" \
    --file "${win_dockerfile}" \
    "${win_repo_root}"

  trap - RETURN
  cleanup_build_ctx

  # Persist the tag so a subsequent 'apply' uses exactly the same one
  echo "${IMAGE_TAG}" > "${SCRIPT_DIR}/.last_image_tag"
  echo "✅ Image pushed: ${FULL_IMAGE} (also tagged :latest)"
}

# ── Auto-import pre-existing Azure resources into Terraform state ─────
# Resources created outside Terraform (e.g. by setup-identity.sh) cause
# "already exists" errors on first apply.  This function checks each
# managed resource and imports it if it exists in Azure but not in state.

_tf_try_import() {
  # Usage: _tf_try_import <tf_address> <azure_resource_id>
  local addr="$1" rid="$2"
  # Skip if already in state
  if terraform state show "$addr" &>/dev/null; then
    echo "  ✓ $addr (already in state)"
    return
  fi
  # Skip if the Azure resource doesn't actually exist
  if [[ -z "$rid" ]]; then
    echo "  – $addr (resource ID unknown, skipping)"
    return
  fi
  echo "  ⬇ Importing $addr …"
  if ! terraform import -var-file=terraform.tfvars "$addr" "$rid"; then
    echo "  ⚠️  Import failed for $addr (resource may be in a bad state)"
  fi
}

_tf_import_if_needed() {
  local mi_rg="${AZ_CONTAINER_APP_ENV_RG}"
  local mi_name="${AZ_MI_NAME:-id-awreason-http-service}"
  local sub_id="${AZURE_SUBSCRIPTION_ID}"

  # ── Managed Identity ────────────────────────────────────────────
  local mi_id="/subscriptions/${sub_id}/resourceGroups/${mi_rg}/providers/Microsoft.ManagedIdentity/userAssignedIdentities/${mi_name}"
  _tf_try_import "azurerm_user_assigned_identity.awreason" "$mi_id"

  # ── Role Assignment: Storage Blob Data Contributor ──────────────
  local blob_ra_id
  blob_ra_id=$(az role assignment list \
    --assignee "${AZ_MI_PRINCIPAL_ID:-}" \
    --role "Storage Blob Data Contributor" \
    --scope "/subscriptions/${sub_id}/resourceGroups/${AZ_STORAGE_RG}/providers/Microsoft.Storage/storageAccounts/${AZ_STORAGE_NAME}" \
    --query "[0].id" -o tsv 2>/dev/null | tr -d '\r' || true)
  _tf_try_import "azurerm_role_assignment.blob_contributor" "${blob_ra_id:-}"

  # ── Role Assignment: Cognitive Services OpenAI User ─────────────
  local aoai_ra_id
  aoai_ra_id=$(az role assignment list \
    --assignee "${AZ_MI_PRINCIPAL_ID:-}" \
    --role "Cognitive Services OpenAI User" \
    --scope "/subscriptions/${sub_id}/resourceGroups/${AZ_AOAI_RESOURCE_RG}/providers/Microsoft.CognitiveServices/accounts/${AZ_AOAI_RESOURCE_NAME}" \
    --query "[0].id" -o tsv 2>/dev/null | tr -d '\r' || true)
  _tf_try_import "azurerm_role_assignment.aoai_user" "${aoai_ra_id:-}"

  # ── Role Assignment: AcrPull on the ACR ─────────────────────────
  local acr_ra_id
  acr_ra_id=$(az role assignment list \
    --assignee "${AZ_MI_PRINCIPAL_ID:-}" \
    --role "AcrPull" \
    --scope "/subscriptions/${sub_id}/resourceGroups/${AZ_ACR_RG}/providers/Microsoft.ContainerRegistry/registries/${AZ_ACR_NAME}" \
    --query "[0].id" -o tsv 2>/dev/null | tr -d '\r' || true)
  _tf_try_import "azurerm_role_assignment.acr_pull" "${acr_ra_id:-}"

  # ── Container App ───────────────────────────────────────────────
  local ca_id="/subscriptions/${sub_id}/resourceGroups/${mi_rg}/providers/Microsoft.App/containerApps/${AZ_CONTAINER_APP_NAME}"
  _tf_try_import "azurerm_container_app.awreason" "$ca_id"

  # ── Storage Network Rules (VNet mode only) ─────────────────────
  if is_vnet_enabled; then
    local stor_id="/subscriptions/${sub_id}/resourceGroups/${AZ_STORAGE_RG}/providers/Microsoft.Storage/storageAccounts/${AZ_STORAGE_NAME}"
    _tf_try_import 'azurerm_storage_account_network_rules.vnet_access[0]' "$stor_id"
  fi
}

select_terraform_workspace() {
  local env_basename workspace
  env_basename=$(basename "$ENV_FILE")
  if [[ "$env_basename" == ".env" ]]; then
    workspace="default"
  else
    workspace="${DEPLOY_TF_WORKSPACE:-${env_basename#.env_}}"
    workspace=$(printf '%s' "$workspace" | tr -c '[:alnum:]_-' '-')
  fi

  if terraform workspace select "$workspace" >/dev/null 2>&1; then
    echo "  Terraform workspace: $workspace"
  else
    terraform workspace new "$workspace" >/dev/null
    echo "  Terraform workspace created: $workspace"
  fi
}

# ── Update Entra ID App Registration redirect URIs ────────────────
# Called after Terraform apply when the ACA FQDN is known. Ensures
# the app registration always has the current FQDN as a redirect URI
# (alongside the localhost dev URI).
update_redirect_uris() {
  local fqdn="${1:-}"
  if [[ -z "$fqdn" ]]; then
    echo "  ⏩ No FQDN supplied — skipping redirect URI update."
    return
  fi
  if [[ -z "${AAD_CLIENT_ID:-}" ]]; then
    echo "  ⏩ AAD_CLIENT_ID not set — skipping redirect URI update."
    return
  fi

  echo ""
  echo "── Updating Entra ID App Registration redirect URIs ────────"

  local aca_uri="https://${fqdn}/" redirect current_redirect
  local redirect_uris=()
  while IFS= read -r current_redirect; do
    [[ -n "$current_redirect" ]] && redirect_uris+=("$current_redirect")
  done < <(az ad app show --id "$AAD_CLIENT_ID" \
    --query "web.redirectUris" -o tsv | tr -d '\r')

  add_redirect_uri() {
    local candidate="$1"
    for redirect in "${redirect_uris[@]}"; do
      [[ "$redirect" == "$candidate" ]] && return
    done
    redirect_uris+=("$candidate")
  }

  add_redirect_uri "http://localhost:8501/"
  add_redirect_uri "$aca_uri"
  if [[ -n "${STREAMLIT_REDIRECT_URI:-}" ]]; then
    local explicit_redirect="${STREAMLIT_REDIRECT_URI}"
    [[ "$explicit_redirect" != */ ]] && explicit_redirect="${explicit_redirect}/"
    add_redirect_uri "$explicit_redirect"
  fi

  az ad app update \
    --id "$AAD_CLIENT_ID" \
    --web-redirect-uris "${redirect_uris[@]}" \
    -o none
  echo "  ✅ Redirect URIs updated:"
  printf '     - %s\n' "${redirect_uris[@]}"
}

configure_streamlit_session_affinity() {
  echo ""
  echo "── Configuring Streamlit session affinity ──────────────────"
  az containerapp ingress sticky-sessions set \
    --name "${AZ_CONTAINER_APP_NAME}" \
    --resource-group "${AZ_CONTAINER_APP_ENV_RG}" \
    --subscription "${AZURE_SUBSCRIPTION_ID}" \
    --affinity sticky \
    --output none
  echo "  ✅ Sticky sessions enabled for Streamlit WebSockets and uploads"
}

do_apply() {
  _do_apply_impl "interactive"
}

do_apply_force() {
  _do_apply_impl "force"
}

_do_apply_impl() {
  local mode="${1:-interactive}"
  refresh_derived
  echo ""
  echo "── Generating terraform.tfvars ─────────────────────────────"
  pushd "${SCRIPT_DIR}/terraform" > /dev/null
  bash gen-tfvars.sh "$ENV_FILE"

  echo ""
  echo "── Terraform init & apply ──────────────────────────────────"
  terraform init -upgrade
  select_terraform_workspace

  # ── Auto-import pre-existing resources ──────────────────────────
  # Resources created by setup-identity.sh may already exist in Azure
  # but not in Terraform state. Import them automatically.
  echo ""
  echo "── Checking for pre-existing resources to import ───────────"
  _tf_import_if_needed
  echo ""

  local proceed="n"
  if [[ "$mode" == "force" ]]; then
    terraform plan -var-file=terraform.tfvars
    proceed="y"
  else
    terraform plan -var-file=terraform.tfvars
    read -rp "Apply this plan? [y/N] " confirm
    confirm="${confirm//$'\r'/}"
    if [[ "$confirm" =~ ^[Yy]$ ]]; then
      proceed="y"
    fi
  fi

  if [[ "$proceed" == "y" ]]; then
    terraform apply -var-file=terraform.tfvars -auto-approve
    configure_streamlit_session_affinity
    echo ""
    echo "✅ Deployment complete!"
    echo "   (nginx proxy_read_timeout is 600s for long-running assessments)"
    terraform output

    # ── Post-deploy health check ────────────────────────────────────
    local fqdn
    fqdn=$(terraform output -raw container_app_fqdn 2>/dev/null || true)
    if [[ -n "$fqdn" ]]; then
      echo ""
      echo "── Waiting for container to become healthy ─────────────────"
      local url="https://${fqdn}/healthz"
      local max_attempts=20  # 20 × 10s = ~3.3 min
      local attempt=0
      local curl_command="curl" curl_output_sink="/dev/null"
      if command -v curl.exe >/dev/null 2>&1; then
        curl_command="curl.exe"
        curl_output_sink="NUL"
      fi
      while (( attempt < max_attempts )); do
        attempt=$((attempt + 1))
        local status
        if ! status=$("$curl_command" -s -o "$curl_output_sink" -w "%{http_code}" --max-time 5 "$url" 2>/dev/null); then
          status="000"
        fi
        if [[ "$status" == "200" ]]; then
          echo "  ✅ Health check passed (HTTP $status) – $url"
          break
        fi
        echo "  ⏳ Attempt $attempt/$max_attempts: HTTP $status – retrying in 10s …"
        sleep 10
      done
      if (( attempt >= max_attempts )); then
        echo "  ⚠️  Health check did not pass after ${max_attempts} attempts."
        echo "     Check logs: az containerapp logs show --name ${AZ_CONTAINER_APP_NAME} --resource-group ${AZ_CONTAINER_APP_ENV_RG} --type console --tail 30 --follow false"
      fi
    fi

    # ── Update Entra ID redirect URIs with the (possibly new) FQDN ──
    update_redirect_uris "$fqdn"
  else
    echo "Aborted."
  fi
  popd > /dev/null
}

do_yaml() {
  refresh_derived
  echo ""
  echo "── Deploying via ACA YAML manifest ─────────────────────────"
  local resolved_yaml win_resolved_yaml
  resolved_yaml="${SCRIPT_DIR}/_resolved-aca.yaml"

  # Resolve values for the YAML placeholders
  MI_RESOURCE_ID=$(az identity show \
    --name "id-awreason-http-service" \
    --resource-group "${AZ_CONTAINER_APP_ENV_RG}" \
    --query id -o tsv 2>/dev/null | tr -d '\r' || true)
  MI_RESOURCE_ID="${MI_RESOURCE_ID:-}"
  MI_CLIENT_ID=$(az identity show \
    --name "id-awreason-http-service" \
    --resource-group "${AZ_CONTAINER_APP_ENV_RG}" \
    --query clientId -o tsv 2>/dev/null | tr -d '\r' || true)
  MI_CLIENT_ID="${MI_CLIENT_ID:-}"
  CONTAINER_APP_ENV_ID=$(az containerapp env show \
    --name "${AZ_CONTAINER_APP_ENV_NAME}" \
    --resource-group "${AZ_CONTAINER_APP_ENV_RG}" \
    --query id -o tsv | tr -d '\r' || true)

  # Resolve a stable Streamlit redirect URI.
  # Prefer explicit env override, else use current app FQDN,
  # else derive from env default domain + app name.
  local app_fqdn env_default_domain streamlit_redirect_uri
  app_fqdn=$(az containerapp show \
    --name "${AZ_CONTAINER_APP_NAME}" \
    --resource-group "${AZ_CONTAINER_APP_ENV_RG}" \
    --query "properties.configuration.ingress.fqdn" -o tsv 2>/dev/null | tr -d '\r' || true)
  env_default_domain=$(az containerapp env show \
    --name "${AZ_CONTAINER_APP_ENV_NAME}" \
    --resource-group "${AZ_CONTAINER_APP_ENV_RG}" \
    --query "properties.defaultDomain" -o tsv 2>/dev/null | tr -d '\r' || true)

  streamlit_redirect_uri="${STREAMLIT_REDIRECT_URI:-}"
  if [[ -z "${streamlit_redirect_uri}" ]]; then
    if [[ -n "${app_fqdn}" ]]; then
      streamlit_redirect_uri="https://${app_fqdn}/"
    elif [[ -n "${env_default_domain}" ]]; then
      streamlit_redirect_uri="https://${AZ_CONTAINER_APP_NAME}.${env_default_domain}/"
    fi
  fi
  if [[ -n "${streamlit_redirect_uri}" ]] && [[ "${streamlit_redirect_uri}" != */ ]]; then
    streamlit_redirect_uri="${streamlit_redirect_uri}/"
  fi
  echo "Using Streamlit redirect URI: ${streamlit_redirect_uri:-<unset>}"

  # Resolve Application Insights connection string
  local appi_conn_str=""
  if [[ -n "${AZ_APPINSIGHTS_NAME:-}" ]] && [[ -n "${AZ_APPINSIGHTS_RG:-}" ]]; then
    appi_conn_str=$(az resource show \
      --resource-type "Microsoft.Insights/components" \
      --name "${AZ_APPINSIGHTS_NAME}" \
      --resource-group "${AZ_APPINSIGHTS_RG}" \
      --query "properties.ConnectionString" -o tsv 2>/dev/null | tr -d '\r')
  fi
  appi_conn_str="${appi_conn_str:-${AZ_APPINSIGHTS_CONNECTION_STRING:-}}"

  # Generate resolved YAML
  sed \
    -e "s|{{ LOCATION }}|${AZ_LOCATION}|g" \
    -e "s|{{ MANAGED_IDENTITY_RESOURCE_ID }}|${MI_RESOURCE_ID}|g" \
    -e "s|{{ MANAGED_IDENTITY_CLIENT_ID }}|${MI_CLIENT_ID}|g" \
    -e "s|{{ CONTAINER_APP_ENV_ID }}|${CONTAINER_APP_ENV_ID}|g" \
    -e "s|{{ ACR_LOGIN_SERVER }}|${ACR_LOGIN_SERVER}|g" \
    -e "s|{{ IMAGE_TAG }}|${IMAGE_TAG}|g" \
    -e "s|{{ AZ_STORAGE_NAME }}|${AZ_STORAGE_NAME}|g" \
    -e "s|{{ AZ_STORAGE_RG }}|${AZ_STORAGE_RG}|g" \
    -e "s|{{ LOG_LEVEL }}|${LOG_LEVEL:-INFO}|g" \
    -e "s|{{ PER_REPLICA_CONCURRENCY }}|${PER_REPLICA_CONCURRENCY:-4}|g" \
    -e "s|{{ HTTP_MIN_REPLICAS }}|${HTTP_MIN_REPLICAS:-1}|g" \
    -e "s|{{ HTTP_MAX_REPLICAS }}|${HTTP_MAX_REPLICAS:-1}|g" \
    -e "s|{{ HTTP_SCALE_CONCURRENT_REQUESTS }}|${HTTP_SCALE_CONCURRENT_REQUESTS:-${PER_REPLICA_CONCURRENCY:-24}}|g" \
    -e "s|{{ ACTIVE_REQUEST_IDS_DIR }}|${ACTIVE_REQUEST_IDS_DIR:-}|g" \
    -e "s|{{ AZURE_OPENAI_ENDPOINT }}|${AZURE_OPENAI_ENDPOINT}|g" \
    -e "s|{{ APIM_AOAI_BASE_URL }}|${APIM_AOAI_BASE_URL:-}|g" \
    -e "s|{{ AOAI_DEPLOYMENT }}|${AOAI_DEPLOYMENT:-${AZURE_OPENAI_DEPLOYMENT_O1}}|g" \
    -e "s|{{ AOAI_API_VERSION }}|${AOAI_API_VERSION:-${AZURE_OPENAI_API_VERSION:-2024-12-01-preview}}|g" \
    -e "s|{{ USE_AAD_FOR_AOAI }}|${USE_AAD_FOR_AOAI:-true}|g" \
    -e "s|{{ AUTH_MODE }}|${AUTH_MODE:-none}|g" \
    -e "s|{{ API_KEY }}|${API_KEY:-}|g" \
    -e "s|{{ AAD_ISSUER }}|https://login.microsoftonline.com/${AZURE_TENANT_ID}/v2.0|g" \
    -e "s|{{ AAD_AUDIENCE }}|${AAD_AUDIENCE}|g" \
    -e "s|{{ AAD_REQUIRED_SCOPE }}|${AAD_REQUIRED_SCOPE:-access_as_user}|g" \
    -e "s|{{ AAD_REQUIRED_APP_ROLE }}|${AAD_REQUIRED_APP_ROLE:-TalentMatch.Access}|g" \
    -e "s|{{ AZURE_TENANT_ID }}|${AZURE_TENANT_ID}|g" \
    -e "s|{{ AZURE_SUBSCRIPTION_ID }}|${AZURE_SUBSCRIPTION_ID}|g" \
    -e "s|{{ AAD_CLIENT_ID }}|${AAD_CLIENT_ID}|g" \
    -e "s|{{ AAD_API_CLIENT_ID }}|${AAD_API_CLIENT_ID}|g" \
    -e "s|{{ AAD_API_SCOPE }}|${AAD_API_SCOPE}|g" \
    -e "s|{{ AAD_TENANT_ID }}|${AAD_TENANT_ID:-${AZURE_TENANT_ID}}|g" \
    -e "s|{{ AAD_CLIENT_SECRET }}|${AAD_CLIENT_SECRET}|g" \
    -e "s|{{ STREAMLIT_REDIRECT_URI }}|${streamlit_redirect_uri}|g" \
    -e "s|{{ AWREASON_MAX_RETRIES }}|${AWREASON_MAX_RETRIES:-3}|g" \
    -e "s|{{ AWREASON_RETRY_BACKOFF }}|${AWREASON_RETRY_BACKOFF:-10}|g" \
    -e "s|{{ APPINSIGHTS_CONNECTION_STRING }}|${appi_conn_str}|g" \
    -e "s|{{ OTEL_ENDPOINT }}||g" \
    "${SCRIPT_DIR}/aca-containerapp.yaml" > "${resolved_yaml}"

  echo "Generated: ${resolved_yaml}"

  if [[ ! -f "${resolved_yaml}" ]]; then
    echo "ERROR: Resolved YAML was not created: ${resolved_yaml}" >&2
    exit 1
  fi

  # az is a Windows process under Git Bash; pass Windows-form path for --yaml.
  win_resolved_yaml=$(cygpath -w "${resolved_yaml}" 2>/dev/null || echo "${resolved_yaml}")

  if resource_exists az containerapp show --name "${AZ_CONTAINER_APP_NAME}" --resource-group "${AZ_CONTAINER_APP_ENV_RG}"; then
    az containerapp update \
      --resource-group "${AZ_CONTAINER_APP_ENV_RG}" \
      --name "${AZ_CONTAINER_APP_NAME}" \
      --yaml "${win_resolved_yaml}"
  else
    az containerapp create \
      --resource-group "${AZ_CONTAINER_APP_ENV_RG}" \
      --yaml "${win_resolved_yaml}"
  fi

  configure_streamlit_session_affinity

  echo ""
  echo "✅ Deployed via YAML!"
  echo "   (nginx proxy_read_timeout is 600s for long-running assessments)"

  # ── Update Entra ID redirect URIs with the (possibly new) FQDN ──
  local yaml_fqdn
  yaml_fqdn=$(az containerapp show \
    --name "${AZ_CONTAINER_APP_NAME}" \
    --resource-group "${AZ_CONTAINER_APP_ENV_RG}" \
    --query "properties.configuration.ingress.fqdn" -o tsv 2>/dev/null | tr -d '\r' || true)
  update_redirect_uris "${yaml_fqdn:-}"
}

# ── Preview ───────────────────────────────────────────────────────────

do_preview() {
  refresh_derived

  echo ""
  echo "═══════════════════════════════════════════════════════════"
  echo "  Deployment Preview"
  echo "═══════════════════════════════════════════════════════════"

  # ── Subscription & Location ──────────────────────────────────────
  echo ""
  echo "── Subscription & Location ─────────────────────────────────"
  echo "  Subscription:   ${AZURE_SUBSCRIPTION_ID}"
  echo "  Tenant:         ${AZURE_TENANT_ID}"
  echo "  Location:       ${AZ_LOCATION}"
  echo "  Resource Group: ${AZ_CONTAINER_APP_ENV_RG}"

  # ── Storage Account ─────────────────────────────────────────────
  echo ""
  echo "── Storage Account ─────────────────────────────────────────"
  if is_reuse AZ_STORAGE_REUSE; then
    echo "  Action: REUSE existing"
  elif [[ -n "${AZ_STORAGE_NAME}" ]] && resource_exists az storage account show --name "${AZ_STORAGE_NAME}" --resource-group "${AZ_STORAGE_RG}"; then
    echo "  Action: EXISTS (will reuse)"
  else
    echo "  Action: CREATE"
  fi
  echo "  Name:   ${AZ_STORAGE_NAME:-$(generate_name "stawrea") (auto-generated)}"
  echo "  RG:     ${AZ_STORAGE_RG:-<NOT SET — add AZ_STORAGE_RG to .env>}"
  echo "  SKU:    Standard_LRS"
  echo "  Kind:   StorageV2"

  # ── ACR ──────────────────────────────────────────────────────────
  echo ""
  echo "── Container Registry (ACR) ────────────────────────────────"
  local preview_acr_name="${AZ_ACR_NAME}"
  if [[ -z "$preview_acr_name" ]]; then
    preview_acr_name="$(generate_name "acrawr") (auto-generated)"
  fi
  if is_reuse AZ_ACR_REUSE; then
    echo "  Action: REUSE existing"
  elif [[ -n "${AZ_ACR_NAME}" ]] && resource_exists az acr show --name "${AZ_ACR_NAME}" --resource-group "${AZ_ACR_RG}"; then
    echo "  Action: EXISTS (will reuse)"
  else
    echo "  Action: CREATE"
  fi
  echo "  Name:   ${preview_acr_name}"
  echo "  RG:     ${AZ_ACR_RG:-<NOT SET — add AZ_ACR_RG to .env>}"
  echo "  SKU:    Basic"
  echo "  Admin:  Enabled"

  # ── Container Apps Environment ──────────────────────────────────
  echo ""
  echo "── Container Apps Environment ──────────────────────────────"
  local preview_env_name="${AZ_CONTAINER_APP_ENV_NAME}"
  if [[ -z "$preview_env_name" ]]; then
    preview_env_name="$(generate_name "cae-awr-") (auto-generated)"
  fi
  if is_reuse AZ_CONTAINER_APP_ENV_REUSE; then
    echo "  Action: REUSE existing"
  elif [[ -n "${AZ_CONTAINER_APP_ENV_NAME}" ]] && resource_exists az containerapp env show --name "${AZ_CONTAINER_APP_ENV_NAME}" --resource-group "${AZ_CONTAINER_APP_ENV_RG}"; then
    echo "  Action: EXISTS (will reuse)"
  else
    echo "  Action: CREATE"
  fi
  echo "  Name:   ${preview_env_name}"
  echo "  RG:     ${AZ_CONTAINER_APP_ENV_RG:-<NOT SET — add AZ_CONTAINER_APP_ENV_RG to .env>}"
  echo "  SKU:    Consumption (serverless)"

  # ── Container App ───────────────────────────────────────────────
  echo ""
  echo "── Container App ───────────────────────────────────────────"
  if [[ -n "${AZ_CONTAINER_APP_NAME}" ]] && resource_exists az containerapp show --name "${AZ_CONTAINER_APP_NAME}" --resource-group "${AZ_CONTAINER_APP_ENV_RG}"; then
    echo "  Action: UPDATE existing"
  else
    echo "  Action: CREATE"
  fi
  echo "  Name:     ${AZ_CONTAINER_APP_NAME}"
  echo "  CPU:      1.0 vCPU"
  echo "  Memory:   2 Gi"
  echo "  Replicas: 1–10"
  echo "  Ingress:  External, port 8000 (nginx → API + UX)"

  # ── Container Image ─────────────────────────────────────────────
  echo ""
  echo "── Container Image ─────────────────────────────────────────"
  echo "  Registry:   ${ACR_LOGIN_SERVER}"
  echo "  Image:      ${IMAGE_NAME}:${IMAGE_TAG}"
  echo "  Dockerfile: wrappers/http-service/Dockerfile"
  echo "  Build:      Remote (az acr build)"

  # ── Managed Identity ────────────────────────────────────────────
  echo ""
  echo "── Managed Identity ────────────────────────────────────────"
  echo "  Name:         ${AZ_MI_NAME:-id-awreason-http-service}"
  echo "  Principal ID: ${AZ_MI_PRINCIPAL_ID:-<not set — run setup-identity.sh>}"
  echo "  Client ID:    ${AZ_MI_CLIENT_ID:-<not set — run setup-identity.sh>}"
  echo "  Roles:"
  echo "    • Storage Blob Data Contributor → ${AZ_STORAGE_NAME:-<storage>}"
  echo "    • Cognitive Services OpenAI User → ${AZ_AOAI_RESOURCE_NAME:-<aoai>}"
  echo "    • AcrPull → ${AZ_ACR_NAME:-<acr>}"

  # ── AOAI ────────────────────────────────────────────────────────
  echo ""
  echo "── Azure OpenAI ────────────────────────────────────────────"
  echo "  Endpoint:   ${AZURE_OPENAI_ENDPOINT}"
  echo "  Deployment: ${AZURE_OPENAI_DEPLOYMENT_O1}"
  echo "  API Ver:    ${AZURE_OPENAI_API_VERSION:-2024-12-01-preview}"
  echo "  Auth:       Managed Identity (USE_AAD_FOR_AOAI=true)"

  # ── Entra ID Auth ───────────────────────────────────────────────
  echo ""
  echo "── Entra ID Auth ────────────────────────────────────────────"
  echo "  Streamlit App:  ${AAD_APP_DISPLAY_NAME:-awreason-streamlit}"
  echo "  Client ID:      ${AAD_CLIENT_ID:-<not set — run setup-identity.sh app>}"
  echo "  API App ID:     ${AAD_API_CLIENT_ID:-<not set — run setup-identity.sh app>}"
  echo "  API Scope:      ${AAD_API_SCOPE:-<not set — run setup-identity.sh app>}"
  echo "  API App Role:   ${AAD_REQUIRED_APP_ROLE:-TalentMatch.Access}"
  if [[ -n "${AAD_CLIENT_SECRET:-}" ]]; then
    echo "  Client Secret:  <set>"
  else
    echo "  Client Secret:  <NOT SET — run setup-identity.sh app>"
  fi
  echo "  Tenant:         ${AAD_TENANT_ID:-${AZURE_TENANT_ID}}"

  # ── Readiness check ─────────────────────────────────────────────
  echo ""
  echo "═══════════════════════════════════════════════════════════"
  echo "  Readiness Check"
  echo "═══════════════════════════════════════════════════════════"

  local ready=true

  if [[ -z "${AZ_CONTAINER_APP_ENV_RG:-}" ]]; then
    echo "  ❌ AZ_CONTAINER_APP_ENV_RG is empty"
    ready=false
  else
    echo "  ✅ Container Apps resource group is set"
  fi

  if [[ -z "${AZ_STORAGE_RG:-}" ]]; then
    echo "  ❌ AZ_STORAGE_RG is empty"
    ready=false
  else
    echo "  ✅ Storage resource group is set"
  fi

  if [[ -z "${AZ_AOAI_RESOURCE_NAME:-}" || -z "${AZ_AOAI_RESOURCE_RG:-}" ]] || \
      ! resource_exists az cognitiveservices account show \
        --name "${AZ_AOAI_RESOURCE_NAME:-}" \
        --resource-group "${AZ_AOAI_RESOURCE_RG:-}"; then
    echo "  ❌ Azure AI account is not configured or was not found"
    ready=false
  else
    echo "  ✅ Azure AI account exists"
  fi

  if [[ -z "${AAD_CLIENT_ID:-}" ]]; then
    echo "  ❌ AAD_CLIENT_ID is empty — run: bash setup-identity.sh app"
    ready=false
  else
    echo "  ✅ AAD_CLIENT_ID is set"
  fi

  if [[ -z "${AAD_CLIENT_SECRET:-}" ]]; then
    echo "  ❌ AAD_CLIENT_SECRET is empty — run: bash setup-identity.sh app"
    ready=false
  else
    echo "  ✅ AAD_CLIENT_SECRET is set"
  fi

  if [[ "${AUTH_MODE:-none}" == "entra" ]]; then
    if [[ -z "${AAD_API_CLIENT_ID:-}" || -z "${AAD_API_SCOPE:-}" ]]; then
      echo "  ❌ Entra API registration is incomplete — run: bash setup-identity.sh app"
      ready=false
    else
      echo "  ✅ Entra API registration is configured"
    fi
  fi

  if [[ -z "${AZ_MI_PRINCIPAL_ID:-}" ]] || \
      ! resource_exists az identity show \
        --name "${AZ_MI_NAME:-id-awreason-http-service}" \
        --resource-group "${AZ_CONTAINER_APP_ENV_RG:-}"; then
    echo "  ❌ AZ_MI_PRINCIPAL_ID is empty — run: bash setup-identity.sh mi"
    ready=false
  else
    echo "  ✅ Managed Identity configured"
  fi

  if ! command -v terraform &>/dev/null; then
    echo "  ⚠️  terraform not found — install it, or use 'deploy.sh yaml' instead"
  else
    echo "  ✅ terraform available ($(terraform version -json 2>/dev/null | grep -o '"terraform_version":"[^"]*"' | cut -d'"' -f4 || terraform version | head -1))"
  fi

  echo ""
  if [[ "$ready" == "true" ]]; then
    echo "  ✅ Ready to deploy. Run: bash deploy.sh all"
  else
    echo "  ⛔ Not ready — fix the items above first."
  fi
}

# ── Main ──────────────────────────────────────────────────────────────

ACTION="${1:-all}"

case "$ACTION" in
  preview) do_preview ;;
  infra)   do_infra; refresh_derived; print_banner ;;
  build)   do_build ;;
  apply)       do_apply ;;
  applyforce)  do_apply_force ;;
  yaml)    do_yaml ;;
  all)          do_infra; refresh_derived; print_banner; do_build; do_apply ;;
  allforce)     do_infra; refresh_derived; print_banner; do_build; do_apply_force ;;
  *)
    echo "Usage: $0 {preview|infra|build|apply|applyforce|yaml|all|allforce}" >&2
    echo "       Optional env override: DEPLOY_ENV_FILE=.env_qa $0 yaml" >&2
    echo ""
    echo "  preview     Show what will be deployed (no changes made)"
    echo "  infra       Ensure ACR, ACA env, storage exist"
    echo "  build       Build & push image via ACR Tasks"
    echo "  apply       Terraform apply (with confirmation)"
    echo "  applyforce  Terraform apply (no confirmation)"
    echo "  yaml        Deploy via ACA YAML manifest (no Terraform)"
    echo "  all         Full deploy: infra → build → apply"
    echo "  allforce    Full deploy: infra → build → apply (no confirmation)"
    exit 1
    ;;
esac
