// Starting point, not a validated template — adapt names/params to your
// subscription before deploying. Assumes an existing Container Apps
// Environment, Key Vault, and user-assigned managed identity that has been
// granted "Key Vault Secrets User" on the vault.
//
// Deploy with:
//   az deployment group create -g <rg> -f container-apps-job.bicep \
//     -p environmentName=<env> keyVaultName=<kv> containerImage=<registry>/earthquake-pipeline:latest

@description('Name of the existing Container Apps Environment')
param environmentName string

@description('Existing Key Vault name holding database-url and graph-client-secret')
param keyVaultName string

@description('Existing user-assigned managed identity, already granted Key Vault Secrets User')
param identityName string

@description('Container image, e.g. myregistry.azurecr.io/earthquake-pipeline:latest')
param containerImage string

@description('Cron schedule for the job trigger (NCronTab format)')
param cronExpression string = '17 * * * *'

resource identity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' existing = {
  name: identityName
}

resource keyVault 'Microsoft.KeyVault/vaults@2023-07-01' existing = {
  name: keyVaultName
}

resource environment 'Microsoft.App/managedEnvironments@2023-05-01' existing = {
  name: environmentName
}

resource job 'Microsoft.App/jobs@2023-05-01' = {
  name: 'earthquake-pipeline'
  location: resourceGroup().location
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${identity.id}': {}
    }
  }
  properties: {
    environmentId: environment.id
    configuration: {
      triggerType: 'Schedule'
      scheduleTriggerConfig: {
        cronExpression: cronExpression
        parallelism: 1
        replicaCompletionCount: 1
      }
      replicaTimeout: 600
      replicaRetryLimit: 1
      secrets: [
        {
          name: 'database-url'
          keyVaultUrl: '${keyVault.properties.vaultUri}secrets/database-url'
          identity: identity.id
        }
        {
          name: 'graph-client-secret'
          keyVaultUrl: '${keyVault.properties.vaultUri}secrets/graph-client-secret'
          identity: identity.id
        }
      ]
    }
    template: {
      containers: [
        {
          name: 'earthquake-pipeline'
          image: containerImage
          env: [
            { name: 'DATABASE_URL', secretRef: 'database-url' }
            { name: 'GRAPH_CLIENT_SECRET', secretRef: 'graph-client-secret' }
            { name: 'MAILER', value: 'graph' }
          ]
          resources: {
            cpu: json('0.25')
            memory: '0.5Gi'
          }
        }
      ]
    }
  }
}
