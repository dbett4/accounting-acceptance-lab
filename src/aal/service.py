from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from .app import Lab


UI = """<!doctype html><html><head><meta charset=utf-8><meta name=viewport content='width=device-width'>
<title>Accounting Acceptance Lab</title><style>
:root{color-scheme:dark}body{font:15px system-ui;margin:0;background:#111827;color:#e5e7eb}header{padding:24px;background:#0f172a;border-bottom:1px solid #334155}main{padding:24px;max-width:1200px;margin:auto}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:14px}.card{background:#1f2937;border:1px solid #374151;border-radius:10px;padding:16px}.tag{font-size:12px;padding:3px 7px;border-radius:8px;background:#334155}pre{white-space:pre-wrap;color:#cbd5e1}.muted{color:#94a3b8}</style></head>
<body><header><h1>Accounting Acceptance Lab</h1><div class=muted>Recorded-model, synthetic, localhost-only operator console</div></header><main>
<h2>Cases</h2><div id=cases class=grid></div><template id=tpl><article class=card><h3></h3><span class=tag></span><p class=verdict></p><details><summary>Evidence & citations</summary><pre class=evidence></pre></details><details><summary>Proposal, risk & materiality</summary><pre class=proposal></pre></details><details><summary>Approval, readback & terminal verdict</summary><pre class=control></pre></details></article></template>
<script>fetch('/api/cases').then(r=>r.json()).then(cs=>cs.forEach(c=>{let n=tpl.content.cloneNode(true);let p=JSON.parse(c.payload);n.querySelector('h3').textContent=c.case_id+' · '+c.case_type;n.querySelector('.tag').textContent=c.state;n.querySelector('.verdict').textContent=p.verdict||'';n.querySelector('.evidence').textContent=JSON.stringify(p.citations||[],null,2);n.querySelector('.proposal').textContent=JSON.stringify(p.proposal||{},null,2);n.querySelector('.control').textContent=JSON.stringify({approval:c.approval,readback:p.readback,verdict:p.verdict},null,2);cases.appendChild(n)}))</script></main></body></html>"""


class LabServer(ThreadingHTTPServer):
    def server_close(self):
        self.lab.close()
        super().server_close()


def make_server(host: str, port: int, db_path: str | Path, artifact_dir: str | Path) -> LabServer:
    lab = Lab(db_path, artifact_dir)
    class Handler(BaseHTTPRequestHandler):
        def _json(self, value, status=200):
            body = json.dumps(value, sort_keys=True).encode()
            self.send_response(status); self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
        def do_GET(self):
            path = urlparse(self.path).path
            if path == "/health":
                if lab.store.health_probe():
                    return self._json({"status":"ok","service":"accounting-acceptance-lab","database":"reachable"})
                return self._json({"status":"error","service":"accounting-acceptance-lab","database":"unreachable"}, 503)
            if path == "/api/cases":
                rows = lab.store.cases()
                for row in rows: row["approval"] = lab.store.approval(row["case_id"])
                return self._json(rows)
            if path.startswith("/api/cases/"):
                try: return self._json(lab.inspect(path.rsplit("/", 1)[-1]))
                except KeyError: return self._json({"error":"not found"}, 404)
            if path == "/":
                body = UI.encode(); self.send_response(200); self.send_header("Content-Type","text/html; charset=utf-8"); self.send_header("Content-Length",str(len(body))); self.end_headers(); return self.wfile.write(body)
            self._json({"error":"not found"}, 404)
        def log_message(self, fmt, *args):
            return
    server = LabServer((host, port), Handler)
    server.lab = lab
    return server
