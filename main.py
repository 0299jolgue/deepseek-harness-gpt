import json
import os
import re
import shutil
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = Path(__file__).parent.resolve()
DATA = ROOT / "data.json"
WORKSPACE = ROOT / "workspace"
TEMPLATES = ROOT / "templates"
HOST = "0.0.0.0"
PORT = int(os.getenv("PORT", "80"))

def defaults():
    return {"provider":{"base_url":os.getenv("NVIDIA_BASE_URL","https://integrate.api.nvidia.com/v1"),"model":os.getenv("NVIDIA_MODEL","meta/llama-3.1-8b-instruct"),"api_key":os.getenv("NVIDIA_API_KEY","")},"chats":[],"skills":[]}

def load():
    d=defaults()
    if DATA.exists():
        try:d.update(json.loads(DATA.read_text()))
        except Exception:pass
    else: DATA.write_text(json.dumps(d,indent=2))
    return d

def safe_path(name):
    p=(WORKSPACE/str(name or "")).resolve()
    if p != WORKSPACE and WORKSPACE not in p.parents: raise ValueError("Path is outside the workspace")
    return p

def list_files():
    WORKSPACE.mkdir(exist_ok=True)
    return [{"path":str(p.relative_to(WORKSPACE)),"size":p.stat().st_size} for p in sorted(WORKSPACE.rglob("*")) if p.is_file()]

def file_tool(name,args):
    if name in ("create_file","write_file"):
        p=safe_path(args.get("path"));p.parent.mkdir(parents=True,exist_ok=True);p.write_text(args.get("content", ""),encoding="utf-8");return {"ok":True,"path":str(p.relative_to(WORKSPACE))}
    if name=="read_file":
        p=safe_path(args.get("path"));return {"ok":True,"path":str(p.relative_to(WORKSPACE)),"content":p.read_text(encoding="utf-8")}
    if name=="delete_file":
        p=safe_path(args.get("path"));
        if p.is_dir():shutil.rmtree(p)
        elif p.exists():p.unlink()
        return {"ok":True,"path":str(p.relative_to(WORKSPACE))}
    if name=="zip_files":
        src=safe_path(args.get("source","."));dest=safe_path(args.get("output","archive.zip"));dest.parent.mkdir(parents=True,exist_ok=True)
        with zipfile.ZipFile(dest,"w",zipfile.ZIP_DEFLATED) as z:
            for p in ([src] if src.is_file() else src.rglob("*")):
                if p.is_file() and p != dest:z.write(p,p.name if src.is_file() else p.relative_to(src))
        return {"ok":True,"path":str(dest.relative_to(WORKSPACE))}
    if name=="unzip_files":
        src=safe_path(args.get("archive"));dest=safe_path(args.get("output","unzipped"));dest.mkdir(parents=True,exist_ok=True)
        with zipfile.ZipFile(src) as z:
            for m in z.infolist():
                target=(dest/m.filename).resolve()
                if target != WORKSPACE and WORKSPACE not in target.parents:raise ValueError("Unsafe ZIP path")
            z.extractall(dest)
        return {"ok":True,"path":str(dest.relative_to(WORKSPACE))}
    raise ValueError("Unknown file tool")

def call_llm(payload):
    p=payload.get("provider") or {};base=(p.get("base_url") or "").rstrip("/");key=p.get("api_key") or os.getenv("NVIDIA_API_KEY","");model=p.get("model") or "meta/llama-3.1-8b-instruct"
    if not base or not key:raise RuntimeError("Configure an OpenAI-compatible Base URL and API key.")
    system='''You are Jolgue AI, a normal conversational AI with a workspace. You can discuss normally, write code, edit files and manage workspace files. Available tools: create_file(path,content), write_file(path,content), read_file(path), delete_file(path), zip_files(source,output), unzip_files(archive,output). When you need a file operation, emit exactly one line: <tool>{"name":"create_file","args":{"path":"example.py","content":"print(1)"}}</tool>. Use only workspace-relative paths.'''
    for s in payload.get("skills",[]):system+=f"\nSkill: {s.get('name')}\n{s.get('instructions')}\n"
    body=json.dumps({"model":model,"messages":[{"role":"system","content":system}]+payload.get("messages",[]),"temperature":0.2,"stream":False}).encode()
    req=urllib.request.Request(base+"/chat/completions",data=body,headers={"Content-Type":"application/json","Authorization":"Bearer "+key},method="POST")
    with urllib.request.urlopen(req,timeout=180) as r:return json.loads(r.read())["choices"][0]["message"]["content"]

def run_tools(text):
    results=[]
    for raw in re.findall(r'<tool>\s*(\{.*?\})\s*</tool>',text,re.S):
        try:
            c=json.loads(raw);results.append(file_tool(c["name"],c.get("args",{})))
        except Exception as e:results.append({"ok":False,"error":str(e)})
    return results

class H(BaseHTTPRequestHandler):
    def send_json(self,obj,status=200):
        b=json.dumps(obj).encode();self.send_response(status);self.send_header("Content-Type","application/json");self.send_header("Content-Length",str(len(b)));self.end_headers();self.wfile.write(b)
    def do_GET(self):
        if self.path=="/":
            p=TEMPLATES/"chat.html";b=p.read_bytes();self.send_response(200);self.send_header("Content-Type","text/html; charset=utf-8");self.send_header("Content-Length",str(len(b)));self.end_headers();self.wfile.write(b);return
        if self.path=="/api/state":self.send_json(load());return
        if self.path=="/api/files":self.send_json({"files":list_files()});return
        self.send_json({"error":"Not found"},404)
    def do_POST(self):
        n=int(self.headers.get("Content-Length","0"));raw=self.rfile.read(n)
        try:data=json.loads(raw or b"{}")
        except Exception:self.send_json({"error":"Invalid JSON"},400);return
        if self.path=="/api/state":DATA.write_text(json.dumps(data,indent=2));self.send_json({"ok":True});return
        if self.path=="/api/files":
            try:self.send_json(file_tool(data.get("name"),data.get("args",{})))
            except Exception as e:self.send_json({"error":str(e)},400)
            return
        if self.path=="/api/chat":
            try:
                content=call_llm(data);tools=run_tools(content);self.send_json({"content":content,"tools":tools,"files":list_files()})
            except urllib.error.HTTPError as e:self.send_json({"error":f"Provider HTTP {e.code}: {e.read().decode(errors='ignore')}"},502)
            except Exception as e:self.send_json({"error":str(e)},500)
            return
        self.send_json({"error":"Not found"},404)

if __name__=="__main__":
    WORKSPACE.mkdir(exist_ok=True);load();print(f"Jolgue AI listening on {HOST}:{PORT}");ThreadingHTTPServer((HOST,PORT),H).serve_forever()
