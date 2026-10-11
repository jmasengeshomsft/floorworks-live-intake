// FloorWorks VoiceIntake: one app that owns the ACS call.
//  - Receptionist: native Foundry voice agent (bridged over its voice WebSocket). It can call transfer_to_rep.
//  - Handoff: after the receptionist's farewell, the AI is muted and the Teams rep is added.
//  - Notes: per-speaker STT feeds a text Foundry agent (note-taker) for the whole call, before and after the transfer.
using System.Collections.Concurrent;
using System.Net.WebSockets;
using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;
using System.Threading.Channels;
using Azure.Communication;
using Azure.Communication.CallAutomation;
using Azure.Messaging;
using Azure.Messaging.EventGrid;
using Azure.Messaging.EventGrid.SystemEvents;
using Microsoft.CognitiveServices.Speech;
using Microsoft.CognitiveServices.Speech.Audio;

var builder = WebApplication.CreateBuilder(args);
var app = builder.Build();
app.UseWebSockets();

string Cfg(string k) => Environment.GetEnvironmentVariable(k) ?? throw new InvalidOperationException($"Missing env {k}");
string Opt(string k, string d) => Environment.GetEnvironmentVariable(k) ?? d;

var client = new CallAutomationClient(Cfg("ACS_CONNECTION_STRING"));
var publicBase = Cfg("PUBLIC_BASE_URL").TrimEnd('/');
var wsBase = publicBase.Replace("https://", "wss://");
var repIds = Opt("REP_TEAMS_OBJECT_IDS", "").Split(',', StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries);
var foundryEndpoint = Cfg("FOUNDRY_PROJECT_ENDPOINT").TrimEnd('/');
var voiceAgentName = Opt("VOICE_AGENT_NAME", "floorworks-voice-agent-app");
var speechKey = Cfg("SPEECH_KEY");
var speechRegion = Opt("SPEECH_REGION", "eastus");
// acs-stt (default): Speech STT for every speaker. voice-agent: caller text from the voice agent until handoff.
var transcriptionMode = Opt("TRANSCRIPTION_MODE", "acs-stt").ToLowerInvariant();
var extractor = new Extractor(foundryEndpoint, Opt("NOTETAKER_AGENT", "floorworks-notetaker"));
var drafts = new DraftStore(Opt("DATAVERSE_URL", "https://floorworks.crm.dynamics.com"));
extractor.OnResult = async (st, res) =>
{
    try { await drafts.SaveNotes(st, res); }
    catch (Exception ex) { app.Logger.LogWarning("[{Id}] draft save error: {M}", st.CallId, ex.Message); }
};
var repKey = Opt("REP_KEY", "");
var cred = new Azure.Identity.DefaultAzureCredential();
var states = new ConcurrentDictionary<string, CallState>();
var hubs = new ConcurrentDictionary<string, Channel<string>>();
Channel<string> Hub(string id) => hubs.GetOrAdd(id, _ => Channel.CreateUnbounded<string>());

app.MapGet("/healthz", () => "ok");

// ---- Rep console: live draft for a call; the rep reviews, corrects and submits ----
bool RepAuthorized(HttpRequest r) => repKey == "" || r.Headers["x-rep-key"] == repKey || r.Query["k"] == repKey;
app.MapGet("/rep", (HttpRequest r) => RepAuthorized(r) ? Results.Content(RepUi.Html, "text/html") : Results.Unauthorized());

var rep = app.MapGroup("/api/rep");
rep.AddEndpointFilter(async (ctx, next) =>
    RepAuthorized(ctx.HttpContext.Request) ? await next(ctx) : Results.Unauthorized());

rep.MapGet("/calls", async () => Results.Json(new { live = states.Keys, recent = await drafts.RecentDrafts() }));

rep.MapGet("/draft/{callId}", async (string callId) =>
{
    var d = await drafts.FindDraft(callId);
    states.TryGetValue(callId, out var st);
    JsonNode? customer = null, candidates = null;
    var custId = d?["_bdm_customerid_value"]?.ToString();
    if (custId is not null) customer = await drafts.GetCustomer(custId);
    else
    {
        candidates = await drafts.SuggestCustomers(d?["bdm_customername"]?.ToString(),
            d?["bdm_contactphone"]?.ToString(), d?["bdm_callerphone"]?.ToString() ?? st?.CallerPhone);
    }
    return Results.Json(new
    {
        callId, live = st is not null, draft = d, customer, candidates,
        transcript = st is not null ? st.Snapshot().transcript : null
    });
});

rep.MapGet("/customers", async (string q) => Results.Json(await drafts.SearchCustomers(q)));
rep.MapGet("/equipment", async (string? customerId) => Results.Json(await drafts.ListEquipment(customerId)));

rep.MapPost("/draft/{callId}", async (string callId, JsonNode edit) =>
{
    try { await drafts.Update(callId, edit); return Results.Ok(); }
    catch (Exception ex) { return Results.BadRequest(new { error = ex.Message }); }
});

rep.MapPost("/draft/{callId}/customer", async (string callId, JsonNode body) =>
{
    try { await drafts.CreateCustomerForDraft(callId, body["name"]?.ToString() ?? "", body["phone"]?.ToString()); return Results.Ok(); }
    catch (Exception ex) { return Results.BadRequest(new { error = ex.Message }); }
});

rep.MapPost("/draft/{callId}/submit", async (string callId, JsonNode body) =>
{
    try { return Results.Json(new { ticket = await drafts.Submit(callId, body["submittedBy"]?.ToString() ?? "rep") }); }
    catch (Exception ex) { return Results.BadRequest(new { error = ex.Message }); }
});

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
        var phone = "";
        if (json.TryGetProperty("from", out var from))
        {
            if (from.TryGetProperty("phoneNumber", out var pn) && pn.TryGetProperty("value", out var pv)) phone = pv.GetString() ?? "";
            else if (from.TryGetProperty("rawId", out var rid)) phone = rid.GetString() ?? "";
        }
        states[callId] = new CallState { CallId = callId, CallerPhone = phone };

        var streaming = new MediaStreamingOptions(MediaStreamingAudioChannel.Unmixed, StreamingTransport.Websocket)
        {
            TransportUri = new Uri($"{wsBase}/ws/{callId}"),
            MediaStreamingContent = MediaStreamingContent.Audio,
            StartMediaStreaming = true,
            EnableBidirectional = true,
            AudioFormat = AudioFormat.Pcm24KMono
        };
        try
        {
            var r = await client.AnswerCallAsync(new AnswerCallOptions(ctx, new Uri($"{publicBase}/api/callbacks/{callId}")) { MediaStreamingOptions = streaming });
            log.LogInformation("[{Id}] ANSWER OK caller={P} conn={C}", callId, phone, r.Value.CallConnectionProperties.CallConnectionId);
        }
        catch (Exception e) { log.LogError("[{Id}] ANSWER FAILED: {M}", callId, e.Message); }
    }
    return Results.Ok();
});

app.MapPost("/api/callbacks/{callId}", async (string callId, HttpRequest req, ILogger<Program> log) =>
{
    var body = await BinaryData.FromStreamAsync(req.Body);
    var ev = CallAutomationEventParser.Parse(CloudEvent.Parse(body));
    log.LogInformation("[{Id}] EVENT {T}", callId, ev.GetType().Name);
    var st = states.GetOrAdd(callId, _ => new CallState { CallId = callId });
    switch (ev)
    {
        case CallConnected c: st.ConnId = c.CallConnectionId; break;
        case AddParticipantSucceeded: st.Muted = true; st.RepJoined = true; log.LogInformation("[{Id}] rep joined; AI muted", callId); break;
        case AddParticipantFailed f:
            // Rep did not join: let the receptionist keep helping the caller.
            st.Muted = false; st.TransferStarted = false;
            log.LogWarning("[{Id}] AddParticipant FAILED {M}", callId, f.ResultInformation?.Message);
            break;
        case MediaStreamingFailed m: log.LogWarning("[{Id}] MediaStreaming FAILED {M}", callId, m.ResultInformation?.Message); break;
    }
    return Results.Ok();
});

app.Map("/ws/{callId}", async (string callId, HttpContext ctx, ILogger<Program> log) =>
{
    if (!ctx.WebSockets.IsWebSocketRequest) return Results.BadRequest();
    using var ws = await ctx.WebSockets.AcceptWebSocketAsync();
    log.LogInformation("[{Id}] WS connected; rep UI: {Url}/rep?call={Id}", callId, publicBase, callId);

    var state = states.GetOrAdd(callId, _ => new CallState { CallId = callId });
    Interlocked.Increment(ref state.ActiveWs);
    var isReconnect = Interlocked.Increment(ref state.Connections) > 1;
    var hub = Hub(callId);
    var sendLock = new SemaphoreSlim(1, 1);
    var vlLock = new SemaphoreSlim(1, 1);
    using var cts = new CancellationTokenSource();
    ClientWebSocket? vl = null;
    var aiRef = new List<byte>();   // AI audio awaiting playback; replayed as the echo reference

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

    // ---- Per-speaker STT (unmixed audio, resampled 24k -> 16k) ----
    var recognizers = new ConcurrentDictionary<string, (PushAudioInputStream push, SpeechRecognizer rec)>();
    var sttFormat = AudioStreamFormat.GetWaveFormatPCM(16000, 16, 1);
    (PushAudioInputStream push, SpeechRecognizer rec) StartStt(string participant)
    {
        var cfg = SpeechConfig.FromSubscription(speechKey, speechRegion);
        cfg.SpeechRecognitionLanguage = "en-US";
        var push = AudioInputStream.CreatePushStream(sttFormat);
        var rec = new SpeechRecognizer(cfg, AudioConfig.FromStreamInput(push));
        var role = state.RoleFor(participant);
        rec.Recognized += (_, e) =>
        {
            if (e.Result.Reason != ResultReason.RecognizedSpeech || e.Result.Text.Length == 0) return;
            log.LogInformation("[{Id}] STT {Role}: {T}", callId, role, e.Result.Text);
            hub.Writer.TryWrite(JsonSerializer.Serialize(new { type = "final", speaker = role, text = e.Result.Text }));
            state.Add(role, e.Result.Text);
            _ = extractor.RunAsync(state, hub.Writer, log);
        };
        rec.Canceled += (_, e) => log.LogWarning("[{Id}] STT canceled {P}: {R} {E}", callId, participant, e.Reason, e.ErrorDetails);
        rec.StartContinuousRecognitionAsync().GetAwaiter().GetResult();
        log.LogInformation("[{Id}] STT started for {P} as {Role}", callId, participant, role);
        return (push, rec);
    }

    // ---- Receptionist: native Foundry voice agent ----
    Task vlRx = Task.CompletedTask;
    try
    {
        vl = new ClientWebSocket();
        var tok = await cred.GetTokenAsync(new Azure.Core.TokenRequestContext(new[] { "https://ai.azure.com/.default" }));
        vl.Options.SetRequestHeader("Authorization", "Bearer " + tok.Token);
        vl.Options.SetRequestHeader("foundry-features", "VoiceAgents=V1Preview");
        vl.Options.AddSubProtocol("realtime");
        var wsUrl = foundryEndpoint.Replace("https://", "wss://") + $"/agents/{voiceAgentName}/endpoint/protocols/voice?api-version=v1";
        await vl.ConnectAsync(new Uri(wsUrl), CancellationToken.None);
        log.LogInformation("[{Id}] voice agent connected ({A})", callId, voiceAgentName);

        // Opt-in: ask Voice Live for noise suppression, echo cancellation and semantic VAD. The agent may reject it; errors are logged as VA ERROR.
        if (Environment.GetEnvironmentVariable("VOICE_SESSION_TUNING") == "on")
        {
            // One update per setting so a rejected one doesn't block the others.
            await SendVl(new { type = "session.update", session = new { type = "realtime", audio = new { input = new { noise_reduction = new { type = "azure_deep_noise_suppression" } } } } });
            await SendVl(new { type = "session.update", session = new { type = "realtime", audio = new { input = new { echo_cancellation = new { type = "server_echo_cancellation" } } } } });
            await SendVl(new { type = "session.update", session = new { type = "realtime", audio = new { input = new { turn_detection = new { type = "azure_semantic_vad" } } } } });
            log.LogInformation("[{Id}] VA session tuning requested", callId);
        }

        // Context for the agent. Used to tag any ticket it creates so the note-taker can find it.
        var resumeNote = "";
        if (isReconnect)
        {
            var recent = state.Snapshot().transcript.Select(n => $"{n!["speaker"]}: {n["text"]}").TakeLast(12);
            resumeNote = " IMPORTANT: this call is already in progress and the audio connection just dropped briefly. Do NOT greet or introduce yourself again; continue naturally from where you left off. Conversation so far: " + string.Join(" | ", recent);
            log.LogInformation("[{Id}] media reconnect; resuming without greeting", callId);
        }
        await SendVl(new
        {
            type = "conversation.item.create",
            item = new
            {
                type = "message",
                role = "system",
                content = new[] { new { type = "input_text", text = $"Call context: Call ID is {state.CallId}. Caller phone is {state.CallerPhone}. When you create or update a service ticket, put 'Call ID: {state.CallId}' and the caller phone in its description." + resumeNote } }
            }
        });
    }
    catch (Exception e) { log.LogError("[{Id}] voice agent connect FAILED: {M}", callId, e.Message); vl = null; }

    if (vl is not null)
    {
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

                    if (t == "response.output_audio.delta" && !state.Muted)
                    {
                        lock (aiRef) aiRef.AddRange(Convert.FromBase64String(e.GetProperty("delta").GetString()!));
                        await SendAcs(JsonSerializer.Serialize(new { kind = "AudioData", audioData = new { data = e.GetProperty("delta").GetString() }, stopAudio = (string?)null }));
                    }
                    else if (t == "input_audio_buffer.speech_started" && !state.Muted)
                    {
                        lock (aiRef) aiRef.Clear();
                        await SendAcs("{\"kind\":\"StopAudio\",\"audioData\":null,\"stopAudio\":{}}");
                    }
                    else if (t == "conversation.item.input_audio_transcription.completed" && e.TryGetProperty("transcript", out var ct) && !string.IsNullOrWhiteSpace(ct.GetString()))
                    {
                        var heard = ct.GetString()!.Trim();
                        log.LogInformation("[{Id}] VA heard: {T}", callId, heard);
                        if (transcriptionMode == "voice-agent")
                        {
                            hub.Writer.TryWrite(JsonSerializer.Serialize(new { type = "final", speaker = "customer", text = heard }));
                            state.Add("customer", heard);
                            _ = extractor.RunAsync(state, hub.Writer, log);
                        }
                    }
                    else if (t == "response.done") state.ResponseDone.TrySetResult();
                    else if (t == "error") log.LogWarning("[{Id}] VA ERROR {E}", callId, e.GetProperty("error").ToString());
                    else if (t == "response.output_audio_transcript.done" && e.TryGetProperty("transcript", out var tr) && !string.IsNullOrWhiteSpace(tr.GetString()))
                    {
                        log.LogInformation("[{Id}] AI: {T}", callId, tr.GetString());
                        state.Add("assistant", tr.GetString()!);
                        _ = extractor.RunAsync(state, hub.Writer, log);
                    }
                    else if (t == "response.output_item.done" && e.TryGetProperty("item", out var it) && it.TryGetProperty("type", out var itt)
                             && itt.GetString() == "function_call" && it.GetProperty("name").GetString() == "transfer_to_rep" && !state.TransferStarted)
                    {
                        state.TransferStarted = true;
                        var cid = it.GetProperty("call_id").GetString();
                        state.ResponseDone = new TaskCompletionSource();
                        log.LogInformation("[{Id}] transfer_to_rep requested", callId);
                        await SendVl(new { type = "conversation.item.create", item = new { type = "function_call_output", call_id = cid, output = "{\"status\":\"connecting\"}" } });
                        _ = Task.Run(async () =>
                        {
                            try
                            {
                                // Let the farewell finish, silence the AI, then add the rep.
                                await Task.WhenAny(state.ResponseDone.Task, Task.Delay(10000));
                                await Task.Delay(1500);
                                state.Muted = true;
                                var conn = client.GetCallConnection(state.ConnId);
                                foreach (var id in repIds)
                                {
                                    await conn.AddParticipantAsync(new AddParticipantOptions(new CallInvite(new MicrosoftTeamsUserIdentifier(id))));
                                    log.LogInformation("[{Id}] AddParticipant requested for {Rep}", callId, id);
                                }
                            }
                            catch (Exception ex)
                            {
                                state.Muted = false; state.TransferStarted = false;
                                log.LogError("[{Id}] transfer ERROR {M}", callId, ex.Message);
                            }
                        });
                    }
                }
            }
            catch (OperationCanceledException) { }
            catch (Exception e) { log.LogWarning("[{Id}] VA rx ended: {M}", callId, e.Message); }
        });
    }

    // ---- ACS media loop ----
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
            if (kind != "AudioData" || !doc.TryGetProperty("audioData", out var ad)) continue;
            if (ad.TryGetProperty("silent", out var s) && s.GetBoolean()) continue;
            if (!ad.TryGetProperty("data", out var dd)) continue;

            var who = ad.TryGetProperty("participantRawID", out var p) ? p.GetString() ?? "unknown" : "unknown";
            var pcm24 = Convert.FromBase64String(dd.GetString()!);

            // voice-agent mode: the receptionist supplies the caller transcript until handoff; STT starts afterwards.
            var agentCoversCaller = transcriptionMode == "voice-agent" &&             vl is not null && !state.RepJoined && !who.StartsWith("8:orgid:");
            if (!agentCoversCaller)
                recognizers.GetOrAdd(who, StartStt).push.Write(Audio.Resample24To16(pcm24));

            // Only the phone caller feeds the receptionist, and only until the handoff.
            if (vl is not null && !state.Muted && !who.StartsWith("8:orgid:"))
            {
                byte[] reference;
                lock (aiRef)
                {
                    var n = Math.Min(pcm24.Length, aiRef.Count);
                    reference = new byte[pcm24.Length];
                    aiRef.CopyTo(0, reference, 0, n);
                    aiRef.RemoveRange(0, n);
                }
                await SendVl(new { type = "input_audio_buffer.append", audio = Convert.ToBase64String(Audio.Interleave(pcm24, reference)) });
            }
        }
    }
    catch (Exception e) { log.LogWarning("[{Id}] WS ended: {M}", callId, e.Message); }
    finally { cts.Cancel(); if (vl is not null) { try { vl.Abort(); } catch { } } }

    log.LogInformation("[{Id}] WS closed; finalizing notes", callId);
    foreach (var (push, rec) in recognizers.Values)
    {
        push.Close();
        try { await rec.StopContinuousRecognitionAsync(); } catch { }
        rec.Dispose();
    }
    // ACS may drop and reopen the media socket mid-call; only the last socket finalizes.
    if (Interlocked.Decrement(ref state.ActiveWs) > 0) { log.LogInformation("[{Id}] WS superseded by reconnect; not finalizing", callId); return Results.Empty; }
    await Task.Delay(4000);
    if (state.ActiveWs > 0) { log.LogInformation("[{Id}] WS reconnected; not finalizing", callId); return Results.Empty; }
    await extractor.FinalizeAsync(state, hub.Writer, log);
    hub.Writer.TryWrite(JsonSerializer.Serialize(new { type = "end" }));
    states.TryRemove(callId, out _);
    return Results.Empty;
});

app.Run();

static class Audio
{
    // The voice agent expects stereo: channel 0 = caller mic, channel 1 = echo reference (what we just played to the caller).
    public static byte[] Interleave(byte[] mic, byte[] reference)
    {
        var st = new byte[mic.Length * 2];
        for (int i = 0; i + 1 < mic.Length; i += 2)
        {
            st[i * 2] = mic[i]; st[i * 2 + 1] = mic[i + 1];
            st[i * 2 + 2] = reference[i]; st[i * 2 + 3] = reference[i + 1];
        }
        return st;
    }

    // 24 kHz -> 16 kHz mono PCM16 by linear interpolation (ratio 1.5).
    public static byte[] Resample24To16(byte[] pcm)
    {
        int n = pcm.Length / 2;
        int outN = n * 2 / 3;
        var o = new byte[outN * 2];
        for (int i = 0; i < outN; i++)
        {
            double pos = i * 1.5;
            int i0 = (int)pos; int i1 = Math.Min(i0 + 1, n - 1);
            double f = pos - i0;
            short a = BitConverter.ToInt16(pcm, i0 * 2), b = BitConverter.ToInt16(pcm, i1 * 2);
            short v = (short)(a + (b - a) * f);
            o[i * 2] = (byte)(v & 0xff); o[i * 2 + 1] = (byte)(v >> 8);
        }
        return o;
    }
}
