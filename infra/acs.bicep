targetScope = 'resourceGroup'

param name string
param tags object = {}

resource acs 'Microsoft.Communication/communicationServices@2023-04-01' = {
  name: name
  location: 'global'
  tags: tags
  properties: { dataLocation: 'United States' }
}
