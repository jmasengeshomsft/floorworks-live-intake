public static class RepUi
{
    public const string Html = """
<!doctype html><html><head><meta charset="utf-8"><title>FloorWorks Rep Console</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
*{box-sizing:border-box}body{font-family:Segoe UI,Arial,sans-serif;margin:0;background:#f3f2f1;color:#242424}
header{background:#fff;padding:10px 20px;display:flex;align-items:center;gap:14px;border-bottom:1px solid #ddd}
header h1{font-size:16px;margin:0;flex:1}.chip{padding:3px 10px;border-radius:12px;font-size:12px;background:#e1dfdd}
.chip.live{background:#d1f0d8;color:#0b6a0b}.chip.sub{background:#cfe4fa;color:#0f548c}
main{display:grid;grid-template-columns:1fr 1fr;gap:16px;padding:16px}
.card{background:#fff;border-radius:6px;padding:14px;box-shadow:0 1px 3px #0002}
.card h2{font-size:14px;margin:0 0 8px}
#tr{height:300px;overflow:auto;border:1px solid #ddd;padding:8px;font-size:13px;background:#fafafa}
#tr div{margin-bottom:5px}.Customer{color:#0f548c}.Rep{color:#8a4b00}.Agent{color:#555;font-style:italic}
#coach{background:#fff8e1;border-left:4px solid #f2b705;padding:10px;font-size:13px;min-height:44px}
input,select,textarea{width:100%;padding:7px;border:1px solid #bbb;border-radius:4px;font:inherit}
textarea{height:110px}button{padding:8px 14px;border:0;border-radius:4px;background:#0f6cbd;color:#fff;cursor:pointer;font:inherit}
button.go{background:#107c10;font-weight:600}button:disabled{background:#bbb;cursor:default}
table{width:100%;border-collapse:collapse;font-size:13px}th,td{text-align:left;padding:5px;border-bottom:1px solid #eee}
tr.pick{cursor:pointer}tr.pick:hover,tr.sel{background:#e6f0fa}
.tabs button{background:#e1dfdd;color:#242424;margin-right:4px}.tabs button.on{background:#0f6cbd;color:#fff}
.kv{display:grid;grid-template-columns:130px 1fr;gap:4px;font-size:13px}.kv b{color:#666;font-weight:600}
.row{display:flex;gap:8px;margin:8px 0}.muted{color:#777;font-size:12px}
.form{display:grid;grid-template-columns:1fr 1fr;gap:10px}.form label{font-size:12px;color:#555;display:flex;flex-direction:column;gap:3px}.form .wide{grid-column:1/3}
.flash{background:#fff4c2;transition:background 1.5s}
.need{background:#fde7e9;border-left:4px solid #c50f1f;color:#8a0b16;padding:8px;font-size:13px;margin-top:6px;display:none}
.need.on{display:block}#msg.err{background:#fde7e9;border-left:4px solid #c50f1f;color:#8a0b16;padding:8px;font-size:13px;font-weight:600}
#msg.ok{background:#dff6dd;border-left:4px solid #107c10;color:#0b5a0b;padding:8px;font-size:13px;font-weight:600}
#aicard{border:1px solid #b9a6f0;border-left:5px solid #6b4fbb;background:#f6f2ff}#aicard h2{color:#4b2f9e}
.aitag{background:#6b4fbb;color:#fff;border-radius:10px;padding:1px 8px;font-size:11px;margin-left:6px}
#aicard ol{margin:4px 0 4px 18px;padding:0;font-size:13px}#aicard .src{font-size:11px;color:#5b4a8a;margin-top:6px}
#aicard button{background:#6b4fbb;margin-top:6px;margin-right:6px}
</style></head><body>
<header><h1>FloorWorks Rep Console &mdash; Teams Call Agent</h1>
<span class="tabs"><button id="t-call" class="on" onclick="showView('call')">Live call</button><button id="t-drafts" onclick="showView('drafts')">Drafts</button><button id="t-tickets" onclick="showView('tickets')">Service tickets</button></span>
<span id="st" class="chip">no call</span>
<select id="calls" style="width:260px"></select></header>
<div id="listview" style="display:none;padding:16px"><div class="card"><h2 id="lt"></h2><div id="lb"></div></div></div>
<main id="callview">
<section>
 <div class="card"><h2>Real-time Transcription</h2><div id="tr"></div></div>
 <div class="card" style="margin-top:16px"><h2>Coaching Window</h2><div id="coach">Waiting for the agent&hellip;</div></div>
</section>
<section>
 <div class="card"><h2>Service Intake Form <span class="muted" id="formnote">(filled live by the agents; edit any field)</span></h2>
  <div class="form">
   <label>Customer name<input data-f="customername" data-c="bdm_customername"></label>
   <label>Contact phone<input data-f="contactphone" data-c="bdm_contactphone"></label>
   <label>Equipment<input data-f="equipmenttext" data-c="bdm_equipmenttext"></label>
   <label>Asset number<input data-f="assetnumber" data-c="bdm_assetnumber"></label>
   <label>Location<input data-f="location" data-c="bdm_location"></label>
   <label>Preferred time<input data-f="preferredtime" data-c="bdm_preferredtime"></label>
   <label class="wide">Issue with equipment<textarea data-f="complaint" data-c="bdm_complaint"></textarea></label>
   <label>Priority<select id="prio" data-f="priority"><option>Low</option><option>Normal</option><option>High</option><option>Urgent</option></select></label>
   <label>Linked records<span id="links" class="muted"></span></label>
   <label class="wide">Call summary<textarea data-f="summary" data-c="bdm_summary" style="height:70px"></textarea></label>
  </div>
  <div class="row"><span>Work Request: <b id="wr">(created on submit)</b></span><span style="flex:1"></span>
   <button class="go" id="sub" onclick="submit()">Submit</button></div>
  <div id="need" class="need"></div>
  <div id="msg" class="muted"></div>
 </div>
 <div class="card" style="margin-top:16px"><h2>Customer</h2>
  <div class="row"><input id="q" placeholder="Search by customer's phone, first or last name"><button onclick="search()">Search</button></div>
  <div id="cust"></div></div>
 <div class="card" style="margin-top:16px"><h2>Equipment</h2><div id="p1"></div></div>
 <div class="card" id="aicard" style="margin-top:16px;display:none"><h2>AI suggested<span class="aitag">Foundry IQ</span></h2><div id="ai"></div></div>
</section></main>
<script>
const P=new URLSearchParams(location.search),K=P.get('k')||'';let call=P.get('call')||'',cur=null,eq=[],editing=false;
const H={'Content-Type':'application/json','x-rep-key':K};
const api=(p,o)=>fetch('/api/rep'+p,Object.assign({headers:H},o)).then(async r=>{if(!r.ok)throw new Error((await r.json().catch(()=>({}))).error||r.status);return r.status==204?null:r.json().catch(()=>null)});
const esc=s=>(s??'').toString().replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));
const PR={105730000:'Low',105730001:'Normal',105730002:'High',105730003:'Urgent'};
const F=()=>[...document.querySelectorAll('[data-f]')];
F().forEach(el=>{el.addEventListener('input',()=>{el.dataset.dirty='1';clearTimeout(el._t);el._t=setTimeout(()=>saveField(el),700)});el.addEventListener('blur',()=>saveField(el))});
async function saveField(el){if(!el.dataset.dirty||!call)return;delete el.dataset.dirty;try{await api('/draft/'+call,{method:'POST',body:JSON.stringify({[el.dataset.f]:el.value})})}catch(e){document.getElementById('msg').textContent=e.message}}
let aiCur=null;
function renderAi(d,sub){const card=document.getElementById('aicard'),box=document.getElementById('ai');
 const e=d.enrichmentEnabled?d.enrichment:null;aiCur=e&&Object.keys(e).length?e:null;
 if(!d.enrichmentEnabled||(!aiCur&&!d.enriching)){card.style.display='none';box.innerHTML='';return}
 card.style.display='';
 if(!aiCur){box.innerHTML='<div class="muted">Enriching&hellip; looking up manuals, bulletins and parts</div>';return}
 const e2=aiCur,li=a=>(a||[]).map(x=>`<li>${esc(x)}</li>`).join('');
 const parts=(e2.parts||[]).map(p=>`${esc(p.number)} ${esc(p.name)}${p.unit_cost!=null?' ($'+esc(p.unit_cost)+')':''}`).join('; ');
 box.innerHTML=(d.enriching?'<div class="muted">Updating&hellip;</div>':'')+
  `<div class="kv"><b>Likely cause</b><span>${esc(e2.likely_cause)}</span>`+
  (e2.bulletin?`<b>Bulletin</b><span>${esc(e2.bulletin)}</span>`:'')+
  (parts?`<b>Parts to check</b><span>${parts}</span>`:'')+
  (e2.suggested_priority?`<b>Suggested priority</b><span>${esc(e2.suggested_priority)}${e2.priority_reason?' &mdash; '+esc(e2.priority_reason):''}</span>`:'')+`</div>`+
  ((e2.diagnostic_steps||[]).length?`<div style="margin-top:6px;font-size:13px"><b>Diagnostic steps</b></div><ol>${li(e2.diagnostic_steps)}</ol>`:'')+
  (sub?'':'<button onclick="applyAi()">Apply priority &amp; parts</button><span class="muted">Copies into the form; you still press Submit.</span>')+
  ((e2.sources||[]).length?`<div class="src">Sources: ${(e2.sources||[]).map(s=>esc(String(s).replace(/\u3010[^\u3011]*\u3011/g,'').trim())).join(' &middot; ')}</div>`:'')}
function applyAi(){const e=aiCur;if(!e||!call)return;
 const pr=document.getElementById('prio'),want=(e.suggested_priority||'').toString();
 const opt=[...pr.options].find(o=>o.value.toLowerCase()===want.toLowerCase());
 if(opt&&pr.value!==opt.value){pr.value=opt.value;pr.dataset.dirty='1';saveField(pr)}
 const parts=(e.parts||[]).map(p=>p.number+' '+p.name).join(', ');
 const c=document.querySelector('[data-f=complaint]');
 if(parts&&!c.value.includes('Suggested parts to check')){c.value=(c.value?c.value+'\n':'')+'Suggested parts to check: '+parts;c.dataset.dirty='1';saveField(c)}}
async function loadCalls(){const c=await api('/calls');const s=document.getElementById('calls');
 const ids=[...new Set([...(c.live||[]),...(c.recent||[]).map(x=>x.bdm_callid)])];
 s.innerHTML=ids.map(i=>`<option value="${i}" ${i==call?'selected':''}>${i}${(c.live||[]).includes(i)?' (live)':''}</option>`).join('');
 if(!call&&ids.length){call=ids[0];}
 else if((c.live||[]).length&&!(c.live||[]).includes(call)&&!window._pinned){selectCall(c.live[0]);return}
 s.onchange=()=>{window._pinned=true;selectCall(s.value)}}
function selectCall(id){call=id;cur=null;eq=[];editing=false;
 const m=document.getElementById('msg');m.textContent='';m.className='muted';
 document.getElementById('q').value='';
 for(const el of F()){delete el.dataset.dirty;el.value=''}
 document.getElementById('cust').innerHTML='';document.getElementById('p1').innerHTML='';
 aiCur=null;document.getElementById('aicard').style.display='none';document.getElementById('ai').innerHTML='';
 document.getElementById('tr').innerHTML='';document.getElementById('wr').textContent='(created on submit)';
 document.getElementById('calls').value=id;showView('call');refresh()}
let view='call';
function showView(v){view=v;
 document.getElementById('callview').style.display=v==='call'?'':'none';
 document.getElementById('listview').style.display=v==='call'?'none':'';
 for(const k of['call','drafts','tickets'])document.getElementById('t-'+k).className=k===v?'on':'';
 if(v!=='call')loadList()}
const DS={105730000:'Draft',105730001:'Submitted'};
const fmt=s=>s?new Date(s).toLocaleString():'';
async function loadList(){const lb=document.getElementById('lb'),lt=document.getElementById('lt');
 try{
 if(view==='drafts'){const r=await api('/drafts')||[];lt.textContent='Drafts (call intakes, incl. after-hours) \u2014 click to open';
  lb.innerHTML='<table><tr><th>Received</th><th>Caller</th><th>Customer</th><th>Equipment</th><th>Issue</th><th>Priority</th><th>Status</th><th>Call</th></tr>'+r.map(x=>`<tr class="pick" onclick="selectCall('${esc(x.bdm_callid)}')"><td>${esc(fmt(x.createdon))}</td><td>${esc(x.bdm_callerphone)}</td><td>${esc(x.bdm_customername)}</td><td>${esc(x.bdm_equipmenttext)}</td><td>${esc((x.bdm_complaint||'').slice(0,80))}</td><td>${esc(PR[x.bdm_priority]||'')}</td><td>${esc(DS[x.bdm_status]||'')}${x.bdm_submittedby?' by '+esc(x.bdm_submittedby):''}</td><td>${esc((x.bdm_callid||'').slice(0,8))}</td></tr>`).join('')+'</table>'}
 else{const r=await api('/tickets')||[];lt.textContent='Service tickets';const fv=(x,k)=>x[k+'@OData.Community.Display.V1.FormattedValue']||'';
  lb.innerHTML='<table><tr><th>Ticket</th><th>Customer</th><th>Equipment</th><th>Priority</th><th>Status</th><th>Opened</th></tr>'+r.map(x=>`<tr><td>${esc(x.bdm_name)}</td><td>${esc(fv(x,'_bdm_customerid_value'))}</td><td>${esc(fv(x,'_bdm_equipmentid_value'))}</td><td>${esc(fv(x,'bdm_priority')||PR[x.bdm_priority])}</td><td>${esc(fv(x,'bdm_status'))}</td><td>${esc(fmt(x.bdm_openedon||x.createdon))}</td></tr>`).join('')+'</table>'}
 }catch(e){lb.textContent='Failed to load: '+e.message}}
async function refresh(){if(!call||editing)return;const my=call;
 let d;try{d=await api('/draft/'+call)}catch(e){return}
 if(my!==call)return;
 const dr=d.draft,sub=dr&&dr.bdm_status==105730001;cur=d;
 const st=document.getElementById('st');st.textContent=sub?'Submitted':d.live?'Live':'Call ended';st.className='chip '+(sub?'sub':d.live?'live':'');
 const lines=(d.transcript||(dr?.bdm_transcript||'').split('\n').filter(Boolean).map(l=>{const m=l.match(/^\[(.*?)\] (.*)$/);return m?{speaker:m[1],text:m[2]}:{speaker:'',text:l}}));
 const tr=document.getElementById('tr'),atEnd=tr.scrollTop+tr.clientHeight>=tr.scrollHeight-20;
 tr.innerHTML=lines.map(l=>`<div class="${esc(l.speaker)}"><b>[${esc(l.speaker)}]</b> ${esc(l.text)}</div>`).join('');if(atEnd)tr.scrollTop=tr.scrollHeight;
 document.getElementById('coach').textContent=dr?.bdm_coaching||'No prompts yet.';
 const cu=document.getElementById('cust');
 if(d.customer){const c=d.customer;cu.innerHTML=`<div class="kv"><b>Account Name</b><span>${esc(c.bdm_name)}</span><b>Account Number</b><span>${esc(c.bdm_accountnumber)}</span><b>Primary Phone</b><span>${esc(c.bdm_primaryphone)}</span><b>Email</b><span>${esc(c.bdm_email)}</span><b>Street</b><span>${esc(c.bdm_street)}</span><b>City / State / Zip</b><span>${esc(c.bdm_city)}, ${esc(c.bdm_state)} ${esc(c.bdm_zip)}</span></div>${sub?'':'<div class="muted" style="margin-top:6px">Search above to change customer.</div>'}`}
 else showCands(d.candidates||[]);
 if(d.customer){const id=d.customer.bdm_customerid;if(!eq.length||eq.cid!==id){eq=await api('/equipment?customerId='+id);if(!eq.length)eq=await api('/equipment');eq.cid=id}}
 else if(!eq.length||eq.cid!=='all'){eq=await api('/equipment');eq.cid='all'}
 if(my!==call)return;
 renderAi(d,sub);
 const sel=dr?._bdm_equipmentid_value;
 document.getElementById('p1').innerHTML=(dr?.bdm_equipmenttext?`<div class="muted">Heard: ${esc(dr.bdm_equipmenttext)}</div>`:'')+
  `<table><tr><th>Asset #</th><th>Equipment</th><th>Make</th></tr>`+eq.map(e=>`<tr class="pick ${e.bdm_equipmentid==sel?'sel':''}" onclick="pickEq('${e.bdm_equipmentid}')"><td>${esc(e.bdm_assetnumber)}</td><td>${esc(e.bdm_name)}</td><td>${esc(e.bdm_manufacturer)}</td></tr>`).join('')+`</table>`;
 for(const el of F()){if(document.activeElement===el||el.dataset.dirty)continue;
  const nv=el.id==='prio'?(PR[dr?.bdm_priority]||'Normal'):(dr?.[el.dataset.c]||'');
  if(el.value!==nv){el.value=nv;if(nv){el.classList.add('flash');setTimeout(()=>el.classList.remove('flash'),1500)}}
  el.disabled=!!sub||!dr}
 document.getElementById('links').textContent=(d.customer?'Customer: '+d.customer.bdm_name+'. ':'No customer linked. ')+(sel?'Equipment linked.':'No equipment linked.');
 document.getElementById('wr').textContent=sub?(dr.bdm_submittedby?'Submitted by '+dr.bdm_submittedby:'Submitted'):'(created on submit)';
 const need=[];const m0=document.getElementById('msg');if(!d.customer)need.push('a customer (search below, or create one from this call)');if(!sel)need.push('equipment (pick one below)');
 const nb=document.getElementById('need');nb.className='need'+(!sub&&dr&&need.length?' on':'');nb.textContent=need.length?'Before you can submit, link '+need.join(' and ')+'.':'';
 if(sub&&d.ticket){m0.className='ok';m0.textContent='Service ticket created: '+d.ticket}
 else if(!sub&&m0.className==='ok'){m0.className='muted';m0.textContent=''}
 document.getElementById('sub').disabled=!!sub||!dr||need.length>0;}
function showCands(c){const cu=document.getElementById('cust');
 const mk='<div class="row"><button onclick="createCust()">Create customer from this call</button></div><div class="muted">Uses the customer name and contact phone in the form.</div>';
 cu.innerHTML=(c.length?`<div class="muted">${c[0].why?'Suggested customers from the call &mdash; click Link':(c.length>1?'Multiple customers found':'Possible match')}</div><table><tr><th>Name</th><th>Account</th><th>Phone</th><th>Match</th><th></th></tr>`+c.map(x=>`<tr class="pick" onclick="pickCust('${x.bdm_customerid}')"><td>${esc(x.bdm_name)}</td><td>${esc(x.bdm_accountnumber)}</td><td>${esc(x.bdm_primaryphone)}</td><td>${x.why?esc(x.why)+' ('+Math.round(x.score*100)+'%)':''}</td><td><button>Link</button></td></tr>`).join('')+'</table>':'<div class="muted">No existing customer matched. Search above, or create one.</div>')+mk}
async function createCust(){const m=document.getElementById('msg');try{await save();const f=n=>document.querySelector('[data-f='+n+']').value;
 await api('/draft/'+call+'/customer',{method:'POST',body:JSON.stringify({name:f('customername'),phone:f('contactphone')||cur?.draft?.bdm_callerphone||''})});m.className='ok';m.textContent='Customer created and linked.';eq=[];refresh()}catch(e){m.className='err';m.textContent=e.message}}
async function search(){const q=document.getElementById('q').value;if(q)showCands(await api('/customers?q='+encodeURIComponent(q)))}
async function pickCust(id){eq=[];await api('/draft/'+call,{method:'POST',body:JSON.stringify({customerId:id})});refresh()}
async function pickEq(id){await api('/draft/'+call,{method:'POST',body:JSON.stringify({equipmentId:id})});refresh()}
async function save(){for(const el of F())await saveField(el)}
async function submit(){const m=document.getElementById('msg');m.textContent='';
 try{await save();const r=await api('/draft/'+call+'/submit',{method:'POST',body:JSON.stringify({submittedBy:P.get('rep')||'rep'})});m.className='ok';m.textContent='Service ticket created: '+r.ticket;refresh()}catch(e){m.className='err';m.textContent='Not submitted: '+e.message}}
loadCalls().then(refresh);setInterval(refresh,2000);setInterval(loadCalls,15000);
</script></body></html>
""";
}
