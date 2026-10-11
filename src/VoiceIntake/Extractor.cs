using System.Net.Http.Headers;
using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;
using System.Threading.Channels;
using Azure.Core;
using Azure.Identity;

// Per-call transcript and the last extracted fields (kept in memory only; nothing is persisted).
public sealed class CallState
{
    private readonly object _gate = new();
    private readonly List<(string speaker, string text)> _lines = new();
    private readonly Dictionary<string, string> _roles = new();
    public string CallId = "";
    public string CallerPhone = "";
    public string ConnId = "";
    public int ActiveWs;
    public int Connections;
    public volatile bool Muted;
    public volatile bool RepJoined;
    public volatile bool TransferStarted;
    public TaskCompletionSource ResponseDone = new();
    public JsonNode Fields { get; set; } = new JsonObject();
    public int Version { get; private set; }
    public int Processed { get; set; }
    public bool Running { get; set; }

    // Unmixed streams are keyed by participant id: Teams reps are "8:orgid:...", everyone else is the customer.
    public string RoleFor(string participant)
    {
        lock (_gate)
        {
            if (_roles.TryGetValue(participant, out var r)) return r;
            return _roles[participant] = participant.StartsWith("8:orgid:") ? "rep" : "customer";
        }
    }

    public void Add(string speaker, string text)
    {
        lock (_gate) { _lines.Add((speaker, text)); Version++; }
    }

    public (int version, JsonArray transcript) Snapshot()
    {
        lock (_gate)
        {
            var arr = new JsonArray();
            foreach (var (s, t) in _lines) arr.Add(new JsonObject { ["speaker"] = s, ["text"] = t });
            return (Version, arr);
        }
    }
}

// Calls the Foundry prompt agent (Responses API) with the running transcript.
// One request in flight per call; utterances that arrive meanwhile are batched into the next run.
public sealed class Extractor
{
    private static readonly HttpClient Http = new() { Timeout = TimeSpan.FromSeconds(170) };
    private readonly string _endpoint, _agent;
    private readonly TokenCredential _cred =
        Environment.GetEnvironmentVariable("LOCAL_DEV") == "1" ? new AzureCliCredential() : new DefaultAzureCredential();

    public Func<CallState, JsonNode?, Task>? OnResult { get; set; }

    public Extractor(string endpoint, string agent) { _endpoint = endpoint.TrimEnd('/'); _agent = agent; }

    public async Task RunAsync(CallState state, ChannelWriter<string> hub, ILogger log)
    {
        lock (state) { if (state.Running) return; state.Running = true; }
        try
        {
            while (true)
            {
                var (version, transcript) = state.Snapshot();
                if (version == state.Processed) break;

                var input = new JsonObject
                {
                    ["mode"] = "extract", ["call_id"] = state.CallId, ["caller_phone"] = state.CallerPhone,
                    ["transcript"] = transcript, ["current"] = state.Fields.DeepClone()
                };
                try
                {
                    var result = await CallAgent(input.ToJsonString());
                    if (result?["fields"] is JsonNode f) state.Fields = f.DeepClone();
                    if (OnResult is not null) await OnResult(state, result);
                    var evt = result?.DeepClone() as JsonObject ?? new JsonObject();
                    evt["type"] = "fields";
                    hub.TryWrite(evt.ToJsonString());
                    log.LogInformation("[{Id}] NOTES v{V}: {F}", state.CallId, version, state.Fields.ToJsonString());
                }
                catch (Exception ex)
                {
                    log.LogWarning("[{Id}] notes error: {M}", state.CallId, ex.Message);
                    hub.TryWrite(JsonSerializer.Serialize(new { type = "error", message = ex.Message }));
                }
                state.Processed = version;
            }
        }
        finally { lock (state) { state.Running = false; } }
    }

    // At call end: one last pass over the full transcript. The result becomes the final draft; no ticket is created here.
    public async Task FinalizeAsync(CallState state, ChannelWriter<string> hub, ILogger log)
    {
        for (int i = 0; i < 60 && state.Running; i++) await Task.Delay(500);
        var (_, transcript) = state.Snapshot();
        if (transcript.Count == 0) return;
        var input = new JsonObject
        {
            ["mode"] = "finalize", ["call_id"] = state.CallId, ["caller_phone"] = state.CallerPhone,
            ["transcript"] = transcript, ["current"] = state.Fields.DeepClone()
        };
        try
        {
            var result = await CallAgent(input.ToJsonString());
            log.LogInformation("[{Id}] FINALIZE: {R}", state.CallId, result?.ToJsonString());
            if (result?["fields"] is JsonNode ff) state.Fields = ff.DeepClone();
            if (OnResult is not null) await OnResult(state, result);
            var evt = result?.DeepClone() as JsonObject ?? new JsonObject();
            evt["type"] = "final_notes";
            hub.TryWrite(evt.ToJsonString());
        }
        catch (Exception ex) { log.LogWarning("[{Id}] finalize error: {M}", state.CallId, ex.Message); }
    }

    private async Task<JsonObject?> CallAgent(string inputJson)
    {
        var token = await _cred.GetTokenAsync(new TokenRequestContext(new[] { "https://ai.azure.com/.default" }), default);
        var body = new JsonObject
        {
            ["agent_reference"] = new JsonObject { ["type"] = "agent_reference", ["name"] = _agent },
            ["input"] = inputJson
        };
        using var req = new HttpRequestMessage(HttpMethod.Post, $"{_endpoint}/openai/v1/responses")
        {
            Content = new StringContent(body.ToJsonString(), Encoding.UTF8, "application/json")
        };
        req.Headers.Authorization = new AuthenticationHeaderValue("Bearer", token.Token);
        using var resp = await Http.SendAsync(req);
        var text = await resp.Content.ReadAsStringAsync();
        if (!resp.IsSuccessStatusCode) throw new Exception($"agent {(int)resp.StatusCode}: {text[..Math.Min(300, text.Length)]}");

        var msg = JsonNode.Parse(text)?["output"]?.AsArray()
            .FirstOrDefault(o => o?["type"]?.GetValue<string>() == "message");
        var outText = msg?["content"]?.AsArray().FirstOrDefault()?["text"]?.GetValue<string>();
        return outText is null ? null : JsonNode.Parse(outText) as JsonObject;
    }
}
