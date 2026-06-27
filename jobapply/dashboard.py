"""The review dashboard, served at GET /. Plain HTML + vanilla JS, no build step.
It polls /api/jobs and lets you preview each tailored PDF, then approve (opens the
apply page), mark applied, or deny."""

DASHBOARD_HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>Job Apply — Review</title>
<style>
  :root { --bg:#f4f5f7; --card:#fff; --ink:#20242c; --muted:#5b6472; --line:#e3e6ea;
          --accent:#2f62b0; --good:#1f9d57; --bad:#c0392b; }
  * { box-sizing:border-box; }
  body { margin:0; font:15px/1.5 -apple-system,Segoe UI,Roboto,sans-serif;
         background:var(--bg); color:var(--ink); }
  header { padding:18px 24px; background:var(--ink); color:#fff; display:flex;
           align-items:center; gap:16px; }
  header h1 { font-size:18px; margin:0; font-weight:600; }
  header .counts { margin-left:auto; font-size:13px; opacity:.85; }
  main { max-width:1100px; margin:0 auto; padding:24px; }
  .tabs { display:flex; gap:8px; margin-bottom:18px; flex-wrap:wrap; }
  .tab { padding:6px 14px; border:1px solid var(--line); border-radius:999px;
         background:#fff; cursor:pointer; font-size:13px; }
  .tab.active { background:var(--ink); color:#fff; border-color:var(--ink); }
  .card { background:var(--card); border:1px solid var(--line); border-radius:12px;
          padding:16px 18px; margin-bottom:14px; display:grid;
          grid-template-columns:64px 1fr auto; gap:16px; align-items:start; }
  .fit { width:64px; height:64px; border-radius:12px; display:flex;
         align-items:center; justify-content:center; font-weight:700; font-size:20px;
         color:#fff; background:var(--muted); }
  .fit.hi { background:var(--good); } .fit.mid { background:#d9a514; }
  .fit.lo { background:var(--bad); }
  .role { font-weight:600; font-size:16px; }
  .meta { color:var(--muted); font-size:13px; margin:2px 0 8px; }
  .reasons { font-size:13.5px; }
  .badge { display:inline-block; font-size:11px; text-transform:uppercase;
           letter-spacing:.04em; padding:2px 8px; border-radius:6px;
           background:#eef1f5; color:var(--muted); margin-left:8px; }
  .actions { display:flex; flex-direction:column; gap:8px; min-width:150px; }
  button, a.btn { font:inherit; font-size:13px; padding:8px 12px; border-radius:8px;
          border:1px solid var(--line); background:#fff; cursor:pointer;
          text-align:center; text-decoration:none; color:var(--ink); }
  .approve { background:var(--good); color:#fff; border-color:var(--good); }
  .deny { background:#fff; color:var(--bad); border-color:var(--bad); }
  .applied { background:var(--accent); color:#fff; border-color:var(--accent); }
  .err { color:var(--bad); font-size:12.5px; white-space:pre-wrap; }
  .empty { color:var(--muted); text-align:center; padding:60px 0; }
</style>
</head>
<body>
<header>
  <h1>Job Apply — Review</h1>
  <span class="counts" id="counts"></span>
</header>
<main>
  <div class="tabs" id="tabs"></div>
  <div id="list"></div>
</main>
<script>
const STATUS_LABELS = {
  pending_review:"Pending review", new:"New", processing:"Processing",
  approved:"Approved", applied:"Applied", denied:"Denied", error:"Error"
};
const ORDER = ["pending_review","approved","new","processing","applied","denied","error"];
let JOBS = [], FILTER = "pending_review";

function fitClass(s){ if(s==null) return ""; if(s>=75) return "hi"; if(s>=50) return "mid"; return "lo"; }

async function post(path){ const r = await fetch(path,{method:"POST"}); return r.json(); }

async function refresh(){
  JOBS = await (await fetch("/api/jobs")).json();
  renderTabs(); renderList();
}

function renderTabs(){
  const counts = {};
  JOBS.forEach(j => counts[j.status] = (counts[j.status]||0)+1);
  document.getElementById("counts").textContent =
    JOBS.length + " jobs · " + (counts["pending_review"]||0) + " awaiting review";
  const tabs = document.getElementById("tabs");
  tabs.innerHTML = "";
  ORDER.forEach(st => {
    const n = counts[st]||0;
    const el = document.createElement("div");
    el.className = "tab" + (st===FILTER ? " active":"");
    el.textContent = STATUS_LABELS[st] + " (" + n + ")";
    el.onclick = () => { FILTER = st; renderTabs(); renderList(); };
    tabs.appendChild(el);
  });
}

function renderList(){
  const list = document.getElementById("list");
  const rows = JOBS.filter(j => j.status===FILTER);
  if(!rows.length){ list.innerHTML = '<div class="empty">Nothing here.</div>'; return; }
  list.innerHTML = "";
  rows.forEach(j => list.appendChild(card(j)));
}

function card(j){
  const el = document.createElement("div");
  el.className = "card";
  const fit = j.fit_score==null ? "—" : j.fit_score;
  el.innerHTML = `
    <div class="fit ${fitClass(j.fit_score)}">${fit}</div>
    <div>
      <div class="role">${esc(j.title)||"(no title)"} <span class="badge">${STATUS_LABELS[j.status]}</span></div>
      <div class="meta">${esc(j.company)} · ${esc(j.location)||"—"}</div>
      <div class="reasons">${esc(j.fit_reasons)||""}</div>
      ${j.error ? `<div class="err">${esc(j.error.split("\n")[0])}</div>` : ""}
    </div>
    <div class="actions" data-id="${j.id}"></div>`;
  const a = el.querySelector(".actions");
  if(j.pdf_path) a.appendChild(link("Preview résumé","/pdf/"+j.id));
  if(j.url) a.appendChild(link("View posting", j.url, true));
  if(j.status==="pending_review"){
    a.appendChild(btn("Approve & open","approve",async()=>{
      const r = await post("/approve/"+j.id);
      if(r.apply_url) window.open(r.apply_url,"_blank");
      refresh();
    }));
    a.appendChild(btn("Deny","deny",async()=>{ await post("/deny/"+j.id); refresh(); }));
  }
  if(j.status==="approved"){
    a.appendChild(btn("Mark applied","applied",async()=>{ await post("/applied/"+j.id); refresh(); }));
    a.appendChild(btn("Deny","deny",async()=>{ await post("/deny/"+j.id); refresh(); }));
  }
  if(j.status==="error" || j.status==="new"){
    a.appendChild(btn("Process","",async()=>{ await post("/process/"+j.id); refresh(); }));
  }
  return el;
}

function btn(label,cls,fn){ const b=document.createElement("button");
  b.textContent=label; if(cls)b.className=cls; b.onclick=fn; return b; }
function link(label,href,blank){ const a=document.createElement("a");
  a.className="btn"; a.textContent=label; a.href=href;
  a.target = blank ? "_blank" : "_blank"; return a; }
function esc(s){ return (s==null?"":String(s)).replace(/[&<>"]/g,
  c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c])); }

refresh();
setInterval(refresh, 4000);
</script>
</body>
</html>"""
