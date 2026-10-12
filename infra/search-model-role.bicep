targetScope = 'resourceGroup'

param foundryAccountName string
param principalId string

resource foundry 'Microsoft.CognitiveServices/accounts@2024-10-01' existing = { name: foundryAccountName }

resource openAiUser 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(foundry.id, principalId, 'openai-user')
  scope: foundry
  properties: {
    principalId: principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '5e0bd9bd-7b93-4f28-af87-19fc36ad61bd') // Cognitive Services OpenAI User
  }
}
