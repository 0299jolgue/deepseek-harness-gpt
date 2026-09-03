import json
import os
import re
import urllib.request
import urllib.error
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = Path(__file__).parent
DATA = ROOT / "data.json"
TEMPLATES = ROOT / "templates"
HOST = "0.0.0.0"
PORT = int(os.getenv("PORT", "80"))

BASE_HTML = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Jolgue AI</title>
<style>
*{box-sizing:border-box}body{margin:0;font:14px system-ui;background:#090b0f;color:#eef0f4}button,input,textarea{font:inherit}button{cursor:pointer}.app{display:grid;grid-template-columns:250px 1fr;min-height:100vh}.sidebar{background:#10131a;border-right:1px solid #222733;padding:16px;overflow:auto}.brand{font-size:20px;font-weight:800;margin-bottom:18px}.nav button,.template,.chat-item{display:block;width:100%;text-align:left;border:1px solid transparent;background:transparent;color:#bbc2cf;padding:10px 11px;border-radius:9px;margin:3px 0}.nav button:hover,.template:hover,.chat-item:hover{background:#181c25;color:#fff}.template.active,.chat-item.active{background:#1b202a;border-color:#303746;color:#fff}.section{margin-top:20px}.label{font-size:11px;text-transform:uppercase;letter-spacing:.08em;color:#737c8c;margin:12px 2px 6px}.main{display:flex;flex-direction:column;min-width:0}.top{height:60px;border-bottom:1px solid #222733;padding:0 22px;display:flex;align-items:center;justify-content:space-between}.title{font-weight:750}.sub{font-size:12px;color:#7f8796}.content{display:grid;grid-template-columns:1fr 300px;min-height:calc(100vh - 60px)}.chat{display:flex;flex-direction:column;min-width:0}.messages{flex:1;overflow:auto;padding:28px;max-width:960px;width:100%;margin:auto}.welcome{padding:50px 10px;text-align:center;color:#798292}.msg{max-width:820px;padding:13px 15px;border-radius:13px;margin:12px auto;white-space:pre-wrap;line-height:1.55}.user{background:#202633}.assistant{background:#151922}.composer{display:flex;gap:9px;padding:16px;max-width:960px;width:100%;margin:auto}.composer textarea{flex:1;resize:none;min-height:56px;max-height:180px;background:#11151d;color:#fff;border:1px solid #303644;border-radius:12px;padding:13px}.send{width:82px;border:0;border-radius:12px;background:#fff;color:#0a0b0e;font-weight:800}.panel{border-left:1px solid #222733;padding:18px;background:#0d1015;overflow:auto}.card{background:#11151c;border:1px solid #252b37;border-radius:13px;padding:14px;margin-bottom:12px}.card h3{margin:0 0 7px;font-size:14px}.card p{margin:0;color:#858e9e;font-size:12px;line-height:1.5}.field{width:100%;margin:6px 0;padding:9px 10px;background:#0f131a;color:#fff;border:1px solid #2a303c;border-radius:9px}.small{font-size:11px;color:#727b8b}.row{display:flex;gap:7px}.row>*{flex:1}.primary{background:#fff;color:#0a0b0e;border:0;border-radius:9px;padding:9px 11px;font-weight:700}.danger{background:#1a1114;color:#ff9a9a;border:1px solid #47252a;border-radius:9px;padding:8px 10px}.template-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px}.template-title{font-weight:700}.template-desc{font-size:12px;color:#7f8796;margin-top:2px}@media(max-width:900px){.content{grid-template-columns:1fr}.panel{display:none}}@media(max-width:650px){.app{grid-template-columns:1fr}.sidebar{display:none}.template-grid{grid-template-columns:1fr}}
</style></head><body><div class="app"><aside class="sidebar"><div class="brand">Jolgue AI</div><div class="nav"><button onclick="newProject()">＋ New project</button><button onclick="newChat()">＋ New chat</button></div><div class="section"><div class="label">Templates</div><div id="templates"></div></div><div class="section"><div class="label">Chats</div><div id="chats"></div></div><div class="section"><div class="label">Skills</div><div id="skills"></div></div></aside><main class="main"><header class="top"><div><div class="title" id="title">Select a template</div><div class="sub" id="subtitle">Jolgue AI workspace</div></div><div class="sub" id="projectLabel"></div></header><div class="content"><section class="chat"><div id="messages" class="messages"><div class="welcome"><h2>Choose a template</h2><p>Templates are stored as separate HTML files in <b>templates/</b>.</p></div></div><div class="composer"><textarea id="input" placeholder="Message..."></textarea><button class="send" onclick="send()">Send</button></div></section><aside class="panel"><div class="card"><h3>Provider</h3><input class="field" id="base" placeholder="Base URL"><input class="field" id="model" placeholder="Model"><input class="field" id="key" placeholder="API key" type="password"><button class="primary" onclick="saveProvider()">Save</button></div><div class="card"><h3>New skill</h3><input class="field" id="skillName" placeholder="Skill name"><textarea class="field" id="skillBody" placeholder="Instructions"></textarea><button class="primary" onclick="createSkill()">Create skill</button></div><div class="card"><h3>Current template</h3><div id="currentTemplate" class="small">None</div></div></aside></div></main></div>
<script>
const S={data:null,template:null,chat:null};
async function api(path,method='GET',body){const r=await fetch(path,{method,headers:{'content-type':'application/json'},body:body?JSON.stringify(body):undefined});return r.json()}
async function load(){S.data=await api('/api/state');render();}
function esc(s=''){return String(s).replace(/[&<>\"']/g,x=>({'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;',"'":'&#39;'}[x]))}
function render(){const d=S.data||{};document.getElementById('base').value=d.provider?.base_url||'';document.getElementById('model').value=d.provider?.model||'';document.getElementById('templates').innerHTML=(d.templates||[]).map(t=>`<button class="template ${S.template?.id===t.id?'active':''}" onclick="pickTemplate('${esc(t.id)}')"><div class="template-title">${esc(t.name)}</div><div class="template-desc">${esc(t.description)}</div></button>`).join('');document.getElementById('chats').innerHTML=(d.chats||[]).map(c=>`<button class="chat-item ${S.chat?.id===c.id?'active':''}" onclick="pickChat('${esc(c.id)}')">${esc(c.title||'New chat')}</button>`).join('');document.getElementById('skills').innerHTML=(d.skills||[]).map(s=>`<div class="small" style="padding:5px">• ${esc(s.name)}</div>`).join('');document.getElementById('title').textContent=S.chat?.title||S.template?.name||'Select a template';document.getElementById('subtitle').textContent=S.template?S.template.description:'Jolgue AI workspace';document.getElementById('projectLabel').textContent=S.chat?.project||'';document.getElementById('currentTemplate').textContent=S.template?S.template.name:'None';const msgs=S.chat?.messages||[];document.getElementById('messages').innerHTML=msgs.length?msgs.map(m=>`<div class="msg ${m.role==='user'?'user':'assistant'}"><b>${m.role==='user'?'You':'AI'}</b><br>${esc(m.content)}</div>`).join(''):`<div class="welcome"><h2>${esc(S.template?.name||'Choose a template')}</h2><p>${esc(S.template?.description||'Pick a template from the sidebar.')}</p></div>`;}
function pickTemplate(id){S.template=S.data.templates.find(t=>t.id===id);S.chat=null;render()}
function pickChat(id){S.chat=S.data.chats.find(c=>c.id===id);S.template=S.data.templates.find(t=>t.id===S.chat?.template_id)||null;render()}
async function newChat(){if(!S.template){pickTemplate(S.data.templates[0]?.id);if(!S.template)return}const c={id:crypto.randomUUID(),title:'New chat',project:'Default',template_id:S.template.id,messages:[]};S.data.chats.unshift(c);S.chat=c;await save();render()}
async function newProject(){alert('Projects are attached to chats in this lightweight build. Create a new chat and set its project in a later update.')}
async function save(){await api('/api/state','POST',S.data)}
async function send(){if(!S.template){alert('Choose a template first.');return}if(!S.chat)await newChat();const i=document.getElementById('input'),text=i.value.trim();if(!text)return;i.value='';S.chat.messages.push({role:'user',content:text});if(S.chat.title==='New chat')S.chat.title=text.slice(0,40)||'Chat';render();const r=await api('/api/chat','POST',{messages:S.chat.messages,provider:S.data.provider,template:S.template,skills:S.data.skills});S.chat.messages.push({role:'assistant',content:r.content||r.error||'No response'});await save();render()}
function saveProvider(){S.data.provider.base_url=document.getElementById('base').value.trim();S.data.provider.model=document.getElementById('model').value.trim();S.data.provider.api_key=document.getElementById('key').value.trim();save()}
async function createSkill(){const n=document.getElementById('skillName').value.trim(),b=document.getElementById('skillBody').value.trim();if(!n||!b)return;S.data.skills.push({id:crypto.randomUUID(),name:n,instructions:b});await save();document.getElementById('skillName').value='';document.getElementById('skillBody').value='';render()}
document.getElementById('input').addEventListener('keydown',e=>{if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();send()}});load();
</script></body></html>"""

def read_templates():
    result=[]
    TEMPLATES.mkdir(exist_ok=True)
    for f in sorted(TEMPLATES.glob('*.html')):
        if f.name.startswith('_'): continue
        text=f.read_text(encoding='utf-8')
        def attr(name, default=''):
            m=re.search(r'data-'+re.escape(name)+r'=\"([^\"]*)\"',text,re.I)
            return m.group(1) if m else default
        result.append({'id':f.stem,'name':attr('name',f.stem.replace('-',' ').title()),'description':attr('description','Custom AI template'),'file':f.name,'prompt':attr('prompt','')})
    return result

def defaults():
    return {'provider':{'base_url':os.getenv('NVIDIA_BASE_URL','https://integrate.api.nvidia.com/v1'),'model':os.getenv('NVIDIA_MODEL','meta/llama-3.1-8b-instruct'),'api_key':os.getenv('NVIDIA_API_KEY','')},'templates':read_templates(),'projects':[{'id':'default','name':'Default'}],'chats':[],'skills':[]}

def load():
    d=defaults()
    if DATA.exists():
        try:
            saved=json.loads(DATA.read_text())
            d.update(saved)
            d['templates']=read_templates()
        except Exception: pass
    else: DATA.write_text(json.dumps(d,indent=2))
    return d

def call_llm(payload):
    p=payload.get('provider') or {}; base=(p.get('base_url') or '').rstrip('/'); model=p.get('model') or 'meta/llama-3.1-8b-instruct'; key=p.get('api_key') or os.getenv('NVIDIA_API_KEY','')
    if not base or not key: raise RuntimeError('Configure an OpenAI-compatible Base URL and API key.')
    t=payload.get('template') or {}; system='You are Jolgue AI.\n\nTemplate: '+t.get('name','General')+'\n'+t.get('prompt','')+'\n'
    for s in payload.get('skills',[]): system+=f"\nSkill: {s.get('name')}\n{s.get('instructions')}\n"
    body=json.dumps({'model':model,'messages':[{'role':'system','content':system}]+payload.get('messages',[]),'temperature':0.2,'stream':False}).encode()
    req=urllib.request.Request(base+'/chat/completions',data=body,headers={'Content-Type':'application/json','Authorization':'Bearer '+key},method='POST')
    with urllib.request.urlopen(req,timeout=180) as r:return json.loads(r.read())['choices'][0]['message']['content']

class H(BaseHTTPRequestHandler):
    def send_json(self,obj,status=200):
        b=json.dumps(obj).encode();self.send_response(status);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(b)));self.end_headers();self.wfile.write(b)
    def do_GET(self):
        if self.path=='/':
            b=BASE_HTML.encode();self.send_response(200);self.send_header('Content-Type','text/html; charset=utf-8');self.send_header('Content-Length',str(len(b)));self.end_headers();self.wfile.write(b);return
        if self.path=='/api/state': self.send_json(load());return
        self.send_json({'error':'Not found'},404)
    def do_POST(self):
        n=int(self.headers.get('Content-Length','0'));raw=self.rfile.read(n)
        try:data=json.loads(raw or b'{}')
        except Exception:self.send_json({'error':'Invalid JSON'},400);return
        if self.path=='/api/state': DATA.write_text(json.dumps(data,indent=2));self.send_json({'ok':True});return
        if self.path=='/api/chat':
            try:self.send_json({'content':call_llm(data)})
            except urllib.error.HTTPError as e:self.send_json({'error':f'Provider HTTP {e.code}: {e.read().decode(errors="ignore")}'},502)
            except Exception as e:self.send_json({'error':str(e)},500)
            return
        self.send_json({'error':'Not found'},404)

if __name__=='__main__':
    load();print(f'Jolgue AI listening on {HOST}:{PORT}');ThreadingHTTPServer((HOST,PORT),H).serve_forever()
