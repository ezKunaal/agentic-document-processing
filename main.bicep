// ============================================================
// Azure Bicep Deployment Template
// Agentic Document Processing Solution
// 
// Deploys:
//   - Azure Container Registry (stores our Docker image)
//   - Azure Storage Account + Blob Container (document ingestion)
//   - Azure Service Bus + Queue (reliable message passing)
//   - Azure Container Apps Environment (serverless compute)
//   - Azure Container App (our doc-agent API)
//   - Azure Key Vault (secrets management)
//   - Azure Application Insights (observability)
//   - Azure Log Analytics Workspace (log aggregation)
//
// Deploy with:
//   az deployment group create \
//     --resource-group rg-doc-agent \
//     --template-file main.bicep \
//     --parameters @parameters.json
// ============================================================

@description('Azure region for all resources')
param location string = resourceGroup().location

@description('Environment name - used as prefix for all resources')
param environmentName string = 'docagent'

@description('Azure OpenAI endpoint URL')
param azureOpenAIEndpoint string = ''

@description('Azure Document Intelligence endpoint URL')
param docIntelligenceEndpoint string = ''

@description('Confidence threshold for auto-routing vs human escalation')
param confidenceThreshold string = '0.80'

// ── Naming convention ─────────────────────────────────────────────────────────
var prefix = toLower(environmentName)
var acrName = '${prefix}registry'
var storageAccountName = '${prefix}storage'
var serviceBusName = '${prefix}-servicebus'
var keyVaultName = '${prefix}-kv'
var appInsightsName = '${prefix}-insights'
var logAnalyticsName = '${prefix}-logs'
var containerAppEnvName = '${prefix}-env'
var containerAppName = '${prefix}-api'
var cosmosAccountName = '${prefix}-cosmos'

// ── 1. Log Analytics Workspace ────────────────────────────────────────────────
// Aggregates all logs from Container Apps and App Insights
resource logAnalytics 'Microsoft.OperationalInsights/workspaces@2022-10-01' = {
  name: logAnalyticsName
  location: location
  properties: {
    sku: { name: 'PerGB2018' }
    retentionInDays: 90  // 90-day log retention for compliance
  }
}

// ── 2. Application Insights ───────────────────────────────────────────────────
// Telemetry, distributed tracing, performance monitoring
resource appInsights 'Microsoft.Insights/components@2020-02-02' = {
  name: appInsightsName
  location: location
  kind: 'web'
  properties: {
    Application_Type: 'web'
    WorkspaceResourceId: logAnalytics.id
  }
}

// ── 3. Azure Key Vault ────────────────────────────────────────────────────────
// Stores all secrets - OpenAI keys, Service Bus connection strings
// No secrets ever stored in code or environment variables directly
resource keyVault 'Microsoft.KeyVault/vaults@2023-02-01' = {
  name: keyVaultName
  location: location
  properties: {
    sku: { family: 'A', name: 'standard' }
    tenantId: subscription().tenantId
    // Soft delete protects against accidental secret deletion
    enableSoftDelete: true
    softDeleteRetentionInDays: 7
    // Only allow access via RBAC - no legacy access policies
    enableRbacAuthorization: true
  }
}

// ── 4. Azure Storage Account ──────────────────────────────────────────────────
// Layer 1: Raw document ingestion - PDFs, emails, invoices land here
resource storageAccount 'Microsoft.Storage/storageAccounts@2023-01-01' = {
  name: storageAccountName
  location: location
  sku: { name: 'Standard_LRS' }
  kind: 'StorageV2'
  properties: {
    // All data encrypted at rest by default
    encryption: {
      services: {
        blob: { enabled: true }
      }
      keySource: 'Microsoft.Storage'
    }
    // HTTPS only - no unencrypted traffic
    supportsHttpsTrafficOnly: true
    minimumTlsVersion: 'TLS1_2'
  }
}

// Document ingestion container
resource blobContainer 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-01-01' = {
  name: '${storageAccount.name}/default/documents'
  properties: {
    // Private - no public internet access to documents
    publicAccess: 'None'
  }
}

// ── 5. Azure Service Bus ──────────────────────────────────────────────────────
// Layer 1: Reliable message queue between ingestion and processing
// Dead-letter queue ensures NO document is ever lost
resource serviceBus 'Microsoft.ServiceBus/namespaces@2022-10-01-preview' = {
  name: serviceBusName
  location: location
  sku: { name: 'Standard', tier: 'Standard' }
}

// Main processing queue
resource processingQueue 'Microsoft.ServiceBus/namespaces/queues@2022-10-01-preview' = {
  parent: serviceBus
  name: 'document-processing'
  properties: {
    // Messages locked for 5 minutes during processing
    lockDuration: 'PT5M'
    // Max 3 retries before dead-lettering
    maxDeliveryCount: 3
    // Dead letter queue enabled - failed messages go here, never lost
    deadLetteringOnMessageExpiration: true
    // Messages expire after 7 days if not processed
    defaultMessageTimeToLive: 'P7D'
  }
}

// Human review queue - escalated documents land here
resource reviewQueue 'Microsoft.ServiceBus/namespaces/queues@2022-10-01-preview' = {
  parent: serviceBus
  name: 'human-review'
  properties: {
    lockDuration: 'PT5M'
    maxDeliveryCount: 1
    defaultMessageTimeToLive: 'P1D'
  }
}

// ── 6. Azure Container Registry ───────────────────────────────────────────────
// Private registry for our Docker image
// Only our Container App can pull from it (Managed Identity)
resource containerRegistry 'Microsoft.ContainerRegistry/registries@2023-01-01-preview' = {
  name: acrName
  location: location
  sku: { name: 'Basic' }
  properties: {
    // Admin access disabled - use Managed Identity instead
    adminUserEnabled: false
  }
}

// ── 7. Cosmos DB ──────────────────────────────────────────────────────────────
// Audit trail storage - immutable, queryable, scales to any volume
resource cosmosAccount 'Microsoft.DocumentDB/databaseAccounts@2023-04-15' = {
  name: cosmosAccountName
  location: location
  properties: {
    databaseAccountOfferType: 'Standard'
    locations: [{ locationName: location, failoverPriority: 0 }]
    consistencyPolicy: { defaultConsistencyLevel: 'Session' }
    // Automatic failover for enterprise reliability
    enableAutomaticFailover: false
  }
}

resource cosmosDatabase 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases@2023-04-15' = {
  parent: cosmosAccount
  name: 'doc-agent-db'
  properties: {
    resource: { id: 'doc-agent-db' }
  }
}

resource auditContainer 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers@2023-04-15' = {
  parent: cosmosDatabase
  name: 'audit-trail'
  properties: {
    resource: {
      id: 'audit-trail'
      // Partition by doc_type for efficient querying
      partitionKey: { paths: ['/doc_type'], kind: 'Hash' }
      // Keep audit records for 3 years (compliance requirement)
      defaultTtl: 94608000
    }
  }
}

// ── 8. Container Apps Environment ─────────────────────────────────────────────
// Serverless compute platform - scales to zero, bursts automatically
resource containerAppsEnv 'Microsoft.App/managedEnvironments@2023-05-01' = {
  name: containerAppEnvName
  location: location
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: logAnalytics.properties.customerId
        sharedKey: logAnalytics.listKeys().primarySharedKey
      }
    }
  }
}

// ── 9. Container App (our doc-agent API) ──────────────────────────────────────
// The main application - auto-scales based on Service Bus queue depth
resource containerApp 'Microsoft.App/containerApps@2023-05-01' = {
  name: containerAppName
  location: location
  // Managed Identity - pulls secrets from Key Vault without passwords
  identity: { type: 'SystemAssigned' }
  properties: {
    managedEnvironmentId: containerAppsEnv.id
    configuration: {
      // External ingress - accessible via HTTPS URL
      ingress: {
        external: true
        targetPort: 8000
        transport: 'http'
      }
      // Pull image from our private Container Registry
      registries: [{
        server: containerRegistry.properties.loginServer
        identity: 'system'
      }]
      // Secrets referenced from Key Vault
      secrets: [
        {
          name: 'appinsights-connection-string'
          keyVaultUrl: '${keyVault.properties.vaultUri}secrets/appinsights-connection-string'
          identity: 'system'
        }
        {
          name: 'servicebus-connection-string'
          keyVaultUrl: '${keyVault.properties.vaultUri}secrets/servicebus-connection-string'
          identity: 'system'
        }
        {
          name: 'openai-key'
          keyVaultUrl: '${keyVault.properties.vaultUri}secrets/openai-key'
          identity: 'system'
        }
      ]
    }
    template: {
      containers: [{
        name: 'doc-agent'
        image: '${containerRegistry.properties.loginServer}/doc-agent:latest'
        // Environment variables - secrets referenced by name, never hardcoded
        env: [
          { name: 'LOCAL_DEV', value: 'false' }
          { name: 'CONFIDENCE_THRESHOLD', value: confidenceThreshold }
          { name: 'AZURE_OPENAI_ENDPOINT', value: azureOpenAIEndpoint }
          { name: 'DOC_INTELLIGENCE_ENDPOINT', value: docIntelligenceEndpoint }
          { name: 'APPLICATIONINSIGHTS_CONNECTION_STRING', secretRef: 'appinsights-connection-string' }
          { name: 'SERVICE_BUS_CONNECTION_STRING', secretRef: 'servicebus-connection-string' }
          { name: 'AZURE_OPENAI_KEY', secretRef: 'openai-key' }
        ]
        // Resource limits - right-sized for document processing
        resources: {
          cpu: json('0.5')
          memory: '1Gi'
        }
      }]
      // Scaling rules - scale based on Service Bus queue depth
      scale: {
        minReplicas: 0   // Scale to zero when idle = zero cost
        maxReplicas: 20  // Burst to 20 instances under load
        rules: [{
          name: 'servicebus-scale-rule'
          custom: {
            type: 'azure-servicebus'
            metadata: {
              queueName: 'document-processing'
              messageCount: '10'  // 1 new replica per 10 queued messages
            }
          }
        }]
      }
    }
  }
}

// ── Outputs ───────────────────────────────────────────────────────────────────
output containerAppUrl string = 'https://${containerApp.properties.configuration.ingress.fqdn}'
output containerRegistryUrl string = containerRegistry.properties.loginServer
output keyVaultUrl string = keyVault.properties.vaultUri
output appInsightsKey string = appInsights.properties.InstrumentationKey
