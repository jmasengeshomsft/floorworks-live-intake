targetScope = 'resourceGroup'

@minLength(1)
@maxLength(20)
@description('Short name used to derive resource names (azd environment name).')
param environmentName string

param location string = resourceGroup().location

@description('Create a new ACS resource in this RG (true) or reference an existing one by name (false).')
param createAcs bool = false
@description('Create a new Foundry account + project + model deployments in this RG (true) or reference existing ones (false).')
param createFoundry bool = false

@description('Existing ACS name (createAcs=false). Ignored when createAcs=true.')
param existingAcsName string = ''
@description('Existing Foundry account (createFoundry=false). Ignored when createFoundry=true.')
param existingFoundryAccountName string = ''
@description('Resource group of the existing Foundry account.')
param existingFoundryResourceGroup string = resourceGroup().name
@description('Existing Foundry project (createFoundry=false).')
param existingFoundryProjectName string = 'firstProject'

@description('Dataverse environment URL, e.g. https://org.crm.dynamics.com')
param dataverseUrl string

@description('Comma-separated Entra object ids of the reps who answer in Teams.')
param repTeamsObjectIds string

@secure()
@description('Shared key for the Rep Console (?k=...). Leave empty to generate none and disable the key check.')
param repKey string = ''

param speechRegion string = 'eastus'
param voiceAgentName string = 'floorworks-voice-agent-app'
param notetakerAgentName string = 'floorworks-notetaker'

@allowed(['on', 'off'])
param enrichment string = 'off'
@allowed(['on', 'off', 'auto'])
param afterHours string = 'off'
@allowed(['on', 'off'])
param ringback string = 'on'

var token = toLower(uniqueString(subscription().id, resourceGroup().id, environmentName))
var tags = { 'azd-env-name': environmentName }
var appName = 'voice-intake'

var acsName = createAcs ? 'acs-${token}' : existingAcsName
var foundryAccountName = createFoundry ? 'aif-${token}' : existingFoundryAccountName
var foundryProjectName = createFoundry ? 'floorworks' : existingFoundryProjectName
var foundryResourceGroup = createFoundry ? resourceGroup().name : existingFoundryResourceGroup

module newAcs 'acs.bicep' = if (createAcs) {
  name: 'acs'
  params: { name: acsName, tags: tags }
}

module newFoundry 'foundry.bicep' = if (createFoundry) {
  name: 'foundry'
  params: {
    location: location
    accountName: foundryAccountName
    projectName: foundryProjectName
    tags: tags
  }
}

resource acs 'Microsoft.Communication/communicationServices@2023-04-01' existing = {
  name: acsName
  dependsOn: [newAcs]
}
resource foundry 'Microsoft.CognitiveServices/accounts@2024-10-01' existing = {
  name: foundryAccountName
  scope: resourceGroup(foundryResourceGroup)
  dependsOn: [newFoundry]
}

resource logs 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: 'log-${token}'
  location: location
  tags: tags
  properties: { sku: { name: 'PerGB2018' }, retentionInDays: 30 }
}

resource acr 'Microsoft.ContainerRegistry/registries@2023-07-01' = {
  name: 'acr${token}'
  location: location
  tags: tags
  sku: { name: 'Basic' }
  properties: { adminUserEnabled: false }
}

resource env 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: 'cae-${token}'
  location: location
  tags: tags
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: logs.properties.customerId
        sharedKey: logs.listKeys().primarySharedKey
      }
    }
  }
}

@description('Create an Azure AI Search service for the Foundry IQ knowledge base (only needed when ENRICHMENT=on).')
param deploySearch bool = true
@description('Entra object id of the person running foundry/ CLI (index + KB build). Optional.')
param runnerPrincipalId string = ''

resource search 'Microsoft.Search/searchServices@2024-06-01-preview' = if (deploySearch) {
  name: 'srch-${token}'
  location: location
  tags: tags
  sku: { name: 'basic' }
  identity: { type: 'SystemAssigned' }
  properties: {
    replicaCount: 1
    partitionCount: 1
    semanticSearch: 'standard'
    disableLocalAuth: false
    authOptions: { aadOrApiKey: { aadAuthFailureMode: 'http401WithBearerChallenge' } }
  }
}

resource foundryProject 'Microsoft.CognitiveServices/accounts/projects@2025-04-01-preview' existing = {
  name: '${foundryAccountName}/${foundryProjectName}'
  scope: resourceGroup(foundryResourceGroup)
  dependsOn: [newFoundry]
}

// The KB calls the chat model with the search service's own identity.
module searchModelAccess 'search-model-role.bicep' = if (deploySearch) {
  name: 'search-model-access'
  scope: resourceGroup(foundryResourceGroup)
  params: {
    foundryAccountName: foundryAccountName
    principalId: deploySearch ? search.identity.principalId : ''
  }
  dependsOn: [newFoundry]
}

// Project managed identity reads the index when the KB MCP tool is called.
resource projectSearchReader 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (deploySearch) {
  name: guid(search.id, foundryProject.id, 'idx-reader')
  scope: search
  properties: {
    principalId: foundryProject.identity.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '1407120a-92aa-4202-b7e9-c0e197c71c8f') // Search Index Data Reader
  }
}

resource runnerSearchRoles 'Microsoft.Authorization/roleAssignments@2022-04-01' = [for r in [
  '7ca78c08-252a-4471-8644-bb5ff32d4ba0' // Search Service Contributor
  '8ebe5a00-799e-43f5-93ac-243d3dce84a7' // Search Index Data Contributor
]: if (deploySearch && !empty(runnerPrincipalId)) {
  name: guid(search.id, runnerPrincipalId, r)
  scope: search
  properties: {
    principalId: runnerPrincipalId
    principalType: 'User'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', r)
  }
}]

var publicBase = 'https://${appName}.${env.properties.defaultDomain}'

// Pull identity exists (with AcrPull) before the app, so the first revision can authenticate to ACR.
resource pullIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: 'id-pull-${token}'
  location: location
  tags: tags
}

resource acrPull 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(acr.id, pullIdentity.id, 'acrpull')
  scope: acr
  properties: {
    principalId: pullIdentity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '7f951dda-4ed3-4680-a7ca-43fe172d538d')
  }
}

resource app 'Microsoft.App/containerApps@2024-03-01' = {
  name: appName
  location: location
  tags: union(tags, { 'azd-service-name': 'voice-intake' })
  identity: {
    type: 'SystemAssigned, UserAssigned'
    userAssignedIdentities: { '${pullIdentity.id}': {} }
  }
  dependsOn: [acrPull]
  properties: {
    managedEnvironmentId: env.id
    configuration: {
      ingress: { external: true, targetPort: 8080, transport: 'auto' }
      registries: [{ server: acr.properties.loginServer, identity: pullIdentity.id }]
      secrets: [
        { name: 'acs-cs', value: acs.listKeys().primaryConnectionString }
        { name: 'speech-key', value: foundry.listKeys().key1 }
      ]
    }
    template: {
      containers: [
        {
          name: appName
          // Placeholder until `azd deploy` pushes the real image.
          image: 'mcr.microsoft.com/dotnet/samples:aspnetapp'
          resources: { cpu: json('0.5'), memory: '1Gi' }
          env: [
            { name: 'ACS_CONNECTION_STRING', secretRef: 'acs-cs' }
            { name: 'SPEECH_KEY', secretRef: 'speech-key' }
            { name: 'SPEECH_REGION', value: speechRegion }
            { name: 'FOUNDRY_PROJECT_ENDPOINT', value: 'https://${foundryAccountName}.services.ai.azure.com/api/projects/${foundryProjectName}' }
            { name: 'DATAVERSE_URL', value: dataverseUrl }
            { name: 'REP_TEAMS_OBJECT_IDS', value: repTeamsObjectIds }
            { name: 'REP_KEY', value: repKey }
            { name: 'VOICE_AGENT_NAME', value: voiceAgentName }
            { name: 'NOTETAKER_AGENT', value: notetakerAgentName }
            { name: 'PUBLIC_BASE_URL', value: publicBase }
            { name: 'TRANSCRIPTION_MODE', value: 'voice-agent' }
            { name: 'VOICE_SESSION_TUNING', value: 'on' }
            { name: 'ENRICHMENT', value: enrichment }
            { name: 'AFTER_HOURS', value: afterHours }
            { name: 'RINGBACK', value: ringback }
          ]
        }
      ]
      // One replica: call state lives in memory and the media WebSocket must stay on the same instance.
      scale: { minReplicas: 1, maxReplicas: 1 }
    }
  }
}

// The app calls the voice and note-taker agents as its managed identity.
module foundryRoles 'foundry-roles.bicep' = {
  name: 'foundry-roles'
  scope: resourceGroup(foundryResourceGroup)
  params: {
    foundryAccountName: foundryAccountName
    principalId: app.identity.principalId
  }
}

output AZURE_CONTAINER_REGISTRY_ENDPOINT string = acr.properties.loginServer
output AZURE_CONTAINER_APPS_ENVIRONMENT_ID string = env.id
output SERVICE_VOICE_INTAKE_NAME string = app.name
output PUBLIC_BASE_URL string = publicBase
output APP_PRINCIPAL_ID string = app.identity.principalId
output SEARCH_SERVICE string = deploySearch ? search.name : ''
output ACS_NAME string = acsName
output FOUNDRY_ACCOUNT_NAME string = foundryAccountName
output FOUNDRY_PROJECT_NAME string = foundryProjectName
output FOUNDRY_RESOURCE_GROUP string = foundryResourceGroup
output PROJECT_PRINCIPAL_ID string = foundryProject.identity.principalId
