# Glossary

| Term | Meaning |
|---|---|
| **ACS** | Azure Communication Services. Provides call automation, phone numbers and media streaming |
| **Call Automation** | The ACS API the app uses to answer calls, stream audio and add participants |
| **TPE** | Teams Phone Extensibility. Lets a Teams phone number (resource account) route calls into an ACS app |
| **Resource account** | A Teams account without a person behind it that owns the business phone number |
| **Enterprise Voice** | The Teams license and setting that lets a user make and receive PSTN calls |
| **PSTN** | The public telephone network |
| **Event Grid** | Azure service that delivers the `IncomingCall` event to the app |
| **Unmixed audio** | ACS sends each participant's audio on its own channel so speech-to-text can label speakers |
| **STT** | Speech-to-text, here Azure Speech |
| **Foundry** | Microsoft Foundry (formerly Azure AI Foundry), where agents, models and connections live |
| **Voice agent** | A Foundry agent that speaks and listens in real time (Voice Live) |
| **Prompt agent** | A Foundry agent defined by instructions and tools that replies in text |
| **Toolbox** | A Foundry bundle of tools attached to a voice agent |
| **MCP** | Model Context Protocol, a standard way for agents to call tools. The Dataverse MCP server exposes Dataverse |
| **Project connection** | A named Foundry credential/endpoint; `dataverse-mfg-mi` uses the project's managed identity |
| **Managed identity** | An Azure-issued identity with no secrets to store |
| **Dataverse** | The Power Platform data store that holds the manufacturing tables |
| **Application user** | A Dataverse user record that represents an app or managed identity |
| **Draft** | `bdm_servicerequestdraft` row built by the agents during a call |
| **Submit** | The rep action that creates the real `bdm_serviceticket` |
| **Receptionist** | The AI voice agent that answers first |
| **Note-taker** | The silent AI agent that fills the draft |
| **Rep** | The human customer service representative |
| **SSE** | Server-sent events, a one-way browser stream used by `/events/{callId}` |
| **`bdm_`** | Publisher prefix on all custom Dataverse tables and columns |
| **JDDO** | The customer experience this project set out to replace (the Service Recording & Scheduling Agent demo) |
