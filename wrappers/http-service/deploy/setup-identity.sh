#!/usr/bin/env bash
# ══════════════════════════════════════════════════════════════════════
#  Setup Managed Identity, role assignments, and Entra ID App
#  Registration for the awreason HTTP Service.
#
#  Sources configuration from DEPLOY_ENV_FILE (repo-root .env by default) and respects
#  the AZ_*_REUSE flags:
#    REUSE=TRUE  → resource must already exist; never created/modified.
#    REUSE=FALSE → created if missing; names auto-generated if blank.
#
#  Created/discovered values are written back to the selected env file for downstream
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
#    DEPLOY_ENV_FILE=.env_qa bash setup-identity.sh mi --yes
# ══════════════════════════════════════════════════════════════════════
set -euo pipefail

# Prevent Git Bash (MSYS) from converting /subscriptions/... to C:/Program Files/Git/subscriptions/...
export MSYS_NO_PATHCONV=1

# Parse flags before validating action-specific configuration.
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

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
ENV_FILE="${DEPLOY_ENV_FILE:-${REPO_ROOT}/.env}"

# Resolve relative paths from the repository root, matching deploy.sh.
if [[ "$ENV_FILE" != /* ]]; then
  ENV_FILE="${REPO_ROOT}/${ENV_FILE}"
fi

if [[ ! -f "$ENV_FILE" ]]; then
  echo "ERROR: environment file not found at $ENV_FILE" >&2
  exit 1
fi

echo "Using environment file: $ENV_FILE"

# ── Load environment file (strip quotes) ─────────────────────────────
set -a
# shellcheck disable=SC1090
source <(sed 's/\r$//' "$ENV_FILE" | grep -v '^\s*#' | grep '=')
set +a

# ── Defaults ──────────────────────────────────────────────────────────
MI_NAME="${AZ_MI_NAME:-id-awreason-http-service}"
APP_DISPLAY_NAME="${AAD_APP_DISPLAY_NAME:-awreason-http-service}"
API_APP_DISPLAY_NAME="${AAD_API_APP_DISPLAY_NAME:-${APP_DISPLAY_NAME}-api}"
API_SCOPE_ID="${AAD_API_SCOPE_ID:-3d5295a4-2b81-4f8f-97db-dbef9b59f127}"
API_APP_ROLE_ID="${AAD_API_APP_ROLE_ID:-7f26b10f-f15a-4f0f-b4e0-5a053e1b7611}"
LOCATION="${AZ_LOCATION:-swedencentral}"

if [[ ("$ACTION" == "mi" || "$ACTION" == "all") && -z "${AZ_CONTAINER_APP_ENV_RG:-}" ]]; then
  echo "ERROR: AZ_CONTAINER_APP_ENV_RG is not set in $ENV_FILE" >&2
  exit 1
fi
MI_RG="${AZ_CONTAINER_APP_ENV_RG:-}"

# ── Set active subscription so all az commands target the right one ───
if [[ -n "${AZURE_SUBSCRIPTION_ID:-}" ]]; then
  echo "Setting active subscription: ${AZURE_SUBSCRIPTION_ID}"
  az account set --subscription "${AZURE_SUBSCRIPTION_ID}"
else
  echo "ERROR: AZURE_SUBSCRIPTION_ID is not set in $ENV_FILE" >&2
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

ensure_service_principal() {
  local app_id="$1" label="$2" sp_object_id
  sp_object_id=$(az ad sp show --id "$app_id" --query id -o tsv 2>/dev/null | tr -d '\r' || true)
  sp_object_id="${sp_object_id:-}"
  if [[ -z "$sp_object_id" ]]; then
    echo "  Creating service principal for ${label}." >&2
    az ad sp create --id "$app_id" -o none
    sp_object_id=$(az ad sp show --id "$app_id" --query id -o tsv | tr -d '\r')
  fi
  if [[ -z "$sp_object_id" ]]; then
    echo "ERROR: service principal for ${label} (${app_id}) was not found or created." >&2
    exit 1
  fi
  printf '%s' "$sp_object_id"
}

# Stage a variable for later .env update (does NOT write yet).
# Also exports to the current shell so downstream code sees new values.
stage_env_var() {
  local var_name="$1" var_value="$2"
  PENDING_ENV["$var_name"]="$var_value"
  export "${var_name}=${var_value}"
}

# Flush all staged variables to the selected env file after user confirmation.
flush_env_vars() {
  if [[ ${#PENDING_ENV[@]} -eq 0 ]]; then
    echo ""
    echo "No environment file updates needed."
    return
  fi

  echo ""
  echo "═══════════════════════════════════════════════════════════"
  echo "  Environment file updates ($ENV_FILE)"
  echo "═══════════════════════════════════════════════════════════"
  echo ""

  # Show each pending change with old → new
  for var_name in $(echo "${!PENDING_ENV[@]}" | tr ' ' '\n' | sort); do
    local new_val="${PENDING_ENV[$var_name]}"
    local old_val=""
    if grep -q "^${var_name}=" "$ENV_FILE" 2>/dev/null; then
      old_val=$(grep "^${var_name}=" "$ENV_FILE" | head -1 | cut -d= -f2-)
    fi
    if [[ "$var_name" == *SECRET* || "$var_name" == *PASSWORD* || "$var_name" == *API_KEY* ]]; then
      if [[ -n "$old_val" ]]; then
        echo "  ${var_name}=<redacted>  (set)"
      else
        echo "  ${var_name}=<redacted>  (new)"
      fi
    elif [[ "$old_val" == "$new_val" ]]; then
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
    read -rp "  Write ${#PENDING_ENV[@]} variable(s) to $ENV_FILE? [y/N] " answer
    answer="${answer//$'\r'/}"
    if [[ "${answer,,}" != "y" && "${answer,,}" != "yes" ]]; then
      echo "  Skipped. $ENV_FILE was NOT modified."
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

  echo "  ✅ Environment file updated: $ENV_FILE"
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
  echo "  Entra ID API + Streamlit App Registrations"
  echo "═══════════════════════════════════════════════════════════"

  local tenant_id="${AAD_TENANT_ID:-${AZURE_TENANT_ID:-}}"

  if [[ -z "$tenant_id" ]]; then
    echo "ERROR: AAD_TENANT_ID / AZURE_TENANT_ID not set." >&2
    exit 1
  fi

  # ── API resource registration ───────────────────────────────────────
  local existing_api_app_id=""

  if [[ -n "${AAD_API_CLIENT_ID:-}" ]]; then
    existing_api_app_id=$(az ad app show --id "$AAD_API_CLIENT_ID" --query appId -o tsv 2>/dev/null | tr -d '\r' || true)
    existing_api_app_id="${existing_api_app_id:-}"
  fi

  if [[ -z "$existing_api_app_id" ]]; then
    existing_api_app_id=$(az ad app list \
      --filter "displayName eq '${API_APP_DISPLAY_NAME}'" \
      --query "[0].appId" -o tsv 2>/dev/null | tr -d '\r' || true)
    existing_api_app_id="${existing_api_app_id:-}"
  fi

  if [[ -n "$existing_api_app_id" ]]; then
    AAD_API_CLIENT_ID="$existing_api_app_id"
    echo "  Found API app: $AAD_API_CLIENT_ID (${API_APP_DISPLAY_NAME})"
  else
    echo "  Creating API App Registration: $API_APP_DISPLAY_NAME"
    AAD_API_CLIENT_ID=$(az ad app create \
      --display-name "$API_APP_DISPLAY_NAME" \
      --sign-in-audience "AzureADMyOrg" \
      --query appId -o tsv | tr -d '\r')
    echo "  ✅ API app created: $AAD_API_CLIENT_ID"
  fi

  local unexpected_scope unexpected_role existing_scope_id existing_role_id
  unexpected_scope=$(az ad app show --id "$AAD_API_CLIENT_ID" \
    --query "api.oauth2PermissionScopes[?value!='access_as_user'].value | [0]" -o tsv | tr -d '\r')
  unexpected_role=$(az ad app show --id "$AAD_API_CLIENT_ID" \
    --query "appRoles[?value!='TalentMatch.Access'].value | [0]" -o tsv | tr -d '\r')
  if [[ -n "$unexpected_scope" || -n "$unexpected_role" ]]; then
    echo "ERROR: API app '$AAD_API_CLIENT_ID' contains scopes or roles not owned by this deployment." >&2
    echo "       Use a dedicated AAD_API_CLIENT_ID; setup will not overwrite shared API metadata." >&2
    exit 1
  fi

  existing_scope_id=$(az ad app show --id "$AAD_API_CLIENT_ID" \
    --query "api.oauth2PermissionScopes[?value=='access_as_user'].id | [0]" -o tsv | tr -d '\r')
  existing_role_id=$(az ad app show --id "$AAD_API_CLIENT_ID" \
    --query "appRoles[?value=='TalentMatch.Access'].id | [0]" -o tsv | tr -d '\r')
  if [[ -n "$existing_scope_id" && "$existing_scope_id" != "$API_SCOPE_ID" ]]; then
    echo "  Reusing existing access_as_user scope ID: $existing_scope_id"
    API_SCOPE_ID="$existing_scope_id"
  fi
  if [[ -n "$existing_role_id" && "$existing_role_id" != "$API_APP_ROLE_ID" ]]; then
    echo "  Reusing existing TalentMatch.Access role ID: $existing_role_id"
    API_APP_ROLE_ID="$existing_role_id"
  fi

  local api_sp_object_id
  api_sp_object_id=$(ensure_service_principal "$AAD_API_CLIENT_ID" "$API_APP_DISPLAY_NAME")

  local api_scope="api://${AAD_API_CLIENT_ID}/access_as_user"
  local scope_json role_json
  scope_json="[{\"adminConsentDescription\":\"Allow this application to access AWReason as the signed-in user.\",\"adminConsentDisplayName\":\"Access AWReason as a user\",\"id\":\"${API_SCOPE_ID}\",\"isEnabled\":true,\"type\":\"User\",\"userConsentDescription\":\"Allow this application to access AWReason on your behalf.\",\"userConsentDisplayName\":\"Access AWReason as you\",\"value\":\"access_as_user\"}]"
  role_json="[{\"allowedMemberTypes\":[\"Application\"],\"description\":\"Allows Talent Match to call the AWReason HTTP API.\",\"displayName\":\"Talent Match API access\",\"id\":\"${API_APP_ROLE_ID}\",\"isEnabled\":true,\"value\":\"TalentMatch.Access\"}]"

  local api_app_object_id app_config_json
  api_app_object_id=$(az ad app show --id "$AAD_API_CLIENT_ID" --query id -o tsv | tr -d '\r')
  app_config_json="{\"identifierUris\":[\"api://${AAD_API_CLIENT_ID}\"],\"api\":{\"requestedAccessTokenVersion\":2,\"oauth2PermissionScopes\":${scope_json}},\"appRoles\":${role_json}}"
  az rest \
    --method patch \
    --uri "https://graph.microsoft.com/v1.0/applications/${api_app_object_id}" \
    --headers "Content-Type=application/json" \
    --body "$app_config_json" \
    -o none
  echo "  ✅ Exposed delegated scope: $api_scope"
  echo "  ✅ Exposed application role: TalentMatch.Access"

  # ── Streamlit confidential web client registration ─────────────────
  local existing_app_id=""

  if [[ -n "${AAD_CLIENT_ID:-}" ]]; then
    # Verify it exists
    existing_app_id=$(az ad app show --id "$AAD_CLIENT_ID" --query appId -o tsv 2>/dev/null | tr -d '\r' || true)
    existing_app_id="${existing_app_id:-}"
    if [[ "$existing_app_id" == "$AAD_API_CLIENT_ID" ]]; then
      echo "  Ignoring AAD_CLIENT_ID because it identifies the API resource app."
      existing_app_id=""
    fi
    if [[ -n "$existing_app_id" ]]; then
      echo "  App Registration already exists: $existing_app_id"
      echo "  Display name: $(az ad app show --id "$AAD_CLIENT_ID" --query displayName -o tsv | tr -d '\r')"
    fi
  fi

  if [[ -z "$existing_app_id" ]]; then
    # Search by display name
    existing_app_id=$(az ad app list \
      --filter "displayName eq '${APP_DISPLAY_NAME}'" \
      --query "[0].appId" -o tsv 2>/dev/null | tr -d '\r' || true)
    existing_app_id="${existing_app_id:-}"
  fi

  if [[ "$existing_app_id" == "$AAD_API_CLIENT_ID" ]]; then
    echo "ERROR: API and Streamlit registrations must use different application IDs." >&2
    exit 1
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

  fi

  local streamlit_sp_object_id
  streamlit_sp_object_id=$(ensure_service_principal "$AAD_CLIENT_ID" "$APP_DISPLAY_NAME")

  # ── Set redirect URIs ───────────────────────────────────────────────
  echo ""
  echo "── Configuring redirect URIs ───────────────────────────────"

  # Preserve existing redirects, then add local, explicit, and discovered ACA URLs.
  local redirect_uris=()
  local existing_redirect
  while IFS= read -r existing_redirect; do
    [[ -n "$existing_redirect" ]] && redirect_uris+=("$existing_redirect")
  done < <(az ad app show --id "$AAD_CLIENT_ID" --query "web.redirectUris" -o tsv | tr -d '\r')

  add_redirect_uri() {
    local candidate="$1" current
    for current in "${redirect_uris[@]}"; do
      [[ "$current" == "$candidate" ]] && return
    done
    redirect_uris+=("$candidate")
  }

  add_redirect_uri "http://localhost:8501/"
  if [[ -n "${STREAMLIT_REDIRECT_URI:-}" ]]; then
    local explicit_redirect="${STREAMLIT_REDIRECT_URI}"
    [[ "$explicit_redirect" != */ ]] && explicit_redirect="${explicit_redirect}/"
    add_redirect_uri "$explicit_redirect"
    echo "  Explicit redirect URI: $explicit_redirect"
  fi

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
    add_redirect_uri "https://${aca_fqdn}/"
    echo "  ACA FQDN detected: $aca_fqdn"
  else
    echo "  ⚠️  ACA not yet deployed – only localhost redirect set."
    echo "     Re-run after deploy to add the ACA redirect URI:"
    echo "     bash setup-identity.sh app"
  fi

  az ad app update \
    --id "$AAD_CLIENT_ID" \
    --web-redirect-uris "${redirect_uris[@]}" \
    -o none
  echo "  Redirect URIs: ${redirect_uris[*]}"

  # ── Grant the Streamlit client delegated access to the API ──────────
  echo ""
  echo "── API Permission: AWReason access_as_user ─────────────────"

  local configured_scope_id
  configured_scope_id=$(az ad app show \
    --id "$AAD_CLIENT_ID" \
    --query "requiredResourceAccess[?resourceAppId=='${AAD_API_CLIENT_ID}'].resourceAccess[] | [?type=='Scope' && id=='${API_SCOPE_ID}'].id | [0]" \
    -o tsv | tr -d '\r')
  if [[ "$configured_scope_id" != "$API_SCOPE_ID" ]]; then
    az ad app permission add \
      --id "$AAD_CLIENT_ID" \
      --api "$AAD_API_CLIENT_ID" \
      --api-permissions "${API_SCOPE_ID}=Scope" \
      -o none
    configured_scope_id=$(az ad app show \
      --id "$AAD_CLIENT_ID" \
      --query "requiredResourceAccess[?resourceAppId=='${AAD_API_CLIENT_ID}'].resourceAccess[] | [?type=='Scope' && id=='${API_SCOPE_ID}'].id | [0]" \
      -o tsv | tr -d '\r')
  fi
  if [[ "$configured_scope_id" != "$API_SCOPE_ID" ]]; then
    echo "ERROR: delegated permission '$api_scope' was not configured on Streamlit app '$AAD_CLIENT_ID'." >&2
    exit 1
  fi
  echo "  ✅ Streamlit client configured for $api_scope"

  local consent_scope
  consent_scope=$(az ad app permission list-grants \
    --id "$AAD_CLIENT_ID" \
    --query "[?resourceId=='${api_sp_object_id}' && contains(scope, 'access_as_user')].scope | [0]" \
    -o tsv 2>/dev/null | tr -d '\r' || true)
  if [[ ! " $consent_scope " =~ [[:space:]]access_as_user[[:space:]] ]]; then
    if az ad app permission grant \
        --id "$AAD_CLIENT_ID" \
        --api "$AAD_API_CLIENT_ID" \
        --scope "access_as_user" \
        -o none 2>/dev/null; then
      consent_scope="access_as_user"
    else
      echo "  ⚠️  Tenant-wide delegated consent was not granted by the current principal."
      echo "     A tenant administrator can grant consent later, or users can consent"
      echo "     interactively if tenant user-consent policy permits it."
    fi
  fi
  if [[ " $consent_scope " =~ [[:space:]]access_as_user[[:space:]] ]]; then
    echo "  ✅ Tenant consent granted for Streamlit delegated access."
  else
    echo "  Delegated permission configured; consent remains pending."
  fi

  # ── Optional Talent Match service-principal role assignment ─────────
  if [[ -n "${TALENT_MATCH_CLIENT_ID:-}" ]]; then
    echo ""
    echo "── Application Permission: Talent Match ────────────────────"
    local talent_sp_object_id existing_assignment
    talent_sp_object_id=$(ensure_service_principal "$TALENT_MATCH_CLIENT_ID" "Talent Match")

    existing_assignment=$(az rest \
      --method get \
      --uri "https://graph.microsoft.com/v1.0/servicePrincipals/${talent_sp_object_id}/appRoleAssignments" \
      --query "value[?resourceId=='${api_sp_object_id}' && appRoleId=='${API_APP_ROLE_ID}'].id | [0]" \
      -o tsv 2>/dev/null | tr -d '\r' || true)
    existing_assignment="${existing_assignment:-}"

    if [[ -n "$existing_assignment" ]]; then
      echo "  TalentMatch.Access is already assigned."
    else
      az rest \
        --method post \
        --uri "https://graph.microsoft.com/v1.0/servicePrincipals/${talent_sp_object_id}/appRoleAssignments" \
        --headers "Content-Type=application/json" \
        --body "{\"principalId\":\"${talent_sp_object_id}\",\"resourceId\":\"${api_sp_object_id}\",\"appRoleId\":\"${API_APP_ROLE_ID}\"}" \
        -o none
      echo "  ✅ TalentMatch.Access assigned to $TALENT_MATCH_CLIENT_ID"
    fi
  else
    echo "  TALENT_MATCH_CLIENT_ID is not set; skipping service app-role assignment."
  fi

  # ── Generate client secret ──────────────────────────────────────────
  echo ""
  echo "── Client Secret ───────────────────────────────────────────"

  # Stage AAD_CLIENT_ID early so it's persisted even if secret generation fails
  stage_env_var "AAD_CLIENT_ID" "$AAD_CLIENT_ID"
  stage_env_var "AAD_API_CLIENT_ID" "$AAD_API_CLIENT_ID"
  stage_env_var "AAD_API_SCOPE" "$api_scope"
  stage_env_var "AAD_AUDIENCE" "$AAD_API_CLIENT_ID"
  stage_env_var "AAD_REQUIRED_SCOPE" "access_as_user"
  stage_env_var "AAD_REQUIRED_APP_ROLE" "TalentMatch.Access"

  if [[ -n "${AAD_CLIENT_SECRET:-}" && "${ROTATE_AAD_CLIENT_SECRET:-FALSE}" != "TRUE" ]]; then
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
      if ! AAD_CLIENT_SECRET=$(az ad app credential reset \
          --id "$AAD_CLIENT_ID" \
          --append \
          --display-name "deploy-$(date +%Y%m%d)" \
          --end-date "$secret_end" \
          --query password -o tsv | tr -d '\r'); then
        echo "  ⚠️  ${label} lifetime rejected by tenant policy."
        AAD_CLIENT_SECRET=""
        continue
      fi
      if [[ -z "$AAD_CLIENT_SECRET" ]]; then
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
  stage_env_var "AAD_API_APP_DISPLAY_NAME" "$API_APP_DISPLAY_NAME"
  stage_env_var "AAD_API_SCOPE_ID" "$API_SCOPE_ID"
  stage_env_var "AAD_API_APP_ROLE_ID" "$API_APP_ROLE_ID"

  echo ""
  echo "✅ App Registration setup complete."
  echo "   API App ID:       $AAD_API_CLIENT_ID"
  echo "   Streamlit App ID: $AAD_CLIENT_ID"
  echo "   Delegated scope:  $api_scope"
  echo "   Application role: TalentMatch.Access"
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
  echo "  Resource Group: ${MI_RG:-<not set>}"
  echo "  REUSE:          ${AZ_IDENTITIES_REUSE:-FALSE}"
  if [[ -z "$MI_RG" ]]; then
    echo "  Status:         NOT CHECKED (AZ_CONTAINER_APP_ENV_RG is not set)"
  elif resource_exists az identity show --name "$MI_NAME" --resource-group "$MI_RG"; then
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
  echo "── App Registrations ───────────────────────────────────────"
  echo "  AAD_API_CLIENT_ID: ${AAD_API_CLIENT_ID:-<not set>}"
  echo "  AAD_CLIENT_ID:  ${AAD_CLIENT_ID:-<not set>}"
  echo "  AAD_TENANT_ID:  ${AAD_TENANT_ID:-<not set>}"
  if [[ -n "${AAD_CLIENT_SECRET:-}" ]]; then
    echo "  AAD_CLIENT_SECRET: <set>"
  else
    echo "  AAD_CLIENT_SECRET: <not set>"
  fi
  if [[ -n "${AAD_API_CLIENT_ID:-}" ]]; then
    local api_name api_scope_id api_role_id api_sp_object_id
    api_name=$(az ad app show --id "$AAD_API_CLIENT_ID" --query displayName -o tsv 2>/dev/null | tr -d '\r' || true)
    api_scope_id=$(az ad app show --id "$AAD_API_CLIENT_ID" --query "api.oauth2PermissionScopes[?value=='access_as_user'].id | [0]" -o tsv 2>/dev/null | tr -d '\r' || true)
    api_role_id=$(az ad app show --id "$AAD_API_CLIENT_ID" --query "appRoles[?value=='TalentMatch.Access'].id | [0]" -o tsv 2>/dev/null | tr -d '\r' || true)
    api_sp_object_id=$(az ad sp show --id "$AAD_API_CLIENT_ID" --query id -o tsv 2>/dev/null | tr -d '\r' || true)
    echo "  API Display:    ${api_name:-NOT FOUND}"
    echo "  access_as_user: ${api_scope_id:-NOT CONFIGURED}"
    echo "  TalentMatch.Access: ${api_role_id:-NOT CONFIGURED}"
  fi
  if [[ -n "${AAD_CLIENT_ID:-}" ]]; then
    local app_name configured_scope_id consent_scope
    app_name=$(az ad app show --id "$AAD_CLIENT_ID" --query displayName -o tsv 2>/dev/null | tr -d '\r' || true)
    app_name="${app_name:-NOT FOUND}"
    echo "  Client Display: $app_name"

    local redirects
    redirects=$(az ad app show --id "$AAD_CLIENT_ID" --query "web.redirectUris" -o tsv 2>/dev/null | tr -d '\r' || true)
    redirects="${redirects:-none}"
    echo "  Redirect URIs:  $redirects"

    configured_scope_id=$(az ad app show --id "$AAD_CLIENT_ID" \
      --query "requiredResourceAccess[?resourceAppId=='${AAD_API_CLIENT_ID:-}'].resourceAccess[] | [?type=='Scope'].id | [0]" \
      -o tsv 2>/dev/null | tr -d '\r' || true)
    echo "  Delegated permission: ${configured_scope_id:-NOT CONFIGURED}"

    consent_scope=$(az ad app permission list-grants --id "$AAD_CLIENT_ID" \
      --query "[?resourceId=='${api_sp_object_id:-}' && contains(scope, 'access_as_user')].scope | [0]" \
      -o tsv 2>/dev/null | tr -d '\r' || true)
    if [[ " $consent_scope " =~ [[:space:]]access_as_user[[:space:]] ]]; then
      echo "  Tenant consent: GRANTED"
    else
      echo "  Tenant consent: PENDING"
    fi
  fi

  if [[ -n "${TALENT_MATCH_CLIENT_ID:-}" && -n "${api_sp_object_id:-}" ]]; then
    local talent_sp_object_id talent_assignment
    talent_sp_object_id=$(az ad sp show --id "$TALENT_MATCH_CLIENT_ID" --query id -o tsv 2>/dev/null | tr -d '\r' || true)
    talent_assignment=$(az rest --method get \
      --uri "https://graph.microsoft.com/v1.0/servicePrincipals/${talent_sp_object_id}/appRoleAssignments" \
      --query "value[?resourceId=='${api_sp_object_id}' && appRoleId=='${API_APP_ROLE_ID}'].id | [0]" \
      -o tsv 2>/dev/null | tr -d '\r' || true)
    if [[ -n "$talent_assignment" ]]; then
      echo "  Talent Match assignment: GRANTED"
    else
      echo "  Talent Match assignment: NOT GRANTED"
    fi
  else
    echo "  Talent Match assignment: NOT CHECKED (TALENT_MATCH_CLIENT_ID is not set)"
  fi

  echo ""
  echo "── Role Assignments ────────────────────────────────────────"
  if [[ -n "$MI_RG" ]] && resource_exists az identity show --name "$MI_NAME" --resource-group "$MI_RG"; then
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
