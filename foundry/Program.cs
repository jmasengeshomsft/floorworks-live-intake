// FloorWorks Foundry IQ CLI: preflight | index | kb | connect | all | ask "<question>"
// Pattern copied from the CAT apq-foundry-iq demo, using raw REST (no preview SDK dependency).
using System.Net.Http.Headers;
using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;
using Azure.Core;
using Azure.Identity;
using FloorWorks.FoundryIq;

string Env(string k, string d) => Environment.GetEnvironmentVariable(k) is { Length: > 0 } v ? v : d;

var sub = Env("AZURE_SUBSCRIPTION_ID", "");
var rg = Env("AZURE_RESOURCE_GROUP", "");
var searchName = Env("SEARCH_SERVICE", "");
var searchUrl = $"https://{searchName}.search.windows.net";
var api = Env("SEARCH_API_VERSION", "2026-05-01-preview");
var account = Env("FOUNDRY_ACCOUNT", "");
var project = Env("FOUNDRY_PROJECT", "");
var aoaiUrl = Env("AOAI_ENDPOINT", $"https://{account}.openai.azure.com");
var embDeploy = Env("EMBEDDING_DEPLOYMENT", "text-embedding-3-small");
var llmDeploy = Env("KB_LLM_DEPLOYMENT", "gpt-4.1"); // KB model; required because the Foundry MCP tool requests a reasoning effort above 'minimal'
const string Index = "floorworks-kb-index", Source = "floorworks-kb-source", Kb = "floorworks-kb", Conn = "floorworks-kb";

var cred = new DefaultAzureCredential();
var http = new HttpClient { Timeout = TimeSpan.FromMinutes(3) };

async Task<string> Tok(string scope) => (await cred.GetTokenAsync(new TokenRequestContext(new[] { scope }))).Token;

async Task<(int code, string body)> Send(HttpMethod m, string url, string scope, JsonNode? body = null)
{
    var req = new HttpRequestMessage(m, url);
    req.Headers.Authorization = new AuthenticationHeaderValue("Bearer", await Tok(scope));
    if (body is not null) req.Content = new StringContent(body.ToJsonString(), Encoding.UTF8, "application/json");
    var r = await http.SendAsync(req);
    return ((int)r.StatusCode, await r.Content.ReadAsStringAsync());
}
Task<(int, string)> Search(HttpMethod m, string path, JsonNode? b = null) =>
    Send(m, $"{searchUrl}/{path}{(path.Contains('?') ? "&" : "?")}api-version={api}", "https://search.azure.com/.default", b).ContinueWith(t => (t.Result.code, t.Result.body));

void Check((int code, string body) r, string what)
{
    if (r.code is >= 200 and < 300) { Console.WriteLine($"  ok   {what}"); return; }
    Console.WriteLine($"  FAIL {what}: HTTP {r.code} {r.body}");
    throw new Exception(what + " failed");
}

async Task<float[][]> Embed(IReadOnlyList<string> inputs)
{
    var url = $"{aoaiUrl}/openai/deployments/{embDeploy}/embeddings?api-version=2024-10-21";
    var r = await Send(HttpMethod.Post, url, "https://cognitiveservices.azure.com/.default", new JsonObject { ["input"] = new JsonArray(inputs.Select(s => (JsonNode)s).ToArray()) });
    if (r.code != 200) throw new Exception($"embedding HTTP {r.code}: {r.body}");
    return JsonNode.Parse(r.body)!["data"]!.AsArray().Select(d => d!["embedding"]!.AsArray().Select(x => (float)x!).ToArray()).ToArray();
}

async Task Preflight()
{
    Console.WriteLine("Preflight");
    Console.WriteLine($"  search={searchUrl} api={api} embedding={embDeploy} project={account}/{project}");
    var r = await Search(HttpMethod.Get, "indexes?$select=name");
    Console.WriteLine(r.Item1 == 200 ? "  ok   search data-plane access (Search Service Contributor / Index Data Contributor)" : $"  FAIL search access: HTTP {r.Item1} {r.Item2}");
    try { var e = await Embed(new[] { "preflight" }); Console.WriteLine($"  ok   embedding deployment returns {e[0].Length} dims"); }
    catch (Exception ex) { Console.WriteLine("  FAIL embedding: " + ex.Message); }
    var arm = await Send(HttpMethod.Get, $"https://management.azure.com/subscriptions/{sub}/resourceGroups/{rg}/providers/Microsoft.CognitiveServices/accounts/{account}/projects/{project}?api-version=2025-04-01-preview", "https://management.azure.com/.default");
    Console.WriteLine(arm.code == 200 ? "  ok   Foundry project reachable via ARM" : $"  FAIL Foundry project: HTTP {arm.code}");
    if (arm.code == 200)
    {
        var pid = JsonNode.Parse(arm.body)?["identity"]?["principalId"]?.ToString();
        Console.WriteLine($"  info project managed identity principalId={pid}; it needs 'Search Index Data Reader' on {searchName}");
    }
}

async Task BuildIndex()
{
    Console.WriteLine("Index");
    var chunks = Corpus.Build();
    Console.WriteLine($"  corpus: {chunks.Count} chunks");
    JsonObject Field(string n, string t, bool key = false, bool srch = false, bool filt = false) => new()
    { ["name"] = n, ["type"] = t, ["key"] = key, ["searchable"] = srch, ["filterable"] = filt, ["retrievable"] = true };
    var def = new JsonObject
    {
        ["name"] = Index,
        ["fields"] = new JsonArray(
            Field("id", "Edm.String", key: true), Field("pillar", "Edm.String", filt: true), Field("title", "Edm.String", srch: true),
            Field("content", "Edm.String", srch: true), Field("source", "Edm.String", srch: true), Field("asset", "Edm.String", srch: true, filt: true),
            Field("part", "Edm.String", srch: true, filt: true), Field("bulletin", "Edm.String", srch: true, filt: true),
            new JsonObject { ["name"] = "contentVector", ["type"] = "Collection(Edm.Single)", ["searchable"] = true, ["retrievable"] = false, ["dimensions"] = 1536, ["vectorSearchProfile"] = "vprofile" }),
        ["vectorSearch"] = new JsonObject
        {
            ["algorithms"] = new JsonArray(new JsonObject { ["name"] = "hnsw", ["kind"] = "hnsw" }),
            ["profiles"] = new JsonArray(new JsonObject { ["name"] = "vprofile", ["algorithm"] = "hnsw", ["vectorizer"] = "aoai-vec" }),
            ["vectorizers"] = new JsonArray(new JsonObject
            {
                ["name"] = "aoai-vec", ["kind"] = "azureOpenAI",
                ["azureOpenAIParameters"] = new JsonObject { ["resourceUri"] = aoaiUrl, ["deploymentId"] = embDeploy, ["modelName"] = embDeploy }
            })
        },
        ["semantic"] = new JsonObject
        {
            ["defaultConfiguration"] = "default",
            ["configurations"] = new JsonArray(new JsonObject
            {
                ["name"] = "default",
                ["prioritizedFields"] = new JsonObject
                {
                    ["titleField"] = new JsonObject { ["fieldName"] = "title" },
                    ["prioritizedContentFields"] = new JsonArray(new JsonObject { ["fieldName"] = "content" }),
                    ["prioritizedKeywordsFields"] = new JsonArray(new JsonObject { ["fieldName"] = "source" })
                }
            })
        }
    };
    Check(await Search(HttpMethod.Put, $"indexes/{Index}", def), $"index {Index}");

    var docs = new JsonArray();
    for (int i = 0; i < chunks.Count; i += 16)
    {
        var batch = chunks.Skip(i).Take(16).ToList();
        var vecs = await Embed(batch.Select(b => $"{b.Title}\n{b.Content}").ToList());
        for (int j = 0; j < batch.Count; j++)
        {
            var b = batch[j];
            docs.Add(new JsonObject
            {
                ["@search.action"] = "mergeOrUpload", ["id"] = b.Id, ["pillar"] = b.Pillar, ["title"] = b.Title, ["content"] = b.Content,
                ["source"] = b.Source, ["asset"] = b.Asset, ["part"] = b.Part, ["bulletin"] = b.Bulletin,
                ["contentVector"] = new JsonArray(vecs[j].Select(f => (JsonNode)f).ToArray())
            });
        }
        Console.WriteLine($"  embedded {Math.Min(i + 16, chunks.Count)}/{chunks.Count}");
    }
    for (int i = 0; i < docs.Count; i += 100)
    {
        var part = new JsonArray(docs.Skip(i).Take(100).Select(d => d!.DeepClone()).ToArray());
        Check(await Search(HttpMethod.Post, $"indexes/{Index}/docs/index", new JsonObject { ["value"] = part }), $"upload docs {i + 1}-{i + part.Count}");
    }
}

async Task BuildKb()
{
    Console.WriteLine("Knowledge source + base");
    var src = new JsonObject
    {
        ["name"] = Source, ["kind"] = "searchIndex", ["description"] = "FloorWorks manuals, part sheets, service bulletins, playbooks, warranty/recall",
        ["searchIndexParameters"] = new JsonObject
        {
            ["searchIndexName"] = Index, ["semanticConfigurationName"] = "default",
            ["sourceDataFields"] = new JsonArray(new[] { "id", "title", "source", "pillar", "asset", "part", "bulletin" }.Select(n => (JsonNode)new JsonObject { ["name"] = n }).ToArray()),
            ["searchFields"] = new JsonArray(new[] { "title", "content", "contentVector" }.Select(n => (JsonNode)new JsonObject { ["name"] = n }).ToArray())
        }
    };
    Check(await Search(HttpMethod.Put, $"knowledgesources/{Source}", src), $"knowledge source {Source}");
    var kb = new JsonObject
    {
        ["name"] = Kb, ["description"] = "FloorWorks service knowledge base for ticket enrichment",
        ["knowledgeSources"] = new JsonArray(new JsonObject { ["name"] = Source }),
        ["outputMode"] = "extractiveData",
        ["retrievalReasoningEffort"] = new JsonObject { ["kind"] = "minimal" }
    };
    if (llmDeploy != "")
    {
        kb["retrievalInstructions"] = "Answer from FloorWorks equipment manuals, part sheets, service bulletins (SB-2026-NNN), diagnostic playbooks and warranty/recall notices. Always include the bulletin number, part number and lot when relevant.";
        kb["models"] = new JsonArray(new JsonObject { ["kind"] = "azureOpenAI", ["azureOpenAIParameters"] = new JsonObject { ["resourceUri"] = aoaiUrl, ["deploymentId"] = llmDeploy, ["modelName"] = llmDeploy } });
        kb["retrievalReasoningEffort"] = new JsonObject { ["kind"] = "low" };
    }
    Check(await Search(HttpMethod.Put, $"knowledgebases/{Kb}", kb), $"knowledge base {Kb}");
}

async Task Connect()
{
    Console.WriteLine("Project connection");
    var url = $"https://management.azure.com/subscriptions/{sub}/resourceGroups/{rg}/providers/Microsoft.CognitiveServices/accounts/{account}/projects/{project}/connections/{Conn}?api-version=2025-04-01-preview";
    var body = new JsonObject
    {
        ["properties"] = new JsonObject
        {
            ["authType"] = "ProjectManagedIdentity", ["category"] = "RemoteTool", ["isSharedToAll"] = true,
            ["target"] = $"{searchUrl}/knowledgebases/{Kb}/mcp?api-version={api}",
            ["audience"] = "https://search.azure.com/",
            ["metadata"] = new JsonObject { ["ApiType"] = "Azure" }
        }
    };
    Check(await Send(HttpMethod.Put, url, "https://management.azure.com/.default", body), $"connection {Conn}");
    Console.WriteLine("  Remember: grant the project managed identity 'Search Index Data Reader' on " + searchName);
}

async Task Ask(string q)
{
    var body = new JsonObject
    {
        ["intents"] = new JsonArray(new JsonObject { ["type"] = "semantic", ["search"] = q }),
        ["maxOutputSize"] = 6000
    };
    if (llmDeploy == "") body["retrievalReasoningEffort"] = new JsonObject { ["kind"] = "minimal" };
    var r = await Search(HttpMethod.Post, $"knowledgebases/{Kb}/retrieve", body);
    if (r.Item1 == 400)
    {
        body = new JsonObject { ["messages"] = new JsonArray(new JsonObject { ["role"] = "user", ["content"] = new JsonArray(new JsonObject { ["type"] = "text", ["text"] = q }) }) };
        r = await Search(HttpMethod.Post, $"knowledgebases/{Kb}/retrieve", body);
    }
    if (r.Item1 != 200) { Console.WriteLine($"HTTP {r.Item1}\n{r.Item2}"); return; }
    var j = JsonNode.Parse(r.Item2)!;
    foreach (var resp in j["response"]?.AsArray() ?? new JsonArray())
        foreach (var c in resp?["content"]?.AsArray() ?? new JsonArray()) Console.WriteLine(c?["text"]);
    Console.WriteLine("\nReferences:");
    foreach (var rf in j["references"]?.AsArray() ?? new JsonArray())
        Console.WriteLine($"  - {rf?["docKey"]} {rf?["sourceData"]?["title"]} ({rf?["sourceData"]?["source"]})");
}

var cmd = args.Length > 0 ? args[0].ToLowerInvariant() : "all";
switch (cmd)
{
    case "preflight": await Preflight(); break;
    case "index": await BuildIndex(); break;
    case "kb": await BuildKb(); break;
    case "connect": await Connect(); break;
    case "all": await Preflight(); await BuildIndex(); await BuildKb(); await Connect(); break;
    case "ask": await Ask(string.Join(' ', args.Skip(1))); break;
    default: Console.WriteLine("usage: preflight | index | kb | connect | all | ask <question>"); break;
}
