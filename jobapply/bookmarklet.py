"""The LinkedIn capture bookmarklet + an install page served at /bookmarklet.

You drag the button to your bookmarks bar once. Then, on any LinkedIn job page,
click it: it scrapes the title/company/location/JD from the page you're already
logged into and POSTs them to the local pipeline. No separate scraping, no creds.
"""

# Readable source of the bookmarklet. `__ENDPOINT__` is replaced with the real URL.
_SRC = r"""
(function(){
  function pick(sels){
    for(var i=0;i<sels.length;i++){
      var el=document.querySelector(sels[i]);
      if(el && (el.innerText||'').trim()) return el.innerText.trim();
    }
    return '';
  }
  var title = pick([
    '.job-details-jobs-unified-top-card__job-title',
    '.jobs-unified-top-card__job-title',
    'h1.t-24', 'h1'
  ]);
  var company = pick([
    '.job-details-jobs-unified-top-card__company-name a',
    '.job-details-jobs-unified-top-card__company-name',
    '.jobs-unified-top-card__company-name'
  ]);
  var location = pick([
    '.job-details-jobs-unified-top-card__primary-description-container .tvm__text',
    '.jobs-unified-top-card__bullet',
    '.job-details-jobs-unified-top-card__primary-description-container'
  ]);
  var desc = pick([
    '#job-details', '.jobs-description__content', '.jobs-description-content__text',
    '.jobs-box__html-content'
  ]);
  if(!desc){ alert('Could not find the job description on this page. Open a LinkedIn job posting first.'); return; }
  var url = location && location.indexOf('http')===0 ? location : window.location.href.split('?')[0];
  fetch('__ENDPOINT__',{
    method:'POST',
    headers:{'Content-Type':'application/json'},
    body:JSON.stringify({title:title,company:company,location:location,url:window.location.href.split('?')[0],source:'linkedin',description:desc})
  }).then(function(r){return r.json();}).then(function(j){
    alert(j.status==='duplicate' ? 'Already captured: '+title : 'Captured ✓  '+title+(j.processing?' (tailoring…)':''));
  }).catch(function(e){ alert('Capture failed — is the pipeline server running?\n'+e); });
})();
"""


# Harvest bookmarklet: run on a LinkedIn jobs SEARCH page. Auto-scrolls the result
# list, clicks each card, reads the detail pane, and POSTs them all in one batch.
# Runs in YOUR real logged-in tab (no separate browser/account). No `//` comments
# here — the minifier joins lines, which would swallow the rest of the script.
_HARVEST_SRC = r"""
(async function(){
  function sleep(ms){ return new Promise(function(r){ setTimeout(r,ms); }); }
  function txt(d,sel){ var e=d.querySelector(sel); return e?(e.textContent||'').trim():''; }
  function clean(d,sel){
    var e=d.querySelector(sel); if(!e) return '';
    var html=(e.innerHTML||'').replace(/<\s*(br|\/p|\/div|\/li|\/h[1-6])\s*\/?>/gi,'\n').replace(/<li[^>]*>/gi,'• ');
    var t=html.replace(/<[^>]+>/g,'');
    var ta=document.createElement('textarea'); ta.innerHTML=t; t=ta.value;
    return t.replace(/[ \t]+/g,' ').replace(/\n{3,}/g,'\n\n').trim();
  }
  var MAX=25, ids=[], seen={};
  for(var s=0;s<12;s++){
    var as=document.querySelectorAll("a[href*='/jobs/view/']");
    for(var k=0;k<as.length;k++){
      var m=(as[k].getAttribute('href')||'').match(/\/jobs\/view\/(\d+)/);
      if(m && !seen[m[1]]){ seen[m[1]]=1; ids.push(m[1]); }
    }
    if(ids.length>=MAX) break;
    if(as.length) as[as.length-1].scrollIntoView({block:'end'});
    window.scrollBy(0,1600);
    await sleep(800);
  }
  ids=ids.slice(0,MAX);
  if(!ids.length){ alert('No job results found. Open a LinkedIn Jobs SEARCH page first, then click this.'); return; }
  var box=document.createElement('div');
  box.style.cssText='position:fixed;top:12px;right:12px;z-index:99999;background:#20242c;color:#fff;padding:10px 14px;border-radius:8px;font:13px sans-serif;box-shadow:0 2px 8px rgba(0,0,0,.3)';
  document.body.appendChild(box);
  var jobs=[];
  for(var i=0;i<ids.length;i++){
    box.textContent='Gathering '+(i+1)+'/'+ids.length+' ('+jobs.length+' ok)';
    try{
      var resp=await fetch('https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/'+ids[i],{credentials:'include'});
      var d=new DOMParser().parseFromString(await resp.text(),'text/html');
      var title=txt(d,'.top-card-layout__title')||txt(d,'h2');
      var company=txt(d,'.topcard__org-name-link')||txt(d,'.topcard__flavor--metadata');
      var loc=txt(d,'.topcard__flavor--bullet');
      var desc=clean(d,'.show-more-less-html__markup')||clean(d,'.description__text');
      if(desc) jobs.push({title:title,company:company,location:loc,url:'https://www.linkedin.com/jobs/view/'+ids[i]+'/',source:'linkedin',description:desc});
    }catch(e){}
    await sleep(500);
  }
  box.textContent='Sending '+jobs.length+' jobs to the pipeline...';
  try{
    var r=await fetch('__ENDPOINT__',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({jobs:jobs})});
    var j=await r.json();
    box.textContent='Done - captured '+(j.captured||0)+' new of '+jobs.length+(j.processing?'. Tailoring...':'');
  }catch(e){ box.textContent='Send failed - is the pipeline server (python run.py serve) running?'; }
  await sleep(6000);
  box.remove();
})();
"""


def _minify(src: str) -> str:
    # Naive whitespace collapse — fine for a bookmarklet (no string literals span lines).
    lines = [ln.strip() for ln in src.strip().splitlines()]
    return "".join(lines)


def build_bookmarklet(endpoint: str) -> str:
    body = _minify(_SRC).replace("__ENDPOINT__", endpoint)
    return "javascript:" + body


def build_harvest_bookmarklet(endpoint: str) -> str:
    body = _minify(_HARVEST_SRC).replace("__ENDPOINT__", endpoint)
    return "javascript:" + body


def install_page(base_url: str) -> str:
    base = base_url.rstrip("/")
    one = build_bookmarklet(base + "/capture").replace('"', "&quot;")
    harvest = build_harvest_bookmarklet(base + "/capture-batch").replace('"', "&quot;")
    return f"""<!doctype html><html><head><meta charset="utf-8"/>
<title>Install the LinkedIn capture bookmarklets</title>
<style>body{{font:16px/1.6 -apple-system,Segoe UI,Roboto,sans-serif;max-width:720px;
margin:40px auto;padding:0 20px;color:#20242c}}
.book{{display:inline-block;background:#2f62b0;color:#fff;padding:10px 18px;
border-radius:8px;text-decoration:none;font-weight:600;margin:4px 0}}
.harvest{{background:#1f9d57}}
code{{background:#f0f2f5;padding:2px 6px;border-radius:4px}}
ol{{padding-left:20px}} li{{margin:8px 0}} .note{{color:#5b6472;font-size:14px}}</style></head><body>
<h1>LinkedIn capture bookmarklets</h1>
<p>Show your bookmarks bar (<code>Ctrl+Shift+B</code>) and drag <b>both</b> buttons onto it.
Keep the pipeline server running while you use them.</p>

<h3>🧲 Harvest search page <span class="note">(the autonomous one)</span></h3>
<p>Drag to bookmarks:&nbsp; <a class="book harvest" href="{harvest}">🧲 Harvest jobs</a></p>
<ol>
<li>Open a LinkedIn <b>Jobs search</b> page with your filters applied.</li>
<li>Click the bookmark. It auto-scrolls, opens each job, and gathers up to 25 — you'll
see a progress box top-right.</li>
<li>All of them get tailored in one batch and appear in your
<a href="{base_url}">review queue</a>.</li>
</ol>

<h3>📌 Capture this job <span class="note">(single posting)</span></h3>
<p>Drag to bookmarks:&nbsp; <a class="book" href="{one}">📌 Capture job</a></p>
<p class="note">On a single job posting, grabs just that one.</p>

<p class="note">⚠ The harvest runs in your real session with human-like pauses. Use it
occasionally and at modest volume — don't blast it repeatedly, so LinkedIn doesn't
flag your account.</p>
</body></html>"""
