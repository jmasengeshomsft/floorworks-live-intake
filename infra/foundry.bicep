targetScope = 'resourceGroup'

param location string
param accountName string
param projectName string
param tags object = {}
param notetakerModel string = 'gpt-5.6-sol'
param notetakerModelVersion string = '2026-07-09'

resource account 'Microsoft.CognitiveServices/accounts@2025-04-01-preview' = {
  name: accountName
  location: location
  tags: tags
  kind: 'AIServices'
  sku: { name: 'S0' }
  identity: { type: 'SystemAssigned' }
  properties: {
    customSubDomainName: accountName
    allowProjectManagement: true
    publicNetworkAccess: 'Enabled'
  }
}

resource project 'Microsoft.CognitiveServices/accounts/projects@2025-04-01-preview' = {
  parent: account
  name: projectName
  location: location
  identity: { type: 'SystemAssigned' }
  properties: {}
}

resource gpt41 'Microsoft.CognitiveServices/accounts/deployments@2024-10-01' = {
  parent: account
  name: 'gpt-4.1'
  dependsOn: [project]
  sku: { name: 'GlobalStandard', capacity: 50 }
  properties: { model: { format: 'OpenAI', name: 'gpt-4.1', version: '2025-04-14' } }
}

resource embed 'Microsoft.CognitiveServices/accounts/deployments@2024-10-01' = {
  parent: account
  name: 'text-embedding-3-small'
  dependsOn: [gpt41]
  sku: { name: 'Standard', capacity: 50 }
  properties: { model: { format: 'OpenAI', name: 'text-embedding-3-small', version: '1' } }
}

resource notetaker 'Microsoft.CognitiveServices/accounts/deployments@2024-10-01' = {
  parent: account
  name: notetakerModel
  dependsOn: [embed]
  sku: { name: 'GlobalStandard', capacity: 50 }
  properties: { model: { format: 'OpenAI', name: notetakerModel, version: notetakerModelVersion } }
}

output accountName string = account.name
output projectPrincipalId string = project.identity.principalId
