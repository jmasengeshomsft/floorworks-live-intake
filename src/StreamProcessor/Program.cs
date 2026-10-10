using System.Collections.Concurrent;
using System.Net.WebSockets;
using System.Text.Json;
using System.Threading.Channels;
using Microsoft.CognitiveServices.Speech;
using Microsoft.CognitiveServices.Speech.Audio;

var builder = WebApplication.CreateBuilder(args);
var app = builder.Build();
app.UseWebSockets();
var log = app.Logger;

var speechKey = Environment.GetEnvironmentVariable("SPEECH_KEY");
var speechRegion = Environment.GetEnvironmentVariable("SPEECH_REGION") ?? "eastus";
var extractor = new Extractor(
    Environment.GetEnvironmentVariable("FOUNDRY_PROJECT_ENDPOINT") ?? throw new InvalidOperationException("Missing env FOUNDRY_PROJECT_ENDPOINT"),
    Environment.GetEnvironmentVariable("FOUNDRY_AGENT") ?? "floorworks-intake-extractor");

// One event channel per call id; the Canvas app reads /events/{callId} (SSE).
var hubs = new ConcurrentDictionary<string, Channel<string>>();
Channel<string> Hub(string id) => hubs.GetOrAdd(id, _ => Channel.CreateUnbounded<string>());

app.MapGet("/healthz", () => "ok");

app.MapGet("/events/{callId}", async (string callId, HttpContext ctx) =>
{
    ctx.Response.Headers.ContentType = "text/event-stream";
    ctx.Response.Headers.CacheControl = "no-cache";
    await foreach (var line in Hub(callId).Reader.ReadAllAsync(ctx.RequestAborted))
    {
        await ctx.Response.WriteAsync($"data: {line}\n\n", ctx.RequestAborted);
        await ctx.Response.Body.FlushAsync(ctx.RequestAborted);
    }
});

// ACS media streaming target: wss://<host>/ws/{callId}
app.Map("/ws/{callId}", async (string callId, HttpContext ctx) =>
{
    if (!ctx.WebSockets.IsWebSocketRequest) { log.LogWarning("Non-WS request to /ws/{CallId}", callId); ctx.Response.StatusCode = 400; return; }
    using var ws = await ctx.WebSockets.AcceptWebSocketAsync();
    log.LogInformation("WS connected for call {CallId}", callId);
    var frames = 0;
    var hub = Hub(callId);
    var state = new CallState();

    // Per-participant push streams (unmixed audio) keyed by participantRawID.
    var recognizers = new ConcurrentDictionary<string, (PushAudioInputStream push, SpeechRecognizer rec)>();
    var format = AudioStreamFormat.GetWaveFormatPCM(16000, 16, 1);

    (PushAudioInputStream push, SpeechRecognizer rec) Start(string participant)
    {
        var cfg = SpeechConfig.FromSubscription(speechKey!, speechRegion);
        cfg.SpeechRecognitionLanguage = "en-US";
        var push = AudioInputStream.CreatePushStream(format);
        var rec = new SpeechRecognizer(cfg, AudioConfig.FromStreamInput(push));
        var role = state.RoleFor(participant);
        rec.Recognizing += (_, e) => hub.Writer.TryWrite(JsonSerializer.Serialize(
            new { type = "partial", speaker = role, text = e.Result.Text }));
        rec.Recognized += (_, e) =>
        {
            if (e.Result.Reason == ResultReason.RecognizedSpeech && e.Result.Text.Length > 0)
            {
                hub.Writer.TryWrite(JsonSerializer.Serialize(
                    new { type = "final", speaker = role, text = e.Result.Text }));
                state.Add(role, e.Result.Text);
                _ = extractor.RunAsync(state, hub.Writer);
            }
        };
        rec.Canceled += (_, e) => log.LogWarning("STT canceled for {P}: {Reason} {Err}", participant, e.Reason, e.ErrorDetails);
        log.LogInformation("Starting STT for participant {P} as {Role}", participant, role);
        rec.StartContinuousRecognitionAsync().GetAwaiter().GetResult();
        return (push, rec);
    }

    var buf = new byte[64 * 1024];
    var closed = false;
    try
    {
        while (ws.State == WebSocketState.Open && !closed)
        {
            var ms = new MemoryStream();
            WebSocketReceiveResult r;
            do
            {
                r = await ws.ReceiveAsync(buf, ctx.RequestAborted);
                if (r.MessageType == WebSocketMessageType.Close) { closed = true; break; }
                ms.Write(buf, 0, r.Count);
            } while (!r.EndOfMessage);
            if (closed) break;

            var root = JsonDocument.Parse(ms.ToArray()).RootElement;
            var kindStr = root.TryGetProperty("kind", out var k0) ? k0.GetString() : "?";
            if (frames++ < 3 || kindStr != "AudioData" && frames < 20) log.LogInformation("WS frame #{N} kind={Kind}", frames, kindStr);
            if (kindStr != "AudioData") continue;

            var ad = root.GetProperty("audioData");
            if (ad.TryGetProperty("silent", out var s) && s.GetBoolean()) continue;
            var who = ad.TryGetProperty("participantRawID", out var p) ? p.GetString() ?? "mixed" : "mixed";
            var pcm = Convert.FromBase64String(ad.GetProperty("data").GetString()!);
            recognizers.GetOrAdd(who, Start).push.Write(pcm);
        }
    }
    catch (OperationCanceledException) { }
    catch (Exception ex) { log.LogError(ex, "WS loop failed for {CallId}", callId); }
    log.LogInformation("WS closed for call {CallId}, frames={Frames}", callId, frames);
    foreach (var (push, rec) in recognizers.Values)
    {
        push.Close();
        await rec.StopContinuousRecognitionAsync();
        rec.Dispose();
    }
    hub.Writer.TryWrite(JsonSerializer.Serialize(new { type = "end" }));
    if (ws.State is WebSocketState.Open or WebSocketState.CloseReceived)
        await ws.CloseAsync(WebSocketCloseStatus.NormalClosure, "done", CancellationToken.None);
});

app.Run();
