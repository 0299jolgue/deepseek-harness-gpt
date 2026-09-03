import json
import os
import urllib.request
import urllib.error
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = Path(__file__).parent
DATA = ROOT / "data.json"
HOST = "0.0.0.0"
PORT = int(os.getenv("PORT", "80"))

INDEX = """<!doctype html>
<html lang=\"en\"><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width,initial-scale=1\"><title>Jolgue AI</title>
<style>body{margin:0;font:15px system-ui;background:#0b0d12;color:#eee}main{display:grid;grid-template-columns:230px 1fr;min-height:100vh}.side{background:#11141b;padding:16px}.side button,.side input,.side textarea{width:100%;box-sizing:border-box;margin:5px 0;padding:9px;border:1px solid #2a2f3a;border-radius:8px;background:#181c25;color:#eee}.chat{display:flex;flex-direction:column}.top{padding:14px 18px;border-bottom:1px solid #242936;display:flex;justify-content:space-between}.msgs{flex:1;overflow:auto;padding:24px;max-width:900px;width:100%;box-sizing:border-box;margin:auto}.m{padding:12px 14px;border-radius:12px;margin:10px 0;white-space:pre-wrap}.u{background:#1f2937}.a{background:#151922}.composer{display:flex;gap:8px;padding:14px;max-width:900px;width:100%;box-sizing:border-box;margin:auto}.composer textarea{flex:1;min-height:55px;background:#12161e;color:#fff;border:1px solid #2a2f3a;border-radius:10px;padding:12px}.composer button{width:90px;border:0;border-radius:10px;background:#fff;color:#111;font-weight:700}.muted{color:#8b93a3;font-size:12px}</style></head>
<body><main><aside class=side><h3>Jolgue AI</h3><button onclick=state.newChat()>+ New chat</button><div id=chats></div><hr><div class=muted>Provider</div><input id=base placeholder=\"Base URL\"><input id=model placeholder=\"Model\"><input id=key placeholder=\"API key\" type=password><button onclick=saveCfg()>Save provider</button><hr><div class=muted>Skill</div><textarea id=skillname placeholder=\"Skill name\"></textarea><textarea id=skillbody placeholder=\"Instructions\"></textarea><button onclick=createSkill()>Create skill</button></aside>
<section class=chat><div class=top><b id=title>New chat</b><span class=muted>OpenAI-compatible API</span></div><div id=msgs class=msgs></div><div class=composer><textarea id=input placeholder=\"Message...\"></textarea><button onclick=send()>Send</button></div></section></main>
<script>
const state={data:null,chat:null,async load(){this.data=await (await fetch('/api/state')).json();this.chat=this.data.chats[0]||this.newChat(true);render();},newChat(silent=false){const c={id:crypto.randomUUID(),title:'New chat',messages:[]};this.data.chats.unshift(c);this.chat=c;if(!silent)save();render();return c;}};
async function save(){await fetch('/api/state',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify(state.data)})}
function render(){document.getElementById('msgs').innerHTML=(state.chat?.messages||[]).map(m=>`<div class=\"m ${m.role==='user'?'u':'a'}\"><b>${m.role==='user'?'You':'AI'}</b><br>${esc(m.content)}</div>`).join('');document.getElementById('title').textContent=state.chat?.title||'New chat';document.getElementById('chats').innerHTML=state.data.chats.map(c=>`<button onclick=pick('${c.id}')>${esc(c.title||'Chat')}</button>`).join('');document.getElementById('base').value=state.data.provider.base_url;document.getElementById('model').value=state.data.provider.model;}
function esc(s){return s.replace(/[&<>\"']/g,x=>({'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;',"'":'&#39;'}[x]))}
function pick(id){state.chat=state.data.chats.find(x=>x.id===id);render()}
async function send(){const i=document.getElementById('input');const text=i.value.trim();if(!text)return;i.value='';state.chat.messages.push({role:'user',content:text});state.chat.title=state.chat.messages[0]?.content.slice(0,40)||'Chat';render();const r=await fetch('/api/chat',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({messages:state.chat.messages,provider:state.data.provider,skills:state.data.skills})});const j=await r.json();state.chat.messages.push({role:'assistant',content:j.content||j.error||'No response'});await save();render();}
function saveCfg(){state.data.provider.base_url=document.getElementById('base').value.trim();state.data.provider.model=document.getElementById('model').value.trim();state.data.provider.api_key=document.getElementById('key').value.trim();save()}
async function createSkill(){const n=document.getElementById('skillname').value.trim();const b=document.getElementById('skillbody').value.trim();if(!n||!b)return;state.data.skills.push({name:n,instructions:b});await save();alert('Skill saved');}
document.getElementById('input').addEventListener('keydown',e=>{if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();send()}});state.load();
</script></body></html>"""

def defaults():
    return {"provider":{"base_url":os.getenv("NVIDIA_BASE_URL","https://integrate.api.nvidia.com/v1"),"model":os.getenv("NVIDIA_MODEL","meta/llama-3.1-8b-instruct"),"api_key":os.getenv("NVIDIA_API_KEY","")},"chats":[],"skills":[]}

def load():
    if not DATA.exists():
        d=defaults(); DATA.write_text(json.dumps(d,indent=2)); return d
    try:return json.loads(DATA.read_text())
    except Exception:return defaults()

def call_llm(payload):
    p=payload.get("provider") or {}
    base=(p.get("base_url") or "").rstrip("/")
    model=p.get("model") or "meta/llama-3.1-8b-instruct"
    key=p.get("api_key") or os.getenv("NVIDIA_API_KEY","")
    if not base or not key: raise RuntimeError("Configure an OpenAI-compatible Base URL and API key.")
    system="You are Jolgue AI, a helpful coding and research agent.\n"
    for s in payload.get("skills",[]): system += f"\nSkill: {s.get('name')}\n{s.get('instructions')}\n"
    messages=[{"role":"system","content":system}]+payload.get("messages",[])
    body=json.dumps({"model":model,"messages":messages,"temperature":0.2,"stream":False}).encode()
    req=urllib.request.Request(base+"/chat/completions",data=body,headers={"Content-Type":"application/json","Authorization":"Bearer "+key},method="POST")
    with urllib.request.urlopen(req,timeout=180) as r:return json.loads(r.read())['choices'][0]['message']['content']

class H(BaseHTTPRequestHandler):
    def send_json(self,obj,status=200):
        b=json.dumps(obj).encode();self.send_response(status);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(b)));self.end_headers();self.wfile.write(b)
    def do_GET(self):
        if self.path=='/':
            b=INDEX.encode();self.send_response(200);self.send_header('Content-Type','text/html; charset=utf-8');self.send_header('Content-Length',str(len(b)));self.end_headers();self.wfile.write(b)
        elif self.path=='/api/state': self.send_json(load())
        else:self.send_json({'error':'Not found'},404)
    def do_POST(self):
        n=int(self.headers.get('Content-Length','0')); raw=self.rfile.read(n)
        try: data=json.loads(raw or b'{}')
        except Exception:self.send_json({'error':'Invalid JSON'},400);return
        if self.path=='/api/state': DATA.write_text(json.dumps(data,indent=2)); self.send_json({'ok':True}); return
        if self.path=='/api/chat':
            try:self.send_json({'content':call_llm(data)})
            except urllib.error.HTTPError as e:
                msg=e.read().decode(errors='ignore');self.send_json({'error':f'Provider HTTP {e.code}: {msg}'},502)
            except Exception as e:self.send_json({'error':str(e)},500)
            return
        self.send_json({'error':'Not found'},404)

if __name__=='__main__':
    load(); print(f'Jolgue AI listening on {HOST}:{PORT}'); ThreadingHTTPServer((HOST,PORT),H).serve_forever()
