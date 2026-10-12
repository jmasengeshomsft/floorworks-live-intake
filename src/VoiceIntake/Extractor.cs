using System.Net.Http.Headers;
using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;
using System.Threading.Channels;
using Azure.Core;
using Azure.Identity;

// Per-call transcript and the last extracted fields (kept in memory only; nothing is persisted).
// After-hours mode. Off unless AFTER_HOURS is set, so default behavior is unchanged.
//   AFTER_HOURS=on|off|auto (auto uses SERVICE_DAYS "Mon-Fri", SERVICE_HOURS "07:00-18:00", SERVICE_TZ "America/Chicago")
public static class AfterHours
{
    public static bool IsActive()
    {
        var mode = (Environment.GetEnvironmentVariable("AFTER_HOURS") ?? "off").Trim().ToLowerInvariant();
        if (mode == "on") return true;
        if (mode != "auto") return false;
        try
        {
            var tz = TimeZoneInfo.FindSystemTimeZoneById(Environment.GetEnvironmentVariable("SERVICE_TZ") ?? "America/Chicago");
            var now = TimeZoneInfo.ConvertTimeFromUtc(DateTime.UtcNow, tz);
            var days = (Environment.GetEnvironmentVariable("SERVICE_DAYS") ?? "Mon-Fri").Split('-');
            int d0 = (int)Enum.Parse<DayOfWeek>(Full(days[0])), d1 = (int)Enum.Parse<DayOfWeek>(Full(days[^1]));
            int dow = (int)now.DayOfWeek;
            var dayOpen = d0 <= d1 ? dow >= d0 && dow <= d1 : dow >= d0 || dow <= d1;
            var hrs = (Environment.GetEnvironmentVariable("SERVICE_HOURS") ?? "07:00-18:00").Split('-');
            var open = TimeSpan.Parse(hrs[0]); var close = TimeSpan.Parse(hrs[1]);
            return !(dayOpen && now.TimeOfDay >= open && now.TimeOfDay < close);
        }
        catch { return false; }
    }
    static string Full(string d) => d.Trim().ToLowerInvariant() switch
    {
        "mon" => "Monday", "tue" => "Tuesday", "wed" => "Wednesday", "thu" => "Thursday",
        "fri" => "Friday", "sat" => "Saturday", _ => d.Trim().StartsWith("sun", StringComparison.OrdinalIgnoreCase) ? "Sunday" : d.Trim()
    };
    public static string Note => Environment.GetEnvironmentVariable("AFTER_HOURS_NOTE")
        ?? "Our service representatives are not available right now, so you cannot transfer the caller. Do not offer or promise a transfer or to connect them to anyone. Collect the caller's name, the equipment and what is wrong, and tell them a service representative will follow up on the next business day. If there is a safety emergency, tell them to hang up and call emergency services.";
}

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
    public bool AfterHours;
    public volatile bool TransferStarted;
    public volatile bool Ringing;
    public TaskCompletionSource ResponseDone = new();
    public JsonNode Fields { get; set; } = new JsonObject();
    public int Version { get; private set; }
    public int Processed { get; set; }
    public bool Running { get; set; }
    public volatile bool Enriching;

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
    private static readonly HttpClient Http = new() { Timeout = TimeSpan.FromSeconds(100) };
    private readonly string _endpoint, _agent;
    private readonly TokenCredential _cred =
        Environment.GetEnvironmentVariable("LOCAL_DEV") == "1" ? new AzureCliCredential() : new DefaultAzureCredential();

    public Func<CallState, JsonNode?, Task>? OnResult { get; set; }

    // Foundry IQ enrichment (note-taker only). ENRICHMENT=on|off, default off: when off the "enrichment" block is ignored.
    public static bool EnrichmentOn => (Environment.GetEnvironmentVariable("ENRICHMENT") ?? "off").Trim().ToLowerInvariant() == "on";

    public Extractor(string endpoint, string agent) { _endpoint = endpoint.TrimEnd('/'); _agent = agent; }

    // Enrichment is stored separately from fields; fields are never derived from it. Dropped entirely when off.
    private static void StripEnrichment(JsonObject? result)
    {
        if (result is not null && !EnrichmentOn) result.Remove("enrichment");
    }

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
                // Enrich live only occasionally; every pass would make each notes call slow. Finalize always enriches.
                bool enrich = EnrichmentOn && (DateTime.UtcNow - _lastEnrich.GetValueOrDefault(state.CallId, DateTime.MinValue)).TotalSeconds > 60;
                if (enrich) { input["enrichment"] = "on"; _lastEnrich[state.CallId] = DateTime.UtcNow; }
                try
                {
                    state.Enriching = enrich;
                    var sw = System.Diagnostics.Stopwatch.StartNew();
                    var result = await CallAgent(input.ToJsonString(), enrich ? 60 : 30);
                    log.LogInformation("[{Id}] notes call {Ms} ms (enrich={E})", state.CallId, sw.ElapsedMilliseconds, enrich);
                    StripEnrichment(result);
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
                state.Enriching = false;
                state.Processed = version;
            }
        }
        finally { state.Enriching = false; lock (state) { state.Running = false; } }
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
        if (EnrichmentOn) input["enrichment"] = "on";
        try
        {
            var result = await CallAgent(input.ToJsonString());
            StripEnrichment(result);
            log.LogInformation("[{Id}] FINALIZE: {R}", state.CallId, result?.ToJsonString());
            if (result?["fields"] is JsonNode ff) state.Fields = ff.DeepClone();
            if (OnResult is not null) await OnResult(state, result);
            var evt = result?.DeepClone() as JsonObject ?? new JsonObject();
            evt["type"] = "final_notes";
            hub.TryWrite(evt.ToJsonString());
        }
        catch (Exception ex) { log.LogWarning("[{Id}] finalize error: {M}", state.CallId, ex.Message); }
    }

    private readonly System.Collections.Concurrent.ConcurrentDictionary<string, DateTime> _lastEnrich = new();

    private async Task<JsonObject?> CallAgent(string inputJson, int timeoutSec = 90)
    {
        using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(timeoutSec));
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
        using var resp = await Http.SendAsync(req, cts.Token);
        var text = await resp.Content.ReadAsStringAsync(cts.Token);
        if (!resp.IsSuccessStatusCode) throw new Exception($"agent {(int)resp.StatusCode}: {text[..Math.Min(300, text.Length)]}");

        var msg = JsonNode.Parse(text)?["output"]?.AsArray()
            .FirstOrDefault(o => o?["type"]?.GetValue<string>() == "message");
        var outText = msg?["content"]?.AsArray().FirstOrDefault()?["text"]?.GetValue<string>();
        return outText is null ? null : JsonNode.Parse(outText) as JsonObject;
    }
}
