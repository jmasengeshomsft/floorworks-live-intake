using System.Net.Http.Headers;
using System.Text;
using System.Text.Json.Nodes;
using Azure.Core;
using Azure.Identity;

// Persists the live service-request draft for a call in Dataverse and creates the real ticket when the rep submits.
public sealed class DraftStore
{
    private const int OptBase = 105730000;
    private static readonly HttpClient Http = new() { Timeout = TimeSpan.FromSeconds(60) };
    private readonly string _org, _api;
    private readonly TokenCredential _cred =
        Environment.GetEnvironmentVariable("LOCAL_DEV") == "1" ? new AzureCliCredential() : new DefaultAzureCredential();
    private readonly SemaphoreSlim _gate = new(1, 1);

    public DraftStore(string orgUrl) { _org = orgUrl.TrimEnd('/'); _api = _org + "/api/data/v9.2/"; }

    private async Task<JsonNode?> Send(HttpMethod m, string path, JsonNode? body = null, bool wantId = false)
    {
        var tok = await _cred.GetTokenAsync(new TokenRequestContext(new[] { _org + "/.default" }), default);
        using var req = new HttpRequestMessage(m, _api + path);
        req.Headers.Authorization = new AuthenticationHeaderValue("Bearer", tok.Token);
        req.Headers.Add("OData-Version", "4.0");
        req.Headers.Add("Prefer", "odata.include-annotations=\"OData.Community.Display.V1.FormattedValue\"");
        if (body is not null) req.Content = new StringContent(body.ToJsonString(), Encoding.UTF8, "application/json");
        using var resp = await Http.SendAsync(req);
        var text = await resp.Content.ReadAsStringAsync();
        if (!resp.IsSuccessStatusCode) throw new Exception($"dataverse {(int)resp.StatusCode} {m} {path}: {text[..Math.Min(300, text.Length)]}");
        if (wantId)
        {
            var loc = resp.Headers.TryGetValues("OData-EntityId", out var v) ? v.First() : "";
            var i = loc.LastIndexOf('('); return JsonValue.Create(loc[(i + 1)..^1]);
        }
        return string.IsNullOrWhiteSpace(text) ? null : JsonNode.Parse(text);
    }

    private static string Esc(string s) => Uri.EscapeDataString(s.Replace("'", "''"));
    private static string Digits(string? s) => new string((s ?? "").Where(char.IsDigit).ToArray());
    private static int PriorityOpt(string? p) => OptBase + (p?.ToLowerInvariant()) switch
    {
        "low" => 0, "medium" or "normal" => 1, "high" => 2, "critical" or "urgent" => 3, _ => 1
    };

    private const string DraftSelect = "bdm_servicerequestdraftid,bdm_name,bdm_callid,bdm_callerphone,bdm_customername,bdm_equipmenttext,bdm_complaint,bdm_priority,bdm_summary,bdm_transcript,bdm_coaching,bdm_status,bdm_submittedby,bdm_submittedon,bdm_contactphone,bdm_assetnumber,bdm_location,bdm_preferredtime,bdm_editedfields,createdon,_bdm_customerid_value,_bdm_equipmentid_value,_bdm_ticketid_value";

    public async Task<JsonNode?> FindDraft(string callId)
    {
        var r = await Send(HttpMethod.Get, $"bdm_servicerequestdrafts?$select={DraftSelect}&$filter=bdm_callid eq '{Esc(callId)}'&$top=1");
        return r?["value"]?.AsArray().FirstOrDefault();
    }

    // Called after each extraction: creates the draft on first use, then refreshes the agent-owned fields.
    // Customer and equipment links are only set while empty so a rep's choice is never overwritten.
    public async Task SaveNotes(CallState state, JsonNode? result, string status = "Draft")
    {
        await _gate.WaitAsync();
        try
        {
            var f = result?["fields"] ?? state.Fields;
            var draft = await FindDraft(state.CallId);
            if (draft is not null && draft["bdm_status"]?.GetValue<int>() != OptBase) return; // already submitted/discarded

            var edited = (draft?["bdm_editedfields"]?.ToString() ?? "").Split(',', StringSplitOptions.RemoveEmptyEntries).ToHashSet();
            var body = new JsonObject { ["bdm_callerphone"] = state.CallerPhone, ["bdm_transcript"] = TranscriptText(state),
                ["bdm_summary"] = f["summary"]?.ToString(), ["bdm_coaching"] = result?["next_question"]?.ToString() };
            // Agent-owned form fields; anything the rep has typed is left alone.
            void Put(string key, string col, string? v) { if (!edited.Contains(key) && !string.IsNullOrWhiteSpace(v)) body[col] = v; }
            Put("customername", "bdm_customername", f["customer_name"]?.ToString());
            Put("contactphone", "bdm_contactphone", f["phone"]?.ToString() ?? state.CallerPhone);
            Put("equipmenttext", "bdm_equipmenttext", f["equipment_model"]?.ToString());
            Put("assetnumber", "bdm_assetnumber", f["equipment_serial_or_asset"]?.ToString());
            Put("location", "bdm_location", f["location"]?.ToString());
            Put("preferredtime", "bdm_preferredtime", f["preferred_time"]?.ToString());
            Put("complaint", "bdm_complaint", f["complaint"]?.ToString());
            if (!edited.Contains("priority") && f["priority"] is not null) body["bdm_priority"] = PriorityOpt(f["priority"]?.ToString());
            else if (draft is null) body["bdm_priority"] = PriorityOpt(null);

            if (draft is null || draft["_bdm_customerid_value"] is null)
            {
                var cid = await MatchCustomer(state.CallerPhone);
                if (cid is not null) body["bdm_CustomerId@odata.bind"] = $"/bdm_customers({cid})";
            }
            if (draft is null || draft["_bdm_equipmentid_value"] is null)
            {
                var eq = result?["matches"]?.AsArray().FirstOrDefault(m => m?["table"]?.ToString() == "bdm_equipment")?["id"]?.ToString();
                var asset = f["equipment_serial_or_asset"]?.ToString();
                if (eq is null && !string.IsNullOrWhiteSpace(asset))
                {
                    var r = await Send(HttpMethod.Get, $"bdm_equipments?$select=bdm_equipmentid&$filter=bdm_assetnumber eq '{Esc(asset.Trim())}'&$top=2");
                    var rows = r?["value"]?.AsArray(); if (rows?.Count == 1) eq = rows[0]!["bdm_equipmentid"]!.ToString();
                }
                if (eq is not null) body["bdm_EquipmentId@odata.bind"] = $"/bdm_equipments({eq})";
            }

            if (draft is null)
            {
                body["bdm_name"] = $"Call {state.CallerPhone} {DateTime.UtcNow:yyyy-MM-dd HH:mm}";
                body["bdm_callid"] = state.CallId;
                body["bdm_status"] = OptBase;
                await Send(HttpMethod.Post, "bdm_servicerequestdrafts", body, wantId: true);
            }
            else await Send(HttpMethod.Patch, $"bdm_servicerequestdrafts({draft["bdm_servicerequestdraftid"]})", body);
        }
        finally { _gate.Release(); }
    }

    public static string TranscriptText(CallState s)
    {
        var (_, t) = s.Snapshot();
        return string.Join("\n", t.Select(l => $"[{l!["speaker"]}] {l["text"]}"));
    }

    // Exactly one customer on that phone number, otherwise leave it for the rep to choose.
    public async Task<string?> MatchCustomer(string phone)
    {
        var d = Digits(phone); if (d.Length > 10) d = d[^10..]; if (d.Length < 7) return null;
        var r = await Send(HttpMethod.Get, $"bdm_customers?$select=bdm_customerid&$filter=contains(bdm_primaryphone,'{d}')&$top=2");
        var rows = r?["value"]?.AsArray();
        return rows?.Count == 1 ? rows[0]!["bdm_customerid"]!.ToString() : null;
    }

    private JsonArray? _custCache; private DateTime _custCacheAt;
    private const string CustSelect = "bdm_customerid,bdm_name,bdm_accountnumber,bdm_firstname,bdm_lastname,bdm_primaryphone,bdm_email,bdm_street,bdm_city,bdm_state,bdm_zip";

    private async Task<JsonArray> AllCustomers()
    {
        if (_custCache is null || DateTime.UtcNow - _custCacheAt > TimeSpan.FromSeconds(30))
        {
            var rows = (await Send(HttpMethod.Get, $"bdm_customers?$select={CustSelect}&$top=500"))?["value"]?.AsArray();
            _custCache = rows is null ? new JsonArray() : JsonNode.Parse(rows.ToJsonString())!.AsArray();
            _custCacheAt = DateTime.UtcNow;
        }
        return _custCache;
    }

    private static string Soundex(string s)
    {
        var l = new string(s.ToUpperInvariant().Where(char.IsLetter).ToArray());
        if (l.Length == 0) return "";
        const string map = "01230120022455012623010202"; // A..Z
        var sb = new StringBuilder().Append(l[0]); var last = map[l[0] - 'A'];
        foreach (var ch in l.Skip(1))
        {
            var c = map[ch - 'A'];
            if (c != '0' && c != last) sb.Append(c);
            if (ch != 'H' && ch != 'W') last = c;
        }
        return sb.ToString().PadRight(4, '0')[..4];
    }

    private static double Sim(string a, string b)
    {
        a = a.ToLowerInvariant(); b = b.ToLowerInvariant();
        if (a.Length == 0 || b.Length == 0) return 0;
        var d = new int[a.Length + 1, b.Length + 1];
        for (var i = 0; i <= a.Length; i++) d[i, 0] = i;
        for (var j = 0; j <= b.Length; j++) d[0, j] = j;
        for (var i = 1; i <= a.Length; i++)
            for (var j = 1; j <= b.Length; j++)
                d[i, j] = Math.Min(Math.Min(d[i - 1, j] + 1, d[i, j - 1] + 1), d[i - 1, j - 1] + (a[i - 1] == b[j - 1] ? 0 : 1));
        var s = 1.0 - (double)d[a.Length, b.Length] / Math.Max(a.Length, b.Length);
        return Soundex(a) == Soundex(b) ? Math.Max(s, 0.85) : s;
    }

    // Ranks customers against a possibly misheard name and any phone numbers from the call.
    public async Task<JsonArray> SuggestCustomers(string? name, params string?[] phones)
    {
        var tokens = (name ?? "").Split(new[] { ' ', ',', '.' }, StringSplitOptions.RemoveEmptyEntries).Where(t => t.Any(char.IsLetter)).ToArray();
        var tails = phones.Select(p => { var d = Digits(p); return d.Length >= 10 ? d[^10..] : ""; }).Where(t => t.Length > 0).ToHashSet();
        var scored = new List<(double score, string why, JsonNode c)>();
        foreach (var c in await AllCustomers())
        {
            var first = c!["bdm_firstname"]?.ToString() ?? ""; var last = c["bdm_lastname"]?.ToString() ?? "";
            double ns = 0;
            if (tokens.Length > 0)
                ns = tokens.Average(t => Math.Max(Sim(t, first), Sim(t, last)));
            var ph = Digits(c["bdm_primaryphone"]?.ToString());
            var phoneHit = ph.Length >= 10 && tails.Contains(ph[^10..]);
            var score = Math.Max(ns, 0) + (phoneHit ? 0.6 : 0);
            if (ns >= 0.6 || phoneHit)
                scored.Add((score, phoneHit && ns >= 0.6 ? "name + phone match" : phoneHit ? "phone match" : "similar name", c));
        }
        var res = new JsonArray();
        foreach (var (score, why, c) in scored.OrderByDescending(x => x.score).Take(5))
        {
            var o = JsonNode.Parse(c.ToJsonString())!.AsObject();
            o["score"] = Math.Round(Math.Min(score, 1.0), 2); o["why"] = why;
            res.Add(o);
        }
        return res;
    }

    public async Task<JsonNode?> SearchCustomers(string q)
    {
        q = q.Trim();
        var d = Digits(q);
        if (d.Length < 4 && q.Any(char.IsLetter)) return await SuggestCustomers(q);
        string filter = d.Length >= 4
            ? $"contains(bdm_primaryphone,'{d}')"
            : string.Join(" and ", q.Split(' ', StringSplitOptions.RemoveEmptyEntries).Select(w =>
                $"(contains(bdm_firstname,'{Esc(w)}') or contains(bdm_lastname,'{Esc(w)}'))"));
        if (string.IsNullOrEmpty(filter)) filter = "bdm_customerid ne null";
        return (await Send(HttpMethod.Get, $"bdm_customers?$select=bdm_customerid,bdm_name,bdm_accountnumber,bdm_firstname,bdm_lastname,bdm_primaryphone,bdm_email,bdm_street,bdm_city,bdm_state,bdm_zip&$filter={filter}&$top=15"))?["value"];
    }

    // Creates a customer account from the call (used when no existing account matches) and links it to the draft.
    public async Task CreateCustomerForDraft(string callId, string fullName, string? phone)
    {
        var d = await FindDraft(callId) ?? throw new KeyNotFoundException("draft not found");
        if (d["bdm_status"]?.GetValue<int>() != OptBase) throw new InvalidOperationException("draft already submitted");
        fullName = fullName.Trim();
        if (fullName.Length == 0) throw new InvalidOperationException("enter the customer's name first");
        var parts = fullName.Split(' ', 2, StringSplitOptions.RemoveEmptyEntries);
        var body = new JsonObject
        {
            ["bdm_name"] = fullName.ToUpperInvariant(),
            ["bdm_accountnumber"] = $"FW-{DateTime.UtcNow:yyMMddHHmmss}",
            ["bdm_firstname"] = parts[0],
            ["bdm_lastname"] = parts.Length > 1 ? parts[1] : "",
            ["bdm_primaryphone"] = phone ?? ""
        };
        var id = (await Send(HttpMethod.Post, "bdm_customers", body, wantId: true))!.ToString();
        await Send(HttpMethod.Patch, $"bdm_servicerequestdrafts({d["bdm_servicerequestdraftid"]})",
            new JsonObject { ["bdm_CustomerId@odata.bind"] = $"/bdm_customers({id})" });
    }

    public async Task<JsonNode?> GetCustomer(string id) =>
        await Send(HttpMethod.Get, $"bdm_customers({id})?$select=bdm_customerid,bdm_name,bdm_accountnumber,bdm_primaryphone,bdm_email,bdm_street,bdm_city,bdm_state,bdm_zip");

    public async Task<JsonNode?> ListEquipment(string? customerId)
    {
        var f = customerId is null ? "" : $"&$filter=_bdm_customerid_value eq {customerId}";
        return (await Send(HttpMethod.Get, $"bdm_equipments?$select=bdm_equipmentid,bdm_name,bdm_assetnumber,bdm_manufacturer,bdm_plant{f}&$top=50"))?["value"];
    }

    public async Task<JsonNode?> RecentDrafts() =>
        (await Send(HttpMethod.Get, "bdm_servicerequestdrafts?$select=bdm_callid,bdm_name,bdm_callerphone,bdm_customername,bdm_status,createdon&$orderby=createdon desc&$top=20"))?["value"];

    public async Task Update(string callId, JsonNode edit)
    {
        var d = await FindDraft(callId) ?? throw new KeyNotFoundException("draft not found");
        if (d["bdm_status"]?.GetValue<int>() != OptBase) throw new InvalidOperationException("draft already submitted");
        var b = new JsonObject();
        if (edit["customerId"] is JsonNode c) b["bdm_CustomerId@odata.bind"] = $"/bdm_customers({c})";
        if (edit["equipmentId"] is JsonNode e) b["bdm_EquipmentId@odata.bind"] = $"/bdm_equipments({e})";
        if (edit["complaint"] is JsonNode m) b["bdm_complaint"] = m.ToString();
        if (edit["priority"] is JsonNode p) b["bdm_priority"] = PriorityOpt(p.ToString());
        var edited = (d["bdm_editedfields"]?.ToString() ?? "").Split(',', StringSplitOptions.RemoveEmptyEntries).ToHashSet();
        foreach (var (key, col) in new[] { ("customername", "bdm_customername"), ("contactphone", "bdm_contactphone"), ("equipmenttext", "bdm_equipmenttext"),
                     ("assetnumber", "bdm_assetnumber"), ("location", "bdm_location"), ("preferredtime", "bdm_preferredtime"), ("summary", "bdm_summary") })
            if (edit[key] is JsonNode v) { b[col] = v.ToString(); edited.Add(key); }
        if (edit["complaint"] is not null) edited.Add("complaint");
        if (edit["priority"] is not null) edited.Add("priority");
        b["bdm_editedfields"] = string.Join(',', edited);
        if (b.Count > 0) await Send(HttpMethod.Patch, $"bdm_servicerequestdrafts({d["bdm_servicerequestdraftid"]})", b);
    }

    // The rep's Submit click: the only place a service ticket is created.
    public async Task<string> Submit(string callId, string submittedBy)
    {
        await _gate.WaitAsync();
        try
        {
            var d = await FindDraft(callId) ?? throw new KeyNotFoundException("draft not found");
            if (d["bdm_status"]?.GetValue<int>() != OptBase) throw new InvalidOperationException("draft already submitted");
            var custId = d["_bdm_customerid_value"]?.ToString();
            var eqId = d["_bdm_equipmentid_value"]?.ToString();
            if (custId is null || eqId is null) throw new InvalidOperationException("choose a customer and equipment first");
            var acct = (await GetCustomer(custId))?["bdm_accountnumber"]?.ToString();
            var title = $"FloorWorks-Voice-{acct}-{DateTime.UtcNow:yyyyMMddHHmm}";
            var desc = new StringBuilder()
                .AppendLine($"Call ID: {callId}").AppendLine($"Customer: {d["bdm_customername"]}")
                .AppendLine($"Contact phone: {d["bdm_contactphone"] ?? d["bdm_callerphone"]}").AppendLine($"Location: {d["bdm_location"]}")
                .AppendLine($"Preferred time: {d["bdm_preferredtime"]}")
                .AppendLine().AppendLine(d["bdm_complaint"]?.ToString())
                .AppendLine().AppendLine("Call summary:").AppendLine(d["bdm_summary"]?.ToString()).ToString();
            var ticket = new JsonObject
            {
                ["bdm_name"] = title, ["bdm_description"] = desc,
                ["bdm_priority"] = d["bdm_priority"]?.GetValue<int>() ?? OptBase + 1,
                ["bdm_status"] = OptBase, ["bdm_openedon"] = DateTime.UtcNow.ToString("o"),
                ["bdm_EquipmentId@odata.bind"] = $"/bdm_equipments({eqId})",
                ["bdm_CustomerId@odata.bind"] = $"/bdm_customers({custId})"
            };
            var ticketId = (await Send(HttpMethod.Post, "bdm_servicetickets", ticket, wantId: true))!.ToString();
            await Send(HttpMethod.Patch, $"bdm_servicerequestdrafts({d["bdm_servicerequestdraftid"]})", new JsonObject
            {
                ["bdm_status"] = OptBase + 1, ["bdm_submittedby"] = submittedBy, ["bdm_submittedon"] = DateTime.UtcNow.ToString("o"),
                ["bdm_TicketId@odata.bind"] = $"/bdm_servicetickets({ticketId})"
            });
            return title;
        }
        finally { _gate.Release(); }
    }
}
