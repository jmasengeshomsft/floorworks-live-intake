using System.Net.WebSockets;
using System.Text;
using System.Text.Json;

// Usage: Simulator <wsUrl> <participant=file.wav> [participant=file.wav ...]
// Replays 16 kHz/16-bit/mono WAVs as ACS-style unmixed AudioData frames in real time.
var url = args[0];
using var ws = new ClientWebSocket();
await ws.ConnectAsync(new Uri(url), default);

async Task Send(object o) =>
    await ws.SendAsync(Encoding.UTF8.GetBytes(JsonSerializer.Serialize(o)), WebSocketMessageType.Text, true, default);

await Send(new { kind = "AudioMetadata", audioMetadata = new { subscriptionId = "sim", encoding = "PCM", sampleRate = 16000, channels = 1, length = 640 } });

var tracks = args.Skip(1).Select(a => a.Split('=', 2)).Select(p => (who: p[0], pcm: File.ReadAllBytes(p[1])[44..])).ToList();
const int frame = 640; // 20 ms
var frames = tracks.Max(t => t.pcm.Length) / frame + 1;
for (var i = 0; i < frames; i++)
{
    foreach (var (who, pcm) in tracks)
    {
        var off = i * frame;
        if (off >= pcm.Length) continue;
        var chunk = pcm[off..Math.Min(off + frame, pcm.Length)];
        await Send(new { kind = "AudioData", audioData = new { participantRawID = who, data = Convert.ToBase64String(chunk), silent = false } });
    }
    await Task.Delay(20);
}
await Task.Delay(3000);
await ws.CloseAsync(WebSocketCloseStatus.NormalClosure, "done", default);
Console.WriteLine("sent");
