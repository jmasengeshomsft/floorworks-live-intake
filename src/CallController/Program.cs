using System.Text.Json;
using Azure.Communication;
using Azure.Communication.CallAutomation;
using Azure.Messaging;
using Azure.Messaging.EventGrid;
using Azure.Messaging.EventGrid.SystemEvents;

var builder = WebApplication.CreateBuilder(args);
var app = builder.Build();

string Cfg(string k) => Environment.GetEnvironmentVariable(k) ?? throw new InvalidOperationException($"Missing env {k}");
var client = new CallAutomationClient(Cfg("ACS_CONNECTION_STRING"));
var publicBase = Cfg("PUBLIC_BASE_URL").TrimEnd('/');          // https://<call-controller-host>
var streamBase = Cfg("STREAM_WSS_BASE").TrimEnd('/');          // wss://<stream-processor-host>
var repIds = Cfg("REP_TEAMS_OBJECT_IDS").Split(',', StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries);

// Our own call id (used for the stream URL and the /events/{id} feed) keyed by ACS correlation id.
var callIds = new System.Collections.Concurrent.ConcurrentDictionary<string, string>();

app.MapGet("/healthz", () => "ok");

// Event Grid webhook: subscription validation + IncomingCall.
app.MapPost("/api/incoming", async (HttpRequest req, ILogger<Program> log) =>
{
    var body = await BinaryData.FromStreamAsync(req.Body);
    foreach (var ev in EventGridEvent.ParseMany(body))
    {
        if (ev.TryGetSystemEventData(out var data) && data is SubscriptionValidationEventData v)
            return Results.Ok(new { validationResponse = v.ValidationCode });

        if (ev.EventType != "Microsoft.Communication.IncomingCall") continue;

        var json = JsonDocument.Parse(ev.Data!.ToString()).RootElement;
        var ctxToken = json.GetProperty("incomingCallContext").GetString()!;
        var correlation = json.GetProperty("correlationId").GetString() ?? Guid.NewGuid().ToString();
        var callId = Guid.NewGuid().ToString("N");
        callIds[correlation] = callId;

        var streaming = new MediaStreamingOptions(MediaStreamingAudioChannel.Unmixed, StreamingTransport.Websocket)
        {
            TransportUri = new Uri($"{streamBase}/ws/{callId}"),
            MediaStreamingContent = MediaStreamingContent.Audio,
            StartMediaStreaming = true,
            EnableBidirectional = false,   // listen only: we never send audio back into the call
            AudioFormat = AudioFormat.Pcm16KMono
        };

        var opts = new AnswerCallOptions(ctxToken, new Uri($"{publicBase}/api/callbacks/{callId}")) { MediaStreamingOptions = streaming };
        var result = await client.AnswerCallAsync(opts);
        log.LogInformation("Answered call {CallId} (connection {Conn})", callId, result.Value.CallConnectionProperties.CallConnectionId);
    }
    return Results.Ok();
});

// Call Automation callbacks: once connected, ring the rep(s) as Teams users.
app.MapPost("/api/callbacks/{callId}", async (string callId, HttpRequest req, ILogger<Program> log) =>
{
    var body = await BinaryData.FromStreamAsync(req.Body);
    var ev = CallAutomationEventParser.Parse(CloudEvent.Parse(body));
    log.LogInformation("{Type} for call {CallId}", ev.GetType().Name, callId);

    switch (ev)
    {
        case CallConnected connected:
            var conn = client.GetCallConnection(connected.CallConnectionId);
            foreach (var id in repIds)
            {
                var invite = new CallInvite(new MicrosoftTeamsUserIdentifier(id));
                await conn.AddParticipantAsync(new AddParticipantOptions(invite));
                log.LogInformation("Invited rep {Rep}", id);
            }
            break;
        case AddParticipantFailed failed:
            log.LogWarning("AddParticipant failed: {Reason}", failed.ResultInformation?.Message);
            break;
    }
    return Results.Ok();
});

app.Run();
