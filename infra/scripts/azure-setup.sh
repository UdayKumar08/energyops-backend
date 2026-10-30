#!/usr/bin/env bash
# One-time Azure setup for EnergyOps AI. Run it in Azure Cloud Shell (bash) as the subscription
# owner. It is safe to run again. It creates no application resources and prints no secrets.
#
#   - registers the resource providers Terraform needs
#   - creates a private, versioned storage account and container for Terraform state
#   - creates an app registration for GitHub Actions with a federated credential (no password)
#   - gives that identity the roles Terraform needs
#
# Usage: SUB=<subscription id> REPO=<owner/repo> ENVIRONMENT=dev bash azure-setup.sh
set -euo pipefail

SUB="${SUB:?set SUB to the subscription id}"
REPO="${REPO:?set REPO to owner/repo}"
ENVIRONMENT="${ENVIRONMENT:-dev}"
LOC="${LOC:-westeurope}"
LOC_FALLBACKS="${LOC_FALLBACKS:-northeurope swedencentral germanywestcentral francecentral uksouth}"
STATE_RG="rg-energyops-tfstate"
# Deterministic so a second run finds the same account. Lowercase letters and digits, 24 max.
SA="stenergyopstf$(echo "$SUB" | tr -d '-' | cut -c1-8)"
APP_NAME="energyops-github-deploy"

az account set --subscription "$SUB"
TENANT="$(az account show --query tenantId -o tsv)"

echo "Registering resource providers"
for p in Microsoft.App Microsoft.ContainerRegistry Microsoft.DBforPostgreSQL Microsoft.KeyVault \
         Microsoft.OperationalInsights Microsoft.Network Microsoft.ManagedIdentity Microsoft.Storage \
         Microsoft.Authorization Microsoft.Consumption; do
  az provider register --namespace "$p" >/dev/null
done

echo "Terraform state storage"
az group show -n "$STATE_RG" -o none 2>/dev/null || az group create -n "$STATE_RG" -l "$LOC" -o none
# Some subscriptions (free trials especially) are refused in busy regions. Try each in turn and
# use the first that accepts the storage account. The same region is then used for everything.
if az storage account show -n "$SA" -g "$STATE_RG" -o none 2>/dev/null; then
  LOC="$(az storage account show -n "$SA" -g "$STATE_RG" --query location -o tsv)"
else
  CREATED=""
  for L in $LOC $LOC_FALLBACKS; do
    echo "  trying $L"
    if az storage account create -n "$SA" -g "$STATE_RG" -l "$L" --sku Standard_LRS \
        --allow-blob-public-access false --min-tls-version TLS1_2 -o none; then
      LOC="$L"; CREATED=1; break
    fi
  done
  [ -n "$CREATED" ] || { echo "No region accepted the storage account"; exit 1; }
fi
az storage account blob-service-properties update --account-name "$SA" -g "$STATE_RG" \
  --enable-versioning true -o none
az storage container create --account-name "$SA" -n tfstate --auth-mode key -o none

echo "Deploy identity for GitHub"
APP_ID="$(az ad app list --display-name "$APP_NAME" --query '[0].appId' -o tsv)"
if [ -z "$APP_ID" ]; then
  APP_ID="$(az ad app create --display-name "$APP_NAME" --query appId -o tsv)"
fi
if [ -z "$(az ad sp list --filter "appId eq '$APP_ID'" --query '[0].id' -o tsv)" ]; then
  az ad sp create --id "$APP_ID" -o none
fi
SP_OBJ="$(az ad sp show --id "$APP_ID" --query id -o tsv)"

# GitHub now puts numeric IDs in the token subject (repo:owner@ownerId/name@repoId:environment:x),
# so the sign-in only matches a credential that has them. Set REPO_SUBJECT to that form, and the
# plain-name form is created as well.
make_cred() {
  local name="$1" subject="$2"
  if [ -z "$(az ad app federated-credential list --id "$APP_ID" --query "[?name=='$name'].name" -o tsv)" ]; then
    az ad app federated-credential create --id "$APP_ID" --parameters "{
      \"name\":\"$name\",\"issuer\":\"https://token.actions.githubusercontent.com\",
      \"subject\":\"$subject\",
      \"audiences\":[\"api://AzureADTokenExchange\"]}" -o none
  fi
}
make_cred "github-$ENVIRONMENT" "repo:$REPO:environment:$ENVIRONMENT"
if [ -n "${REPO_SUBJECT:-}" ]; then
  make_cred "github-$ENVIRONMENT-ids" "repo:$REPO_SUBJECT:environment:$ENVIRONMENT"
fi

echo "Roles"
for r in "Contributor" "User Access Administrator"; do
  az role assignment create --assignee-object-id "$SP_OBJ" --assignee-principal-type ServicePrincipal \
    --role "$r" --scope "/subscriptions/$SUB" -o none
done
az role assignment create --assignee-object-id "$SP_OBJ" --assignee-principal-type ServicePrincipal \
  --role "Storage Blob Data Contributor" \
  --scope "$(az storage account show -n "$SA" -g "$STATE_RG" --query id -o tsv)" -o none

echo
echo "Add these as variables on the GitHub environment '$ENVIRONMENT' (none of them is a secret):"
echo "AZURE_CLIENT_ID=$APP_ID"
echo "AZURE_TENANT_ID=$TENANT"
echo "AZURE_SUBSCRIPTION_ID=$SUB"
echo "TFSTATE_RESOURCE_GROUP=$STATE_RG"
echo "TFSTATE_STORAGE_ACCOUNT=$SA"
echo "TFSTATE_CONTAINER=tfstate"
echo "AZURE_LOCATION=$LOC"
echo "VAULT_ADMIN_OBJECT_IDS=[\"$(az ad signed-in-user show --query id -o tsv)\"]"
