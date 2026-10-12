targetScope = 'resourceGroup'

param foundryAccountName string
param principalId string

resource foundry 'Microsoft.CognitiveServices/accounts@2024-10-01' existing = { name: foundryAccountName }

var roles = [
  '53ca6127-db72-4b80-b1b0-d745d6d5456d' // Azure AI User
  'a97b65f3-24c7-4388-baec-2e87135dc908' // Cognitive Services User
]

resource assignments 'Microsoft.Authorization/roleAssignments@2022-04-01' = [for r in roles: {
  name: guid(foundry.id, principalId, r)
  scope: foundry
  properties: {
    principalId: principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', r)
  }
}]
