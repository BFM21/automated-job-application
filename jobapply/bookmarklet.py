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


def _minify(src: str) -> str:
    # Naive whitespace collapse — fine for a bookmarklet (no string literals span lines).
    lines = [ln.strip() for ln in src.strip().splitlines()]
    return "".join(lines)


def build_bookmarklet(endpoint: str) -> str:
    body = _minify(_SRC).replace("__ENDPOINT__", endpoint)
    return "javascript:" + body


def install_page(base_url: str) -> str:
    endpoint = base_url.rstrip("/") + "/capture"
    href = build_bookmarklet(endpoint)
    # Escape double quotes so it sits safely inside the href attribute.
    href_attr = href.replace('"', "&quot;")
    return f"""<!doctype html><html><head><meta charset="utf-8"/>
<title>Install the LinkedIn capture bookmarklet</title>
<style>body{{font:16px/1.6 -apple-system,Segoe UI,Roboto,sans-serif;max-width:680px;
margin:40px auto;padding:0 20px;color:#20242c}}
.book{{display:inline-block;background:#2f62b0;color:#fff;padding:10px 18px;
border-radius:8px;text-decoration:none;font-weight:600}}
code{{background:#f0f2f5;padding:2px 6px;border-radius:4px}}
ol{{padding-left:20px}} li{{margin:8px 0}}</style></head><body>
<h1>Capture LinkedIn jobs</h1>
<ol>
<li>Show your browser's bookmarks bar (<code>Ctrl+Shift+B</code>).</li>
<li>Drag this button onto it:&nbsp; <a class="book" href="{href_attr}">📌 Capture job</a></li>
<li>Open any LinkedIn job posting, then click the bookmark.</li>
<li>The job is tailored and shows up in your <a href="{base_url}">review dashboard</a>.</li>
</ol>
<p>Keep the pipeline server running while you capture jobs.</p>
</body></html>"""
