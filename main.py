import json
import os
import urllib.error
import urllib.request
from html import escape
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

ROOT = Path(__file__).parent
DATA = ROOT / "data.json"
HOST = "0.0.0.0"
PORT = int(os.getenv("PORT", "80"))

TEMPLATES = [
    {"id":"general","name":"General","icon":"✦","description":"Versatile assistant for everyday tasks.","system":"You are Jolgue AI, a precise and helpful general-purpose assistant."},
    {"id":"coding","name":"Coding Agent","icon":"⌘","description":"Build, debug and explain software.","system":"You are Jolgue Coding Agent. Write production-quality code, reason about bugs, and explain implementation choices clearly."},
    {"id":"research","name":"Research","icon":"◈","description":"Analyze topics and synthesize information.","system":"You are Jolgue Research Agent. Separate facts from assumptions, structure evidence, and be explicit about uncertainty."},
    {"id":"writer","name":"Writer","icon":"✎","description":"Draft, rewrite and polish text.","system":"You are Jolgue Writer. Produce clear, natural, audience-aware writing while preserving the user's intent."},
    {"id":"debugger","name":"Debugger","icon":"⚙","description":"Find root causes and fixes.","system":"You are Jolgue Debugger. Diagnose root causes methodically, propose minimal fixes, and verify edge cases."},
    {"id":"custom","name":"Custom","icon":"＋","description":"Create your own reusable agent template.","system":"You are a custom Jolgue AI agent. Follow the template instructions supplied by the user."},
]

INDEX = r'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Jolgue AI</title>
<style>
:root{--bg:#090b10;--panel:#0f131a;--panel2:#141923;--line:#252b38;--text:#f4f6f8;--muted:#8e98a8;--accent:#e8edf5}*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:14px Inter,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}button,input,textarea{font:inherit}button{cursor:pointer}.app{display:grid;grid-template-columns:250px 1fr;min-height:100vh}.sidebar{border-right:1px solid var(--line);background:var(--panel);padding:14px;overflow:auto}.brand{font-weight:800;font-size:18px;padding:8px 8px 16px}.nav{display:grid;gap:5px;margin-bottom:16px}.nav button,.project,.chatrow{width:100%;text-align:left;border:1px solid transparent;border-radius:9px;background:transparent;color:var(--text);padding:9px}.nav button:hover,.project:hover,.chatrow:hover{background:var(--panel2)}.nav button.active,.chatrow.active{background:#1b202b;border-color:var(--line)}.section{margin-top:18px}.label{color:var(--muted);font-size:11px;text-transform:uppercase;letter-spacing:.08em;margin:0 8px 7px}.stack{display:grid;gap:5px}.main{display:flex;min-width:0;flex-direction:column}.top{height:60px;border-bottom:1px solid var(--line);display:flex;align-items:center;justify-content:space-between;padding:0 20px}.top-title{font-weight:700}.top-actions{display:flex;gap:7px}.btn{border:1px solid var(--line);border-radius:9px;background:var(--panel2);color:var(--text);padding:8px 11px}.btn.primary{background:var(--accent);color:#0b0d12;border-color:var(--accent);font-weight:700}.content{flex:1;overflow:auto}.screen{max-width:1120px;margin:0 auto;padding:28px}.hero h1{font-size:30px;margin:0 0 7px}.hero p{margin:0;color:var(--muted)}.grid{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin-top:24px}.card{border:1px solid var(--line);background:var(--panel);border-radius:14px;padding:18px;transition:.15s}.card:hover{border-color:#3a4252;transform:translateY(-1px)}.icon{font-size:22px}.card h3{margin:12px 0 6px}.card p{color:var(--muted);min-height:38px;line-height:1.45}.card button{margin-top:13px;width:100%}.chatview{display:flex;height:calc(100vh - 60px);flex-direction:column}.messages{flex:1;overflow:auto;padding:28px;max-width:900px;width:100%;margin:0 auto}.msg{padding:13px 15px;border-radius:12px;margin:12px 0;line-height:1.55;white-space:pre-wrap}.user{background:#1a202b}.assistant{background:#11161e;border:1px solid #1d2430}.composer{display:flex;gap:9px;max-width:900px;width:100%;margin:0 auto;padding:14px 20px 20px}.composer textarea{flex:1;min-height:58px;max-height:180px;resize:vertical;background:var(--panel);color:var(--text);border:1px solid var(--line);border-radius:12px;padding:13px;outline:none}.composer textarea:focus,input:focus{border-color:#566178}.empty{height:100%;display:grid;place-items:center;color:var(--muted);text-align:center}.panel{max-width:760px;margin:0 auto;border:1px solid var(--line);background:var(--panel);border-radius:14px;padding:18px}.field{display:grid;gap:6px;margin:12px 0}.field label{font-size:12px;color:var(--muted)}input,textarea,select{width:100%;background:#0c1016;color:var(--text);border:1px solid var(--line);border-radius:9px;padding:10px}.row{display:flex;gap:8px;align-items:center}.small{font-size:12px;color:var(--muted)}.pill{font-size:11px;border:1px solid var(--line);border-radius:999px;padding:4px 7px;color:var(--muted)}.sep{height:1px;background:var(--line);margin:14px 0}@media(max-width:850px){.app{grid-template-columns:1fr}.sidebar{position:sticky;top:0;z-index:5;border-right:0;border-bottom:1px solid var(--line);height:auto}.grid{grid-template-columns:1fr 1fr}}@media(max-width:560px){.grid{grid-template-columns:1fr}.top{padding:0 12px}.screen{padding:20px 14px}.messages{padding:18px 12px}.composer{padding:10px 12px 14px}}
</style></head><body><div id="app" class="app"></div>
<script>
const TEMPLATES=__TEMPLATES__;
const state={data:null,view:'templates',template:null,project:null,chat:null};
const $=id=>document.getElementById(id);
async function api(path,method='GET',body){const r=await fetch(path,{method,headers:{'content-type':'application/json'},body:body?JSON.stringify(body):undefined});return r.json()}
async function save(){await api('/api/state','POST',state.data)}
function esc(s){return String(s??'').replace(/[&<>\"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;',"'":'&#39;'}[c]))}
function getTemplate(id){return TEMPLATES.find(t=>t.id===id)||TEMPLATES[0]}
function ensureData(){if(!state.data.projects?.length)state.data.projects=[{id:crypto.randomUUID(),name:'My Project'}];if(!state.data.chats)state.data.chats=[];if(!state.data.templates)state.data.templates=[];if(!state.data.skills)state.data.skills=[]}
async function boot(){state.data=await api('/api/state');ensureData();state.project=state.data.projects[0];render()}
function sidebar(){return `<aside class="sidebar"><div class="brand">Jolgue AI</div><div class="nav"><button class="${state.view==='templates'?'active':''}" onclick="go('templates')">✦ Templates</button><button class="${state.view==='projects'?'active':''}" onclick="go('projects')">▦ Projects</button><button class="${state.view==='skills'?'active':''}" onclick="go('skills')">◇ Skills</button><button class="${state.view==='settings'?'active':''}" onclick="go('settings')">⚙ Settings</button></div><div class="section"><div class="label">Projects</div><div class="stack">${state.data.projects.map(p=>`<button class="project" onclick="openProject('${p.id}')">${esc(p.name)}</button>`).join('')}</div></div><div class="section"><div class="label">Recent chats</div><div class="stack">${state.data.chats.slice(0,8).map(c=>`<button class="chatrow ${state.chat?.id===c.id?'active':''}" onclick="openChat('${c.id}')">${esc(c.title||'New chat')}<div class="small">${esc(getTemplate(c.template_id).name)}</div></button>`).join('')}</div></div></aside>`}
function render(){document.getElementById('app').innerHTML=sidebar()+`<main class="main"><header class="top"><div class="top-title">${headerTitle()}</div><div class="top-actions">${state.view==='chat'?'<button class="btn" onclick="go(\'templates\')">Templates</button>':''}<button class="btn primary" onclick="newChat()">+ New chat</button></div></header><div class="content">${screen()}</div></main>`;if(state.view==='chat')scrollBottom()}
function headerTitle(){if(state.view==='chat')return `${esc(state.chat?.title||'New chat')} <span class="pill">${esc(getTemplate(state.chat?.template_id).name)}</span>`;return state.view==='templates'?'Templates':state.view[0].toUpperCase()+state.view.slice(1)}
function screen(){if(state.view==='templates')return templatesScreen();if(state.view==='projects')return projectsScreen();if(state.view==='skills')return skillsScreen();if(state.view==='settings')return settingsScreen();return chatScreen()}
function templatesScreen(){return `<div class="screen"><div class="hero"><h1>Choose a template</h1><p>Start a chat with a purpose-built agent. Each template keeps its own instructions and behavior.</p></div><div class="grid">${TEMPLATES.map(t=>`<article class="card"><div class="icon">${t.icon}</div><h3>${esc(t.name)}</h3><p>${esc(t.description)}</p><button class="btn primary" onclick="useTemplate('${t.id}')">Use template</button></article>`).join('')}</div></div>`}
function projectsScreen(){return `<div class="screen"><div class="hero"><h1>Projects</h1><p>Keep related chats together. Templates can be reused inside every project.</p></div><div class="grid">${state.data.projects.map(p=>`<article class="card"><h3>${esc(p.name)}</h3><p>${state.data.chats.filter(c=>c.project_id===p.id).length} chats</p><button class="btn" onclick="openProject('${p.id}')">Open project</button></article>`).join('')}<article class="card"><h3>New project</h3><p>Create a separate workspace for a new goal.</p><button class="btn primary" onclick="createProject()">Create project</button></article></div></div>`}
function skillsScreen(){return `<div class="screen"><div class="hero"><h1>Skills</h1><p>Reusable instructions that can be attached to templates and chats.</p></div><div class="panel"><div class="field"><label>Name</label><input id="skill-name" placeholder="e.g. Roblox Luau Expert"></div><div class="field"><label>Instructions</label><textarea id="skill-body" rows="7" placeholder="Describe what the skill should make the agent do..."></textarea></div><button class="btn primary" onclick="createSkill()">Create skill</button><div class="sep"></div>${state.data.skills.length?state.data.skills.map(s=>`<div class="row" style="justify-content:space-between;padding:10px 0;border-bottom:1px solid var(--line)"><div><b>${esc(s.name)}</b><div class="small">${esc(s.instructions)}</div></div><span class="pill">skill</span></div>`).join(''):'<div class="small">No custom skills yet.</div>'}</div></div>`}
function settingsScreen(){const p=state.data.provider||{};return `<div class="screen"><div class="hero"><h1>Provider</h1><p>Configure any OpenAI-compatible endpoint. NVIDIA is the default.</p></div><div class="panel"><div class="field"><label>Base URL</label><input id="cfg-base" value="${esc(p.base_url||'')}"></div><div class="field"><label>Model</label><input id="cfg-model" value="${esc(p.model||'')}"></div><div class="field"><label>API key</label><input id="cfg-key" type="password" placeholder="Leave blank to use NVIDIA_API_KEY" value="${esc(p.api_key||'')}"></div><button class="btn primary" onclick="saveCfg()">Save provider</button><div class="sep"></div><div class="small">The API key is currently stored with the local application state. For production, use an environment secret instead.</div></div></div>`}
function chatScreen(){if(!state.chat)return `<div class="empty">Choose a template to start.</div>`;return `<div class="chatview"><div id="messages" class="messages">${state.chat.messages.length?state.chat.messages.map(m=>`<div class="msg ${m.role==='user'?'user':'assistant'}"><b>${m.role==='user'?'You':'Jolgue AI'}</b><br>${esc(m.content)}</div>`).join(''):'<div class="empty"><div><h2>${esc(getTemplate(state.chat.template_id).name)}</h2><div>Start the conversation with this template.</div></div></div>'}</div><div class="composer"><textarea id="input" placeholder="Message..." onkeydown="key(event)"></textarea><button class="btn primary" onclick="send()">Send</button></div></div>`}
function go(v){state.view=v;render()}
function useTemplate(id){state.template=getTemplate(id);newChat(id)}
async function newChat(templateId){const id=typeof templateId==='string'?templateId:state.template?.id||TEMPLATES[0].id;const c={id:crypto.randomUUID(),title:'New chat',messages:[],template_id:id,project_id:(state.project||state.data.projects[0]).id};state.data.chats.unshift(c);state.chat=c;state.view='chat';await save();render()}
function openChat(id){state.chat=state.data.chats.find(c=>c.id===id);state.project=state.data.projects.find(p=>p.id===state.chat?.project_id)||state.data.projects[0];state.view='chat';render()}
function openProject(id){state.project=state.data.projects.find(p=>p.id===id)||state.data.projects[0];state.view='projects';render()}
async function createProject(){const name=prompt('Project name');if(!name?.trim())return;state.data.projects.push({id:crypto.randomUUID(),name:name.trim()});state.project=state.data.projects.at(-1);await save();render()}
async function createSkill(){const name=$('skill-name')?.value.trim(),instructions=$('skill-body')?.value.trim();if(!name||!instructions)return alert('Fill both fields.');state.data.skills.push({id:crypto.randomUUID(),name,instructions});await save();render()}
async function saveCfg(){state.data.provider={base_url:$('cfg-base').value.trim(),model:$('cfg-model').value.trim(),api_key:$('cfg-key').value};await save();alert('Provider saved.')}
function selectedSkills(){return state.data.skills}
async function send(){const i=$('input');const text=i.value.trim();if(!text||!state.chat)return;i.value='';state.chat.messages.push({role:'user',content:text});if(state.chat.messages.length===1)state.chat.title=text.slice(0,48);render();const payload={messages:state.chat.messages,provider:state.data.provider,template:getTemplate(state.chat.template_id),skills:selectedSkills()};const r=await api('/api/chat','POST',payload);state.chat.messages.push({role:'assistant',content:r.content||r.error||'No response'});await save();render()}
function key(e){if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();send()}}
function scrollBottom(){setTimeout(()=>{const m=$('messages');if(m)m.scrollTop=m.scrollHeight},0)}
boot();
</script></body></html>'''


def defaults():
    return {"provider":{"base_url":os.getenv("NVIDIA_BASE_URL","https://integrate.api.nvidia.com/v1"),"model":os.getenv("NVIDIA_MODEL","meta/llama-3.1-8b-instruct"),"api_key":os.getenv("NVIDIA_API_KEY","")},"projects":[{"id":"default","name":"My Project"}],"chats":[],"skills":[],"templates":TEMPLATES}

def load():
    if not DATA.exists():
        d=defaults(); DATA.write_text(json.dumps(d,indent=2)); return d
    try:
        d=json.loads(DATA.read_text())
        base=defaults(); base.update(d)
        return base
    except Exception:
        return defaults()

def call_llm(payload):
    p=payload.get("provider") or {}
    base=(p.get("base_url") or "").rstrip("/")
    model=p.get("model") or "meta/llama-3.1-8b-instruct"
    key=p.get("api_key") or os.getenv("NVIDIA_API_KEY","")
    if not base or not key: raise RuntimeError("Configure an OpenAI-compatible Base URL and API key.")
    template=payload.get("template") or {}
    system=template.get("system") or "You are Jolgue AI, a helpful assistant."
    for s in payload.get("skills",[]):
        system += f"\n\nSkill: {s.get('name')}\n{s.get('instructions')}"
    messages=[{"role":"system","content":system}]+payload.get("messages",[])
    body=json.dumps({"model":model,"messages":messages,"temperature":0.2,"stream":False}).encode()
    req=urllib.request.Request(base+"/chat/completions",data=body,headers={"Content-Type":"application/json","Authorization":"Bearer "+key},method="POST")
    with urllib.request.urlopen(req,timeout=180) as r:
        out=json.loads(r.read())
    return out["choices"][0]["message"]["content"]

class Handler(BaseHTTPRequestHandler):
    def send_json(self,obj,status=200):
        b=json.dumps(obj).encode();self.send_response(status);self.send_header("Content-Type","application/json; charset=utf-8");self.send_header("Content-Length",str(len(b)));self.end_headers();self.wfile.write(b)
    def do_GET(self):
        path=urlparse(self.path).path
        if path=="/":
            html=INDEX.replace("__TEMPLATES__",json.dumps(TEMPLATES,separators=(",",":")))
            b=html.encode();self.send_response(200);self.send_header("Content-Type","text/html; charset=utf-8");self.send_header("Content-Length",str(len(b)));self.end_headers();self.wfile.write(b);return
        if path=="/api/state": self.send_json(load());return
        self.send_json({"error":"Not found"},404)
    def do_POST(self):
        n=int(self.headers.get("Content-Length","0"));raw=self.rfile.read(n)
        try:data=json.loads(raw or b"{}")
        except Exception:self.send_json({"error":"Invalid JSON"},400);return
        if self.path=="/api/state":
            try: DATA.write_text(json.dumps(data,indent=2));self.send_json({"ok":True})
            except Exception as e:self.send_json({"error":str(e)},500)
            return
        if self.path=="/api/chat":
            try:self.send_json({"content":call_llm(data)})
            except urllib.error.HTTPError as e:self.send_json({"error":f"Provider HTTP {e.code}: {e.read().decode(errors='ignore')}"},502)
            except Exception as e:self.send_json({"error":str(e)},500)
            return
        self.send_json({"error":"Not found"},404)

if __name__=="__main__":
    load();print(f"Jolgue AI listening on {HOST}:{PORT}");ThreadingHTTPServer((HOST,PORT),Handler).serve_forever()
