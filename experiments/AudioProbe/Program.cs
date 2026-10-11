// Test A probe: can ACS stream UNMIXED audio and accept BIDIRECTIONAL audio on the same call?
// Answers calls, plays a tone to the caller, adds a Teams rep, and logs frames per participant.
using System.Collections.Concurrent;
using System.Net.WebSockets;
using System.Text;
using System.Text.Json;
using Azure.Communication;
using Azure.Communication.CallAutomation;
using Azure.Messaging;
using Azure.Messaging.EventGrid;
using Azure.Messaging.EventGrid.SystemEvents;

var builder = WebApplication.CreateBuilder(args);
var app = builder.Build();
app.UseWebSockets();

string Cfg(string k) => Environment.GetEnvironmentVariable(k) ?? throw new InvalidOperationException($"Missing env {k}");
var client = new CallAutomationClient(Cfg("ACS_CONNECTION_STRING"));
var publicBase = Cfg("PUBLIC_BASE_URL").TrimEnd('/');
var wsBase = publicBase.Replace("https://", "wss://");
var repIds = (Environment.GetEnvironmentVariable("REP_TEAMS_OBJECT_IDS") ?? "")
    .Split(',', StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries);
var channel = Environment.GetEnvironmentVariable("CHANNEL_TYPE") == "mixed" ? MediaStreamingAudioChannel.Mixed : MediaStreamingAudioChannel.Unmixed;
var addDelaySec = int.Parse(Environment.GetEnvironmentVariable("ADD_REP_AFTER_SEC") ?? "12");
// Test B: Voice Live receptionist, handoff to the rep, then the AI goes silent.
var voiceLive = Environment.GetEnvironmentVariable("VOICE_LIVE") == "1";
var vlHost = Environment.GetEnvironmentVariable("VOICE_LIVE_HOST") ?? "ai-foundry-demo-jm.services.ai.azure.com";
var vlModel = Environment.GetEnvironmentVariable("VOICE_LIVE_MODEL") ?? "gpt-realtime";
var voiceAgent = Environment.GetEnvironmentVariable("VOICE_AGENT") == "1";
var voiceAgentName = Environment.GetEnvironmentVariable("VOICE_AGENT_NAME") ?? "floorworks-voice-agent-app";
var foundryProject = Environment.GetEnvironmentVariable("FOUNDRY_PROJECT") ?? "firstProject";
var cred = new Azure.Identity.DefaultAzureCredential();
var states = new ConcurrentDictionary<string, CallState>();

app.MapGet("/healthz", () => "ok");

app.MapPost("/api/incoming", async (HttpRequest req, ILogger<Program> log) =>
{
    var body = await BinaryData.FromStreamAsync(req.Body);
    foreach (var ev in EventGridEvent.ParseMany(body))
    {
        if (ev.TryGetSystemEventData(out var data) && data is SubscriptionValidationEventData v)
            return Results.Ok(new { validationResponse = v.ValidationCode });
        if (ev.EventType != "Microsoft.Communication.IncomingCall") continue;

        var json = JsonDocument.Parse(ev.Data!.ToString()).RootElement;
        var ctx = json.GetProperty("incomingCallContext").GetString()!;
        var callId = Guid.NewGuid().ToString("N")[..8];

        var streaming = new MediaStreamingOptions(channel, StreamingTransport.Websocket)
        {
            TransportUri = new Uri($"{wsBase}/ws/{callId}"),
            MediaStreamingContent = MediaStreamingContent.Audio,
            StartMediaStreaming = true,
            EnableBidirectional = true,
            AudioFormat = voiceLive ? AudioFormat.Pcm24KMono : AudioFormat.Pcm16KMono
        };
        states[callId] = new CallState();
        try
        {
            var r = await client.AnswerCallAsync(new AnswerCallOptions(ctx, new Uri($"{publicBase}/api/callbacks/{callId}")) { MediaStreamingOptions = streaming });
            log.LogInformation("[{Id}] ANSWER OK channel={Ch} bidirectional=true conn={C}", callId, channel, r.Value.CallConnectionProperties.CallConnectionId);
        }
        catch (Exception e)
        {
            log.LogError("[{Id}] ANSWER FAILED channel={Ch}: {M}", callId, channel, e.Message);
        }
    }
    return Results.Ok();
});

app.MapPost("/api/callbacks/{callId}", async (string callId, HttpRequest req, ILogger<Program> log) =>
{
    var body = await BinaryData.FromStreamAsync(req.Body);
    var ev = CallAutomationEventParser.Parse(CloudEvent.Parse(body));
    log.LogInformation("[{Id}] EVENT {T}", callId, ev.GetType().Name);
    switch (ev)
    {
        case CallConnected c when voiceLive:
            states.GetOrAdd(callId, _ => new CallState()).ConnId = c.CallConnectionId;
            break;
        case CallConnected c when repIds.Length > 0:
            _ = Task.Run(async () =>
            {
                await Task.Delay(TimeSpan.FromSeconds(addDelaySec));
                try
                {
                    var conn = client.GetCallConnection(c.CallConnectionId);
                    foreach (var id in repIds)
                    {
                        await conn.AddParticipantAsync(new AddParticipantOptions(new CallInvite(new MicrosoftTeamsUserIdentifier(id))));
                        log.LogInformation("[{Id}] AddParticipant requested for {Rep}", callId, id);
                    }
                }
                catch (Exception e) { log.LogError("[{Id}] AddParticipant ERROR {M}", callId, e.Message); }
            });
            break;
        case AddParticipantSucceeded:
            log.LogInformation("[{Id}] >>> AddParticipant SUCCEEDED", callId);
            if (states.TryGetValue(callId, out var st)) { st.Muted = true; log.LogInformation("[{Id}] >>> AI MUTED (handoff complete)", callId); }
            break;
        case AddParticipantFailed f: log.LogWarning("[{Id}] >>> AddParticipant FAILED {M}", callId, f.ResultInformation?.Message); break;
        case MediaStreamingFailed m: log.LogWarning("[{Id}] >>> MediaStreaming FAILED {M}", callId, m.ResultInformation?.Message); break;
        case MediaStreamingStarted: log.LogInformation("[{Id}] >>> MediaStreaming STARTED", callId); break;
    }
    return Results.Ok();
});

app.Map("/ws/{callId}", async (string callId, HttpContext ctx, ILogger<Program> log) =>
{
    if (!ctx.WebSockets.IsWebSocketRequest) return Results.BadRequest();
    using var ws = await ctx.WebSockets.AcceptWebSocketAsync();
    log.LogInformation("[{Id}] WS connected", callId);

    var frames = new ConcurrentDictionary<string, int>();
    var nonSilent = new ConcurrentDictionary<string, int>();
    var sendLock = new SemaphoreSlim(1, 1);
    using var cts = new CancellationTokenSource();

    // Tone sender: 2s of 440 Hz every ~8s, 20 ms frames at 16 kHz mono PCM16.
    var tone = Task.Run(async () =>
    {
        if (voiceLive) return;
        const int rate = 16000, samples = rate / 50;
        long n = 0;
        try
        {
            while (!cts.IsCancellationRequested && ws.State == WebSocketState.Open)
            {
                await Task.Delay(3000, cts.Token);
                for (int f = 0; f < 100 && ws.State == WebSocketState.Open; f++)
                {
                    var buf = new byte[samples * 2];
                    for (int i = 0; i < samples; i++, n++)
                    {
                        var s = (short)(Math.Sin(2 * Math.PI * 440 * n / rate) * 8000);
                        buf[i * 2] = (byte)(s & 0xff); buf[i * 2 + 1] = (byte)(s >> 8);
                    }
                    var msg = JsonSerializer.Serialize(new { kind = "AudioData", audioData = new { data = Convert.ToBase64String(buf) }, stopAudio = (string?)null });
                    await sendLock.WaitAsync(cts.Token);
                    try { await ws.SendAsync(Encoding.UTF8.GetBytes(msg), WebSocketMessageType.Text, true, cts.Token); }
                    finally { sendLock.Release(); }
                    await Task.Delay(20, cts.Token);
                }
                log.LogInformation("[{Id}] tone sent (2s)", callId);
                await Task.Delay(3000, cts.Token);
            }
        }
        catch (OperationCanceledException) { }
        catch (Exception e) { log.LogWarning("[{Id}] tone sender stopped: {M}", callId, e.Message); }
    });

    var report = Task.Run(async () =>
    {
        try
        {
            while (!cts.IsCancellationRequested)
            {
                await Task.Delay(5000, cts.Token);
                log.LogInformation("[{Id}] FRAMES per participant: {F} | non-silent: {N}", callId,
                    string.Join(", ", frames.Select(k => $"{k.Key}={k.Value}")), string.Join(", ", nonSilent.Select(k => $"{k.Key}={k.Value}")));
            }
        }
        catch (OperationCanceledException) { }
    });

    var state = states.GetOrAdd(callId, _ => new CallState());
    ClientWebSocket? vl = null;
    var transferStarted = false;
    var vlLock = new SemaphoreSlim(1, 1);

    async Task SendAcs(string json)
    {
        await sendLock.WaitAsync();
        try { if (ws.State == WebSocketState.Open) await ws.SendAsync(Encoding.UTF8.GetBytes(json), WebSocketMessageType.Text, true, CancellationToken.None); }
        finally { sendLock.Release(); }
    }
    async Task SendVl(object payload)
    {
        if (vl is null || vl.State != WebSocketState.Open) return;
        await vlLock.WaitAsync();
        try { await vl.SendAsync(JsonSerializer.SerializeToUtf8Bytes(payload), WebSocketMessageType.Text, true, CancellationToken.None); }
        finally { vlLock.Release(); }
    }

    Task vlRx = Task.CompletedTask, handoff = Task.CompletedTask;
    if (voiceLive)
    {
        vl = new ClientWebSocket();
        var tok = await cred.GetTokenAsync(new Azure.Core.TokenRequestContext(new[] { "https://ai.azure.com/.default" }));
        vl.Options.SetRequestHeader("Authorization", "Bearer " + tok.Token);
        if (voiceAgent)
        {
            vl.Options.SetRequestHeader("foundry-features", "VoiceAgents=V1Preview");
            await vl.ConnectAsync(new Uri($"wss://{vlHost}/api/projects/{foundryProject}/agents/{voiceAgentName}/endpoint/protocols/voice?api-version=v1"), CancellationToken.None);
            log.LogInformation("[{Id}] VoiceAgent CONNECTED agent={A}", callId, voiceAgentName);
        }
        else
        {
        await vl.ConnectAsync(new Uri($"wss://{vlHost}/voice-live/realtime?api-version=2025-10-01&model={vlModel}"), CancellationToken.None);
        log.LogInformation("[{Id}] VoiceLive CONNECTED", callId);
        await SendVl(new
        {
            type = "session.update",
            session = new
            {
                instructions = "You are the FloorWorks service desk receptionist. Greet the caller briefly, ask what equipment problem they have, and keep answers to one short sentence. When told to hand off, say one short sentence that you are connecting them to Joseph now.",
                turn_detection = new { type = "azure_semantic_vad" },
                input_audio_format = "pcm16",
                output_audio_format = "pcm16",
                voice = new { name = "en-US-Ava:DragonHDLatestNeural", type = "azure-standard" }
            }
        });
        await SendVl(new { type = "response.create" });
        }

        vlRx = Task.Run(async () =>
        {
            var b = new byte[256 * 1024];
            try
            {
                while (vl.State == WebSocketState.Open)
                {
                    var m = new MemoryStream(); WebSocketReceiveResult rr;
                    do { rr = await vl.ReceiveAsync(b, cts.Token); m.Write(b, 0, rr.Count); } while (!rr.EndOfMessage);
                    if (rr.MessageType == WebSocketMessageType.Close) break;
                    var e = JsonDocument.Parse(m.ToArray()).RootElement;
                    var t = e.GetProperty("type").GetString();
                    if (voiceAgent && t != "response.output_audio.delta" && t != "response.audio.delta")
                        log.LogInformation("[{Id}] VA event {T} {P}", callId, t, e.ToString().Length > 400 ? e.ToString()[..400] : e.ToString());
                    if (voiceAgent && t == "response.output_item.done" && !transferStarted && e.TryGetProperty("item", out var it) && it.TryGetProperty("type", out var itt) && itt.GetString() == "function_call"
                        && it.GetProperty("name").GetString() == "transfer_to_rep")
                    {
                        transferStarted = true;
                        var cid = it.GetProperty("call_id").GetString();
                        log.LogInformation("[{Id}] >>> TOOL CALL transfer_to_rep", callId);
                        {
                            state.ResponseDone = new TaskCompletionSource();
                            await SendVl(new { type = "conversation.item.create", item = new { type = "function_call_output", call_id = cid, output = "{\"status\":\"connecting\"}" } });
                            _ = Task.Run(async () =>
                            {
                                try
                                {
                                    await Task.WhenAny(state.ResponseDone.Task, Task.Delay(10000));
                                    await Task.Delay(1500);
                                    state.Muted = true;
                                    log.LogInformation("[{Id}] AI muted before AddParticipant", callId);
                                    var conn = client.GetCallConnection(state.ConnId);
                                    foreach (var id in repIds)
                                    {
                                        await conn.AddParticipantAsync(new AddParticipantOptions(new CallInvite(new MicrosoftTeamsUserIdentifier(id))));
                                        log.LogInformation("[{Id}] AddParticipant requested for {Rep}", callId, id);
                                    }
                                }
                                catch (Exception ex) { log.LogError("[{Id}] transfer ERROR {M}", callId, ex.Message); }
                            });
                        }
                    }
                    if ((t == "response.audio.delta" || t == "response.output_audio.delta") && !state.Muted)
                        await SendAcs(JsonSerializer.Serialize(new { kind = "AudioData", audioData = new { data = e.GetProperty("delta").GetString() }, stopAudio = (string?)null }));
                    else if (t == "input_audio_buffer.speech_started" && !state.Muted)
                        await SendAcs("{\"kind\":\"StopAudio\",\"audioData\":null,\"stopAudio\":{}}");
                    else if (t == "response.done") { state.ResponseDone.TrySetResult(); log.LogInformation("[{Id}] VL response.done", callId); }
                    else if (t == "error") log.LogWarning("[{Id}] VL ERROR {E}", callId, e.GetProperty("error").ToString());
                }
            }
            catch (OperationCanceledException) { }
            catch (Exception e) { log.LogWarning("[{Id}] VL rx ended: {M}", callId, e.Message); }
        });

        if (!voiceAgent) handoff = Task.Run(async () =>
        {
            try
            {
                await Task.Delay(TimeSpan.FromSeconds(addDelaySec), cts.Token);
                state.ResponseDone = new TaskCompletionSource();
                await SendVl(new { type = "response.create", response = new { instructions = "Tell the caller you are connecting them to Joseph now." } });
                await Task.WhenAny(state.ResponseDone.Task, Task.Delay(10000, cts.Token));
                await Task.Delay(2500, cts.Token);
                var conn = client.GetCallConnection(state.ConnId);
                foreach (var id in repIds)
                {
                    await conn.AddParticipantAsync(new AddParticipantOptions(new CallInvite(new MicrosoftTeamsUserIdentifier(id))));
                    log.LogInformation("[{Id}] AddParticipant requested for {Rep}", callId, id);
                }
            }
            catch (OperationCanceledException) { }
            catch (Exception e) { log.LogError("[{Id}] handoff ERROR {M}", callId, e.Message); }
        });
    }

    var rx = new byte[64 * 1024];
    try
    {
        while (ws.State == WebSocketState.Open)
        {
            var ms = new MemoryStream();
            WebSocketReceiveResult r;
            do { r = await ws.ReceiveAsync(rx, CancellationToken.None); ms.Write(rx, 0, r.Count); } while (!r.EndOfMessage);
            if (r.MessageType == WebSocketMessageType.Close) break;
            var doc = JsonDocument.Parse(ms.ToArray()).RootElement;
            var kind = doc.TryGetProperty("kind", out var k) ? k.GetString() : null;
            if (kind == "AudioMetadata")
                log.LogInformation("[{Id}] METADATA {M}", callId, doc.GetProperty("audioMetadata").ToString());
            else if (kind == "AudioData" && doc.TryGetProperty("audioData", out var ad))
            {
                var who = ad.TryGetProperty("participantRawID", out var p) ? p.GetString() ?? "unknown" : "mixed/none";
                frames.AddOrUpdate(who, 1, (_, v) => v + 1);
                if (!(ad.TryGetProperty("silent", out var s) && s.GetBoolean())) nonSilent.AddOrUpdate(who, 1, (_, v) => v + 1);
                // Forward only the phone caller's audio, and only until the handoff completes.
                if (vl is not null && !state.Muted && !who.StartsWith("8:orgid:") && ad.TryGetProperty("data", out var dd) && !(ad.TryGetProperty("silent", out var s2) && s2.GetBoolean()))
                {
                    var audio = dd.GetString()!;
                    if (voiceAgent)
                    {
                        // Agent expects stereo: ch0 = caller mic, ch1 = echo reference (silent here).
                        var mono = Convert.FromBase64String(audio);
                        var st = new byte[mono.Length * 2];
                        for (int i = 0; i + 1 < mono.Length; i += 2) { st[i * 2] = mono[i]; st[i * 2 + 1] = mono[i + 1]; }
                        audio = Convert.ToBase64String(st);
                    }
                    await SendVl(new { type = "input_audio_buffer.append", audio });
                }
            }
        }
    }
    catch (Exception e) { log.LogWarning("[{Id}] WS ended: {M}", callId, e.Message); }
    finally { cts.Cancel(); if (vl is not null) { try { vl.Abort(); } catch { } } }
    log.LogInformation("[{Id}] WS closed. FINAL frames: {F} | non-silent: {N}", callId,
        string.Join(", ", frames.Select(k => $"{k.Key}={k.Value}")), string.Join(", ", nonSilent.Select(k => $"{k.Key}={k.Value}")));
    return Results.Empty;
});

app.Run();

class CallState
{
    public string ConnId = "";
    public volatile bool Muted;
    public TaskCompletionSource ResponseDone = new();
}
