#!/usr/bin/env bash
# ══════════════════════════════════════════════════════════════════════
#  Setup Managed Identity, role assignments, and Entra ID App
#  Registration for the awreason HTTP Service.
#
#  Sources configuration from the repo-root .env file and respects
#  the AZ_*_REUSE flags:
#    REUSE=TRUE  → resource must already exist; never created/modified.
#    REUSE=FALSE → created if missing; names auto-generated if blank.
#
#  Created/discovered values are written back to .env for downstream
#  scripts (deploy.sh, gen-tfvars.sh) to consume.
#
#  Usage:
#    cd wrappers/http-service/deploy
#    bash setup-identity.sh              # create all (prompts before .env update)
#    bash setup-identity.sh --yes        # create all, auto-approve .env updates
#    bash setup-identity.sh mi           # managed identity + roles only
#    bash setup-identity.sh app          # app registration only
#    bash setup-identity.sh status       # show current state
#    bash setup-identity.sh mi --yes     # MI + roles, auto-approve
# ══════════════════════════════════════════════════════════════════════
set -euo pipefail

# Prevent Git Bash (MSYS) from converting /subscriptions/... to C:/Program Files/Git/subscriptions/...
export MSYS_NO_PATHCONV=1

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
ENV_FILE="${REPO_ROOT}/.env"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "ERROR: .env file not found at $ENV_FILE" >&2
  exit 1
fi

# ── Load .env (strip quotes) ─────────────────────────────────────────
set -a
# shellcheck disable=SC1090
source <(sed 's/\r$//' "$ENV_FILE" | grep -v '^\s*#' | grep '=')
set +a

# ── Defaults ──────────────────────────────────────────────────────────
MI_NAME="${AZ_MI_NAME:-id-awreason-http-service}"
APP_DISPLAY_NAME="${AAD_APP_DISPLAY_NAME:-awreason-http-service}"
LOCATION="${AZ_LOCATION:-swedencentral}"

if [[ -z "${AZ_CONTAINER_APP_ENV_RG:-}" ]]; then
  echo "ERROR: AZ_CONTAINER_APP_ENV_RG is not set in .env" >&2
  exit 1
fi
MI_RG="${AZ_CONTAINER_APP_ENV_RG}"

# ── Set active subscription so all az commands target the right one ───
if [[ -n "${AZURE_SUBSCRIPTION_ID:-}" ]]; then
  echo "Setting active subscription: ${AZURE_SUBSCRIPTION_ID}"
  az account set --subscription "${AZURE_SUBSCRIPTION_ID}"
else
  echo "ERROR: AZURE_SUBSCRIPTION_ID is not set in .env" >&2
  exit 1
fi

# ── Helpers ───────────────────────────────────────────────────────────

# Pending .env updates: associative array  var_name → new_value
declare -A PENDING_ENV=()

is_reuse() {
  local val="${!1:-FALSE}"
  [[ "${val^^}" == "TRUE" ]]
}

resource_exists() {
  "$@" &>/dev/null
}

# Stage a variable for later .env update (does NOT write yet).
# Also exports to the current shell so downstream code sees new values.
stage_env_var() {
  local var_name="$1" var_value="$2"
  PENDING_ENV["$var_name"]="$var_value"
  export "${var_name}=${var_value}"
}

# Flush all staged variables to .env after user confirmation.
flush_env_vars() {
  if [[ ${#PENDING_ENV[@]} -eq 0 ]]; then
    echo ""
    echo "No .env updates needed."
    return
  fi

  echo ""
  echo "═══════════════════════════════════════════════════════════"
  echo "  .env updates ($ENV_FILE)"
  echo "═══════════════════════════════════════════════════════════"
  echo ""

  # Show each pending change with old → new
  for var_name in $(echo "${!PENDING_ENV[@]}" | tr ' ' '\n' | sort); do
    local new_val="${PENDING_ENV[$var_name]}"
    local old_val=""
    if grep -q "^${var_name}=" "$ENV_FILE" 2>/dev/null; then
      old_val=$(grep "^${var_name}=" "$ENV_FILE" | head -1 | cut -d= -f2-)
    fi
    if [[ "$old_val" == "$new_val" ]]; then
      echo "  ${var_name}=${new_val}  (unchanged)"
    elif [[ -z "$old_val" ]]; then
      echo "  ${var_name}=${new_val}  (new)"
    else
      echo "  ${var_name}=${new_val}  (was: ${old_val})"
    fi
  done

  echo ""

  if [[ "$AUTO_APPROVE" == "true" ]]; then
    echo "  --yes flag set → writing ${#PENDING_ENV[@]} variable(s)."
  else
    read -rp "  Write ${#PENDING_ENV[@]} variable(s) to .env? [y/N] " answer
    if [[ "${answer,,}" != "y" && "${answer,,}" != "yes" ]]; then
      echo "  Skipped. .env was NOT modified."
      return
    fi
  fi

  for var_name in "${!PENDING_ENV[@]}"; do
    local var_value="${PENDING_ENV[$var_name]}"
    if grep -q "^${var_name}=" "$ENV_FILE" 2>/dev/null; then
      sed -i "s|^${var_name}=.*|${var_name}=${var_value}|" "$ENV_FILE"
    else
      echo "${var_name}=${var_value}" >> "$ENV_FILE"
    fi
  done

  echo "  ✅ .env updated."
}

# ══════════════════════════════════════════════════════════════════════
#  Managed Identity + Role Assignments
# ══════════════════════════════════════════════════════════════════════

setup_managed_identity() {
  echo ""
  echo "═══════════════════════════════════════════════════════════"
  echo "  Managed Identity & Role Assignments"
  echo "═══════════════════════════════════════════════════════════"

  local mi_principal_id mi_client_id mi_resource_id

  if is_reuse AZ_IDENTITIES_REUSE; then
    echo "  AZ_IDENTITIES_REUSE=TRUE → using existing MI."
    if [[ -z "${AZ_MI_NAME:-}" ]]; then
      echo "  Using default MI name: $MI_NAME"
    fi
    if ! resource_exists az identity show --name "$MI_NAME" --resource-group "$MI_RG"; then
      echo "ERROR: MI '$MI_NAME' not found in RG '$MI_RG' but REUSE=TRUE." >&2
      exit 1
    fi
    mi_principal_id=$(az identity show --name "$MI_NAME" --resource-group "$MI_RG" --query principalId -o tsv | tr -d '\r')
    mi_client_id=$(az identity show --name "$MI_NAME" --resource-group "$MI_RG" --query clientId -o tsv | tr -d '\r')
    echo "  MI principal: $mi_principal_id"
    echo "  MI client:    $mi_client_id"
    stage_env_var "AZ_MI_NAME" "$MI_NAME"
    stage_env_var "AZ_MI_PRINCIPAL_ID" "$mi_principal_id"
    stage_env_var "AZ_MI_CLIENT_ID" "$mi_client_id"
  else
    # ── Create MI ───────────────────────────────────────────────────────
    echo ""
    echo "── Creating Managed Identity: $MI_NAME ─────────────────────"

    if resource_exists az identity show --name "$MI_NAME" --resource-group "$MI_RG"; then
      echo "  MI '$MI_NAME' already exists – reusing."
    else
      az identity create \
        --name "$MI_NAME" \
        --resource-group "$MI_RG" \
        --location "$LOCATION" \
        -o none
      echo "  ✅ MI created: $MI_NAME"
    fi

    mi_principal_id=$(az identity show --name "$MI_NAME" --resource-group "$MI_RG" --query principalId -o tsv | tr -d '\r')
    mi_client_id=$(az identity show --name "$MI_NAME" --resource-group "$MI_RG" --query clientId -o tsv | tr -d '\r')
    mi_resource_id=$(az identity show --name "$MI_NAME" --resource-group "$MI_RG" --query id -o tsv | tr -d '\r')

    echo "  Principal ID: $mi_principal_id"
    echo "  Client ID:    $mi_client_id"

    stage_env_var "AZ_MI_NAME" "$MI_NAME"
    stage_env_var "AZ_MI_PRINCIPAL_ID" "$mi_principal_id"
    stage_env_var "AZ_MI_CLIENT_ID" "$mi_client_id"
    stage_env_var "AZ_MI_RESOURCE_ID" "$mi_resource_id"
  fi

  # ── Role: Storage Blob Data Contributor ─────────────────────────────
  echo ""
  echo "── Role Assignment: Storage Blob Data Contributor ──────────"

  if [[ -z "${AZ_STORAGE_NAME:-}" ]]; then
    echo "  ⚠️  AZ_STORAGE_NAME not set – skipping storage role."
  else
    local storage_id
    storage_id=$(az storage account show \
      --name "$AZ_STORAGE_NAME" \
      --resource-group "${AZ_STORAGE_RG}" \
      --query id -o tsv 2>/dev/null | tr -d '\r' || true)
    storage_id="${storage_id:-}"

    if [[ -z "$storage_id" ]]; then
      echo "  ⚠️  Storage account '$AZ_STORAGE_NAME' not found – skipping."
    else
      if az role assignment list \
          --assignee "$mi_principal_id" \
          --scope "$storage_id" \
          --role "Storage Blob Data Contributor" \
          --query "[0].id" -o tsv 2>/dev/null | tr -d '\r' | grep -q .; then
        echo "  Role already assigned."
      else
        az role assignment create \
          --assignee-object-id "$mi_principal_id" \
          --assignee-principal-type ServicePrincipal \
          --role "Storage Blob Data Contributor" \
          --scope "$storage_id" \
          -o none
        echo "  ✅ Storage Blob Data Contributor assigned on $AZ_STORAGE_NAME"
      fi
    fi
  fi

  # ── Role: Cognitive Services OpenAI User ────────────────────────────
  echo ""
  echo "── Role Assignment: Cognitive Services OpenAI User ─────────"

  if [[ -z "${AZ_AOAI_RESOURCE_NAME:-}" ]]; then
    echo "  ⚠️  AZ_AOAI_RESOURCE_NAME not set – skipping AOAI role."
  else
    local aoai_id
    aoai_id=$(az cognitiveservices account show \
      --name "$AZ_AOAI_RESOURCE_NAME" \
      --resource-group "${AZ_AOAI_RESOURCE_RG}" \
      --query id -o tsv 2>/dev/null | tr -d '\r' || true)
    aoai_id="${aoai_id:-}"

    if [[ -z "$aoai_id" ]]; then
      echo "  ⚠️  AOAI resource '$AZ_AOAI_RESOURCE_NAME' not found – skipping."
    else
      if az role assignment list \
          --assignee "$mi_principal_id" \
          --scope "$aoai_id" \
          --role "Cognitive Services OpenAI User" \
          --query "[0].id" -o tsv 2>/dev/null | tr -d '\r' | grep -q .; then
        echo "  Role already assigned."
      else
        az role assignment create \
          --assignee-object-id "$mi_principal_id" \
          --assignee-principal-type ServicePrincipal \
          --role "Cognitive Services OpenAI User" \
          --scope "$aoai_id" \
          -o none
        echo "  ✅ Cognitive Services OpenAI User assigned on $AZ_AOAI_RESOURCE_NAME"
      fi
    fi
  fi

  echo ""
  echo "✅ Managed Identity setup complete."
}

# ══════════════════════════════════════════════════════════════════════
#  Entra ID App Registration
# ══════════════════════════════════════════════════════════════════════

setup_app_registration() {
  echo ""
  echo "═══════════════════════════════════════════════════════════"
  echo "  Entra ID App Registration"
  echo "═══════════════════════════════════════════════════════════"

  local tenant_id="${AAD_TENANT_ID:-${AZURE_TENANT_ID}}"

  if [[ -z "$tenant_id" ]]; then
    echo "ERROR: AAD_TENANT_ID / AZURE_TENANT_ID not set." >&2
    exit 1
  fi

  # ── Check if app already exists ─────────────────────────────────────
  local existing_app_id=""

  if [[ -n "${AAD_CLIENT_ID:-}" ]]; then
    # Verify it exists
    existing_app_id=$(az ad app show --id "$AAD_CLIENT_ID" --query appId -o tsv 2>/dev/null | tr -d '\r' || true)
    existing_app_id="${existing_app_id:-}"
    if [[ -n "$existing_app_id" ]]; then
      echo "  App Registration already exists: $existing_app_id"
      echo "  Display name: $(az ad app show --id "$AAD_CLIENT_ID" --query displayName -o tsv | tr -d '\r')"
    fi
  fi

  if [[ -z "$existing_app_id" ]]; then
    # Search by display name
    existing_app_id=$(az ad app list \
      --display-name "$APP_DISPLAY_NAME" \
      --query "[0].appId" -o tsv 2>/dev/null | tr -d '\r' || true)
    existing_app_id="${existing_app_id:-}"
  fi

  if [[ -n "$existing_app_id" ]]; then
    echo "  Found existing app: $existing_app_id (${APP_DISPLAY_NAME})"
    AAD_CLIENT_ID="$existing_app_id"
  else
    echo "  Creating App Registration: $APP_DISPLAY_NAME"

    # Create the app with single-tenant sign-in
    AAD_CLIENT_ID=$(az ad app create \
      --display-name "$APP_DISPLAY_NAME" \
      --sign-in-audience "AzureADMyOrg" \
      --query appId -o tsv | tr -d '\r')

    echo "  ✅ App created: $AAD_CLIENT_ID"

    # Create a Service Principal for the app
    az ad sp create --id "$AAD_CLIENT_ID" -o none 2>/dev/null || true
    echo "  ✅ Service Principal created."
  fi

  # ── Set redirect URIs ───────────────────────────────────────────────
  echo ""
  echo "── Configuring redirect URIs ───────────────────────────────"

  # Build redirect URIs: localhost for dev + ACA FQDN if known
  local redirect_uris=("http://localhost:8501/")

  # Try to discover the ACA FQDN
  local aca_fqdn=""
  if [[ -n "${AZ_CONTAINER_APP_NAME:-}" && -n "${AZ_CONTAINER_APP_ENV_RG:-}" ]]; then
    aca_fqdn=$(az containerapp show \
      --name "$AZ_CONTAINER_APP_NAME" \
      --resource-group "$AZ_CONTAINER_APP_ENV_RG" \
      --query "properties.configuration.ingress.fqdn" -o tsv 2>/dev/null | tr -d '\r' || true)
    aca_fqdn="${aca_fqdn:-}"
  fi

  if [[ -n "$aca_fqdn" ]]; then
    redirect_uris+=("https://${aca_fqdn}/")
    echo "  ACA FQDN detected: $aca_fqdn"
  else
    echo "  ⚠️  ACA not yet deployed – only localhost redirect set."
    echo "     Re-run after deploy to add the ACA redirect URI:"
    echo "     bash setup-identity.sh app"
  fi

  # Build the JSON array for the web redirect URIs
  local uri_json="["
  for i in "${!redirect_uris[@]}"; do
    [[ $i -gt 0 ]] && uri_json+=","
    uri_json+="\"${redirect_uris[$i]}\""
  done
  uri_json+="]"

  az ad app update \
    --id "$AAD_CLIENT_ID" \
    --web-redirect-uris ${redirect_uris[@]} \
    -o none 2>/dev/null || true
  echo "  Redirect URIs: ${redirect_uris[*]}"

  # ── Add Microsoft Graph User.Read permission ────────────────────────
  echo ""
  echo "── API Permissions: Microsoft Graph User.Read ──────────────"

  # Microsoft Graph appId = 00000003-0000-0000-c000-000000000000
  # User.Read permission ID = e1fe6dd8-ba31-4d61-89e7-88639da4683d
  az ad app permission add \
    --id "$AAD_CLIENT_ID" \
    --api "00000003-0000-0000-c000-000000000000" \
    --api-permissions "e1fe6dd8-ba31-4d61-89e7-88639da4683d=Scope" \
    -o none 2>/dev/null || true
  echo "  ✅ User.Read delegated permission added."

  # ── Generate client secret ──────────────────────────────────────────
  echo ""
  echo "── Client Secret ───────────────────────────────────────────"

  # Stage AAD_CLIENT_ID early so it's persisted even if secret generation fails
  stage_env_var "AAD_CLIENT_ID" "$AAD_CLIENT_ID"

  if [[ -n "${AAD_CLIENT_SECRET:-}" ]]; then
    echo "  AAD_CLIENT_SECRET already set in .env – keeping existing."
    echo "  To rotate, clear AAD_CLIENT_SECRET in .env and re-run."
  else
    # Try progressively shorter lifetimes to comply with tenant policy
    local secret_end secret_ok="" label
    # Tenant default policy may restrict passwordCredentials to P30D (30 days).
    # Try progressively shorter lifetimes: 12mo, 6mo, 3mo, 29d, 14d, 7d.
    for label in "12 months" "6 months" "3 months" "29 days" "14 days" "7 days"; do
      secret_end=$(date -u -d "+${label}" +%Y-%m-%dT%H:%M:%SZ 2>/dev/null \
                || date -u -v+${label%% *}${label##* } +%Y-%m-%dT%H:%M:%SZ 2>/dev/null)
      echo "  Generating new client secret (valid ${label}) …"
      AAD_CLIENT_SECRET=$(az ad app credential reset \
        --id "$AAD_CLIENT_ID" \
        --display-name "deploy-$(date +%Y%m%d)" \
        --end-date "$secret_end" \
        --query password -o tsv 2>&1 | tr -d '\r' || true)
      # If the output looks like an error, try shorter
      if [[ "$AAD_CLIENT_SECRET" == ERROR* || -z "$AAD_CLIENT_SECRET" ]]; then
        echo "  ⚠️  ${label} lifetime rejected by tenant policy."
        AAD_CLIENT_SECRET=""
        continue
      fi
      secret_ok=1
      break
    done

    if [[ -z "$secret_ok" ]]; then
      echo "ERROR: Could not generate client secret – tenant policy rejected all durations." >&2
      echo "       Your tenant's passwordCredentials maxLifetime may block all secrets." >&2
      echo "       Consider using certificate auth or requesting a policy exemption." >&2
      exit 1
    fi
    echo "  ✅ Client secret generated (expires: $secret_end)."
  fi

  # ── Persist to .env ─────────────────────────────────────────────────
  stage_env_var "AAD_CLIENT_SECRET" "$AAD_CLIENT_SECRET"
  stage_env_var "AAD_TENANT_ID" "$tenant_id"
  stage_env_var "AAD_APP_DISPLAY_NAME" "$APP_DISPLAY_NAME"

  echo ""
  echo "✅ App Registration setup complete."
  echo "   App ID:     $AAD_CLIENT_ID"
  echo "   Tenant:     $tenant_id"
  echo "   Display:    $APP_DISPLAY_NAME"
  echo "   Redirects:  ${redirect_uris[*]}"
}

# ══════════════════════════════════════════════════════════════════════
#  Status – show current state
# ══════════════════════════════════════════════════════════════════════

show_status() {
  echo ""
  echo "═══════════════════════════════════════════════════════════"
  echo "  Identity & App Registration Status"
  echo "═══════════════════════════════════════════════════════════"

  echo ""
  echo "── Managed Identity ────────────────────────────────────────"
  echo "  Name:           ${AZ_MI_NAME:-$MI_NAME}"
  echo "  Resource Group: $MI_RG"
  echo "  REUSE:          ${AZ_IDENTITIES_REUSE:-FALSE}"
  if resource_exists az identity show --name "$MI_NAME" --resource-group "$MI_RG"; then
    local pid cid
    pid=$(az identity show --name "$MI_NAME" --resource-group "$MI_RG" --query principalId -o tsv | tr -d '\r')
    cid=$(az identity show --name "$MI_NAME" --resource-group "$MI_RG" --query clientId -o tsv | tr -d '\r')
    echo "  Status:         EXISTS"
    echo "  Principal ID:   $pid"
    echo "  Client ID:      $cid"
  else
    echo "  Status:         NOT FOUND"
  fi

  echo ""
  echo "── App Registration ────────────────────────────────────────"
  echo "  AAD_CLIENT_ID:  ${AAD_CLIENT_ID:-<not set>}"
  echo "  AAD_TENANT_ID:  ${AAD_TENANT_ID:-<not set>}"
  echo "  AAD_CLIENT_SECRET: ${AAD_CLIENT_SECRET:+<set>}${AAD_CLIENT_SECRET:-<not set>}"
  if [[ -n "${AAD_CLIENT_ID:-}" ]]; then
    local app_name
    app_name=$(az ad app show --id "$AAD_CLIENT_ID" --query displayName -o tsv 2>/dev/null | tr -d '\r' || true)
    app_name="${app_name:-NOT FOUND}"
    echo "  Display Name:   $app_name"

    local redirects
    redirects=$(az ad app show --id "$AAD_CLIENT_ID" --query "web.redirectUris" -o tsv 2>/dev/null | tr -d '\r' || true)
    redirects="${redirects:-none}"
    echo "  Redirect URIs:  $redirects"
  fi

  echo ""
  echo "── Role Assignments ────────────────────────────────────────"
  if resource_exists az identity show --name "$MI_NAME" --resource-group "$MI_RG"; then
    local pid
    pid=$(az identity show --name "$MI_NAME" --resource-group "$MI_RG" --query principalId -o tsv | tr -d '\r')
    echo "  Checking roles for principal: $pid"
    az role assignment list --assignee "$pid" --query "[].{Role:roleDefinitionName, Scope:scope}" -o table 2>/dev/null || echo "  (unable to query)"
  else
    echo "  MI does not exist – no roles to show."
  fi
}

# ══════════════════════════════════════════════════════════════════════
#  Main
# ══════════════════════════════════════════════════════════════════════

# Parse flags
AUTO_APPROVE="false"
ACTION=""

for arg in "$@"; do
  case "$arg" in
    -y|--yes) AUTO_APPROVE="true" ;;
    -*)       echo "Unknown flag: $arg" >&2; exit 1 ;;
    *)        ACTION="$arg" ;;
  esac
done

ACTION="${ACTION:-all}"

# Ensure pending .env changes are offered even if the script exits early (set -e)
trap flush_env_vars EXIT

case "$ACTION" in
  mi)     setup_managed_identity ;;
  app)    setup_app_registration ;;
  status) show_status ;;
  all)    setup_managed_identity; setup_app_registration ;;
  *)
    echo "Usage: $0 [-y|--yes] {mi|app|status|all}" >&2
    echo ""
    echo "  mi      Create Managed Identity + role assignments"
    echo "  app     Create/configure Entra ID App Registration"
    echo "  status  Show current state of identity resources"
    echo "  all     Run both mi and app (default)"
    echo ""
    echo "  -y, --yes   Auto-approve .env updates (no interactive prompt)"
    exit 1
    ;;
esac
