#!/bin/bash
# ============================================================
# Azure Deployment Script — Agentic Document Processing
# 
# Deploys the full solution to Azure in ~20 minutes.
# Prerequisites: Azure CLI installed, logged in (az login)
#
# Usage:
#   chmod +x deploy.sh
#   ./deploy.sh
# ============================================================

set -e  # Exit on any error

# ── Configuration ─────────────────────────────────────────────────────────────
RESOURCE_GROUP="rg-doc-agent"
LOCATION="australiaeast"
ENVIRONMENT_NAME="docagent"
ACR_NAME="${ENVIRONMENT_NAME}registry"
IMAGE_NAME="doc-agent"
IMAGE_TAG="v1"

echo "🚀 Starting Azure deployment for Agentic Document Processing..."
echo "   Resource Group : $RESOURCE_GROUP"
echo "   Location       : $LOCATION"
echo ""

# ── Step 1: Login to Azure ────────────────────────────────────────────────────
echo "📋 Step 1: Logging into Azure..."
az login
az account show --output table

# ── Step 2: Create Resource Group ────────────────────────────────────────────
echo ""
echo "📦 Step 2: Creating Resource Group..."
az group create \
  --name $RESOURCE_GROUP \
  --location $LOCATION \
  --output table

# ── Step 3: Deploy all Azure infrastructure via Bicep ─────────────────────────
echo ""
echo "🏗️  Step 3: Deploying Azure infrastructure (Bicep)..."
echo "   This creates: Storage, Service Bus, Key Vault, Container Registry,"
echo "   Cosmos DB, Container Apps, App Insights..."
echo ""

DEPLOYMENT_OUTPUT=$(az deployment group create \
  --resource-group $RESOURCE_GROUP \
  --template-file azure/main.bicep \
  --parameters @azure/parameters.json \
  --output json)

# Extract outputs
ACR_URL=$(echo $DEPLOYMENT_OUTPUT | python3 -c "import json,sys; print(json.load(sys.stdin)['properties']['outputs']['containerRegistryUrl']['value'])")
APP_URL=$(echo $DEPLOYMENT_OUTPUT | python3 -c "import json,sys; print(json.load(sys.stdin)['properties']['outputs']['containerAppUrl']['value'])")
KV_URL=$(echo $DEPLOYMENT_OUTPUT | python3 -c "import json,sys; print(json.load(sys.stdin)['properties']['outputs']['keyVaultUrl']['value'])")

echo "✅ Infrastructure deployed!"
echo "   Container Registry : $ACR_URL"
echo "   Key Vault          : $KV_URL"

# ── Step 4: Build and push Docker image ───────────────────────────────────────
echo ""
echo "🐳 Step 4: Building and pushing Docker image..."

# Login to Azure Container Registry using Managed Identity
az acr login --name $ACR_NAME

# Build the image
docker build -t $IMAGE_NAME:$IMAGE_TAG .

# Tag for Azure Container Registry
docker tag $IMAGE_NAME:$IMAGE_TAG $ACR_URL/$IMAGE_NAME:$IMAGE_TAG

# Push to ACR
docker push $ACR_URL/$IMAGE_NAME:$IMAGE_TAG

echo "✅ Image pushed to: $ACR_URL/$IMAGE_NAME:$IMAGE_TAG"

# ── Step 5: Store secrets in Key Vault ────────────────────────────────────────
echo ""
echo "🔐 Step 5: Storing secrets in Azure Key Vault..."
echo "   (In production: these come from your Azure OpenAI and Service Bus resources)"

# Get Service Bus connection string
SB_CONNECTION=$(az servicebus namespace authorization-rule keys list \
  --resource-group $RESOURCE_GROUP \
  --namespace-name "${ENVIRONMENT_NAME}-servicebus" \
  --name RootManageSharedAccessKey \
  --query primaryConnectionString -o tsv)

# Store in Key Vault
az keyvault secret set \
  --vault-name "${ENVIRONMENT_NAME}-kv" \
  --name "servicebus-connection-string" \
  --value "$SB_CONNECTION" \
  --output none

echo "   ✅ Service Bus connection string stored"

# Get App Insights connection string
AI_CONNECTION=$(az monitor app-insights component show \
  --resource-group $RESOURCE_GROUP \
  --app "${ENVIRONMENT_NAME}-insights" \
  --query connectionString -o tsv)

az keyvault secret set \
  --vault-name "${ENVIRONMENT_NAME}-kv" \
  --name "appinsights-connection-string" \
  --value "$AI_CONNECTION" \
  --output none

echo "   ✅ App Insights connection string stored"
echo ""
echo "   ⚠️  Add your Azure OpenAI key manually:"
echo "   az keyvault secret set --vault-name ${ENVIRONMENT_NAME}-kv --name openai-key --value <YOUR_KEY>"

# ── Step 6: Grant Container App access to Key Vault ───────────────────────────
echo ""
echo "🔑 Step 6: Granting Container App Managed Identity access to Key Vault..."

# Get the Container App's Managed Identity principal ID
PRINCIPAL_ID=$(az containerapp show \
  --resource-group $RESOURCE_GROUP \
  --name "${ENVIRONMENT_NAME}-api" \
  --query identity.principalId -o tsv)

# Grant Key Vault Secrets User role (read-only access to secrets)
az role assignment create \
  --role "Key Vault Secrets User" \
  --assignee $PRINCIPAL_ID \
  --scope $(az keyvault show --name "${ENVIRONMENT_NAME}-kv" --query id -o tsv) \
  --output none

echo "   ✅ Managed Identity granted Key Vault Secrets User role"
echo "   (No passwords in code — identity-based access only)"

# ── Step 7: Update Container App with correct image ───────────────────────────
echo ""
echo "🔄 Step 7: Updating Container App with our image..."

az containerapp update \
  --resource-group $RESOURCE_GROUP \
  --name "${ENVIRONMENT_NAME}-api" \
  --image "$ACR_URL/$IMAGE_NAME:$IMAGE_TAG" \
  --output none

echo "✅ Container App updated"

# ── Step 8: Health check ──────────────────────────────────────────────────────
echo ""
echo "🏥 Step 8: Running health check..."
sleep 10  # Give the app time to start

HTTP_STATUS=$(curl -s -o /dev/null -w "%{http_code}" "https://$APP_URL/health")
if [ "$HTTP_STATUS" = "200" ]; then
  echo "✅ Health check passed!"
else
  echo "⚠️  Health check returned $HTTP_STATUS — app may still be starting"
fi

# ── Deployment Summary ────────────────────────────────────────────────────────
echo ""
echo "════════════════════════════════════════════════════════"
echo "✅ DEPLOYMENT COMPLETE"
echo "════════════════════════════════════════════════════════"
echo ""
echo "  🌐 API URL        : https://$APP_URL"
echo "  📖 Swagger UI     : https://$APP_URL/docs"
echo "  📊 Metrics        : https://$APP_URL/metrics"
echo "  🔍 Azure Portal   : https://portal.azure.com"
echo ""
echo "  Test your deployment:"
echo "  curl https://$APP_URL/health"
echo ""
echo "  curl -X POST https://$APP_URL/documents/text \\"
echo "    -H 'Content-Type: application/json' \\"
echo "    -d '{\"filename\": \"invoice.txt\", \"text_content\": \"INVOICE\\nVendor: Acme Ltd\\nAmount: 5000\"}'"
echo ""
echo "  🗑️  To clean up ALL resources (avoid any charges):"
echo "  az group delete --name $RESOURCE_GROUP --yes --no-wait"
echo "════════════════════════════════════════════════════════"
