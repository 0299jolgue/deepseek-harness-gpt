import html
import json
import os
import re
import secrets
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import zipfile
from http.cookies import SimpleCookie
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = Path(__file__).parent.resolve()
DATA = ROOT / "data.json"
AUTH_FILE = ROOT / "auth.json"
WORKSPACE = ROOT / "workspace"
TEMPLATES = ROOT / "templates"
HOST = "0.0.0.0"
PORT = int(os.getenv("PORT", "80"))
SHELL_ENABLED = os.getenv("ENABLE_SHELL", "1").lower() not in {"0", "false", "no"}
SHELL_TIMEOUT = int(os.getenv("SHELL_TIMEOUT", "30"))
SESSION_TTL = int(os.getenv("SESSION_TTL", "86400"))
MAX_AGENT_STEPS = int(os.getenv("MAX_AGENT_STEPS", "8"))

JOBS = {}
JOBS_LOCK = threading.Lock()
SESSIONS = {}
SESSIONS_LOCK = threading.Lock()


def defaults():
    return {
        "provider": {
            "base_url": os.getenv("NVIDIA_BASE_URL", "https://integrate.api.nvidia.com/v1"),
            "model": os.getenv("NVIDIA_MODEL", "meta/llama-3.1-8b-instruct"),
            "api_key": os.getenv("NVIDIA_API_KEY", ""),
        },
        "search": {
            "provider": os.getenv("SEARCH_PROVIDER", "duckduckgo"),
            "api_key": os.getenv("BRAVE_SEARCH_API_KEY", ""),
        },
        "chats": [],
        "skills": [],
    }


def load():
    d = defaults()
    if DATA.exists():
        try:
            current = json.loads(DATA.read_text(encoding="utf-8"))
            if isinstance(current, dict):
                d.update(current)
        except Exception:
            pass
    else:
        DATA.write_text(json.dumps(d, indent=2), encoding="utf-8")
    return d


def auth_config():
    username = os.getenv("JOLGUE_USERNAME", "admin")
    env_password = os.getenv("JOLGUE_PASSWORD", "")
    if env_password:
        return {"username": username, "password": env_password, "generated": False}
    if AUTH_FILE.exists():
        try:
            data = json.loads(AUTH_FILE.read_text(encoding="utf-8"))
            if data.get("username") and data.get("password"):
                return data
        except Exception:
            pass
    password = secrets.token_urlsafe(12)
    data = {"username": username, "password": password, "generated": True}
    AUTH_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return data


def session_user(handler):
    raw = handler.headers.get("Cookie", "")
    if not raw:
        return None
    c = SimpleCookie()
    try:
        c.load(raw)
    except Exception:
        return None
    token = c.get("jolgue_session")
    if not token:
        return None
    value = token.value
    with SESSIONS_LOCK:
        item = SESSIONS.get(value)
        if not item:
            return None
        if item["expires"] < time.time():
            SESSIONS.pop(value, None)
            return None
        return item["username"]


def create_session(username):
    token = secrets.token_urlsafe(32)
    with SESSIONS_LOCK:
        SESSIONS[token] = {"username": username, "expires": time.time() + SESSION_TTL}
    return token


def require_auth(handler):
    return session_user(handler) is not None


def safe_path(name):
    p = (WORKSPACE / str(name or "")).resolve()
    if p != WORKSPACE and WORKSPACE not in p.parents:
        raise ValueError("Path is outside the workspace")
    return p


def list_files():
    WORKSPACE.mkdir(exist_ok=True)
    return [{"path": str(p.relative_to(WORKSPACE)), "size": p.stat().st_size} for p in sorted(WORKSPACE.rglob("*")) if p.is_file()]


def file_tool(name, args):
    if name in ("create_file", "write_file"):
        p = safe_path(args.get("path")); p.parent.mkdir(parents=True, exist_ok=True); p.write_text(args.get("content", ""), encoding="utf-8")
        return {"ok": True, "path": str(p.relative_to(WORKSPACE))}
    if name == "read_file":
        p = safe_path(args.get("path"))
        if not p.is_file(): raise ValueError("File not found")
        return {"ok": True, "path": str(p.relative_to(WORKSPACE)), "content": p.read_text(encoding="utf-8")}
    if name == "delete_file":
        p = safe_path(args.get("path"))
        if p.is_dir(): shutil.rmtree(p)
        elif p.exists(): p.unlink()
        return {"ok": True, "path": str(p.relative_to(WORKSPACE))}
    if name == "create_directory":
        p = safe_path(args.get("path")); p.mkdir(parents=True, exist_ok=True)
        return {"ok": True, "path": str(p.relative_to(WORKSPACE))}
    if name == "move_file":
        src = safe_path(args.get("source")); dest = safe_path(args.get("destination")); dest.parent.mkdir(parents=True, exist_ok=True); shutil.move(str(src), str(dest))
        return {"ok": True, "path": str(dest.relative_to(WORKSPACE))}
    if name == "copy_file":
        src = safe_path(args.get("source")); dest = safe_path(args.get("destination")); dest.parent.mkdir(parents=True, exist_ok=True)
        if src.is_dir(): shutil.copytree(src, dest, dirs_exist_ok=True)
        else: shutil.copy2(src, dest)
        return {"ok": True, "path": str(dest.relative_to(WORKSPACE))}
    if name == "zip_files":
        src = safe_path(args.get("source", ".")); dest = safe_path(args.get("output", "archive.zip")); dest.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as z:
            if src.is_file(): z.write(src, src.name)
            else:
                for p in src.rglob("*"):
                    if p.is_file() and p != dest: z.write(p, p.relative_to(src))
        rel = str(dest.relative_to(WORKSPACE))
        return {"ok": True, "path": rel, "download_url": "/download?path=" + urllib.parse.quote(rel)}
    if name == "unzip_files":
        src = safe_path(args.get("archive")); dest = safe_path(args.get("output", "unzipped")); dest.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(src) as z:
            for member in z.infolist():
                target = (dest / member.filename).resolve()
                if target != WORKSPACE and WORKSPACE not in target.parents: raise ValueError("Unsafe ZIP path")
            z.extractall(dest)
        return {"ok": True, "path": str(dest.relative_to(WORKSPACE))}
    raise ValueError("Unknown file tool")


def shell_tool(args):
    if not SHELL_ENABLED: raise RuntimeError("Shell is disabled. Set ENABLE_SHELL=1 to enable it.")
    command = args.get("command")
    if isinstance(command, list):
        argv = [str(x) for x in command]
        display_command = " ".join(argv)
    else:
        command = str(command or "").strip()
        if not command: raise ValueError("Missing command")
        argv = ["/bin/sh", "-lc", command]
        display_command = command
    try:
        proc = subprocess.run(argv, cwd=str(WORKSPACE), capture_output=True, text=True, timeout=SHELL_TIMEOUT)
    except subprocess.TimeoutExpired:
        return {"ok": False, "command": display_command, "exit_code": -1, "stdout": "", "stderr": f"Command timed out after {SHELL_TIMEOUT}s"}
    return {"ok": proc.returncode == 0, "command": display_command, "exit_code": proc.returncode, "stdout": proc.stdout[-12000:], "stderr": proc.stderr[-12000:]}


def search_web(query, provider="duckduckgo", api_key=""):
    query = str(query or "").strip()
    if not query: raise ValueError("Missing search query")
    provider = (provider or "duckduckgo").lower()
    if provider == "brave" and api_key:
        url = "https://api.search.brave.com/res/v1/web/search?" + urllib.parse.urlencode({"q": query, "count": 8})
        req = urllib.request.Request(url, headers={"Accept": "application/json", "X-Subscription-Token": api_key})
        with urllib.request.urlopen(req, timeout=20) as r: data = json.loads(r.read())
        return [{"title": item.get("title", ""), "url": item.get("url", ""), "snippet": item.get("description", "")} for item in data.get("web", {}).get("results", [])[:8]]
    url = "https://html.duckduckgo.com/html/?" + urllib.parse.urlencode({"q": query})
    req = urllib.request.Request(url, headers={"User-Agent": "JolgueAI/1.0"})
    with urllib.request.urlopen(req, timeout=20) as r: page = r.read().decode("utf-8", errors="ignore")
    blocks = re.findall(r'<a[^>]+class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', page, re.I | re.S)
    snippets = re.findall(r'<a[^>]+class="result__snippet"[^>]*>(.*?)</a>', page, re.I | re.S)
    results = []
    for i, (url0, title0) in enumerate(blocks[:8]):
        title0 = html.unescape(re.sub(r"\s+", " ", re.sub(r"<.*?>", "", title0))).strip()
        snippet = html.unescape(re.sub(r"\s+", " ", re.sub(r"<.*?>", " ", snippets[i]))).strip() if i < len(snippets) else ""
        results.append({"title": title0, "url": html.unescape(url0), "snippet": snippet})
    return results


def job_cancelled(job_id):
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        return bool(job and job["cancelled"])


def call_llm(payload, job_id):
    p = payload.get("provider") or {}
    base = (p.get("base_url") or "").rstrip("/")
    key = p.get("api_key") or os.getenv("NVIDIA_API_KEY", "")
    model = p.get("model") or "meta/llama-3.1-8b-instruct"
    if not base or not key: raise RuntimeError("Configure an OpenAI-compatible Base URL and API key.")
    search_cfg = payload.get("search") or {}
    search_provider = search_cfg.get("provider") or os.getenv("SEARCH_PROVIDER", "duckduckgo")
    system = f'''You are Jolgue AI, an autonomous conversational and coding agent with a real workspace.

You have actual tools, and you should decide yourself when to use them. Do not merely describe what you would do when a tool can do it.
- Use shell when you need to inspect, run, debug, install, build, test, transform, or otherwise operate on files/programs in the workspace.
- Use web_search when the user asks for current/external information, a lookup, documentation, prices, news, compatibility, or facts that may have changed.
- Use file tools when you need to create, edit, read, move, copy, delete, zip, or unzip workspace files.
- You may chain several tool calls across multiple turns until the task is actually complete.
- After a tool call, inspect its result and continue working when another tool call is needed. Do not stop just because one tool succeeded.
- Do not ask the user to run a command that you can run yourself with shell.
- Do not claim a command, search, file operation, or test happened unless the tool result confirms it.

Available tools:
- create_file(path,content)
- write_file(path,content)
- read_file(path)
- delete_file(path)
- create_directory(path)
- move_file(source,destination)
- copy_file(source,destination)
- zip_files(source,output) — creates a downloadable archive.
- unzip_files(archive,output)
- shell(command) — runs with the workspace as cwd.
- web_search(query) — searches the public web. Current provider: {search_provider}.

When using a tool, emit exactly one line per tool call in this format:
<tool>{{"name":"TOOL_NAME","args":{{...}}}}</tool>

Use only workspace-relative file paths. Tool markup is for execution and must not be used as normal prose. After all needed tools finish, answer the user normally and summarize the actual work/results.'''
    for s in payload.get("skills", []):
        system += f"\nSkill: {s.get('name')}\n{s.get('instructions')}\n"
    body = json.dumps({"model": model, "messages": [{"role": "system", "content": system}] + payload.get("messages", []), "temperature": 0.2, "stream": False}).encode("utf-8")
    req = urllib.request.Request(base + "/chat/completions", data=body, headers={"Content-Type": "application/json", "Authorization": "Bearer " + key}, method="POST")
    response = None
    with JOBS_LOCK:
        if job_id in JOBS: JOBS[job_id]["response"] = None
    try:
        response = urllib.request.urlopen(req, timeout=180)
        with JOBS_LOCK:
            if job_id in JOBS: JOBS[job_id]["response"] = response
        if job_cancelled(job_id):
            response.close()
            raise RuntimeError("Generation stopped")
        raw = response.read()
        if job_cancelled(job_id): raise RuntimeError("Generation stopped")
        data = json.loads(raw)
        return data["choices"][0]["message"]["content"]
    finally:
        if response is not None:
            try: response.close()
            except Exception: pass
        with JOBS_LOCK:
            if job_id in JOBS: JOBS[job_id]["response"] = None


def run_tools(text, search_cfg):
    results = []
    for raw in re.findall(r'<tool>\s*(\{.*?\})\s*</tool>', text, re.S):
        call = {}
        try:
            call = json.loads(raw)
            name = call["name"]
            args = call.get("args", {})
            if name in {"create_file", "write_file", "read_file", "delete_file", "create_directory", "move_file", "copy_file", "zip_files", "unzip_files"}:
                result = file_tool(name, args)
            elif name == "shell":
                result = shell_tool(args)
            elif name == "web_search":
                result = {"ok": True, "query": args.get("query", ""), "results": search_web(args.get("query", ""), search_cfg.get("provider"), search_cfg.get("api_key") or os.getenv("BRAVE_SEARCH_API_KEY", ""))}
            else:
                raise ValueError(f"Unknown tool: {name}")
            results.append({"tool": name, **result})
        except Exception as e:
            results.append({"tool": call.get("name", "unknown"), "ok": False, "error": str(e)})
    return re.sub(r'<tool>\s*\{.*?\}\s*</tool>', '', text, flags=re.S).strip(), results


def agent_run(payload, job_id):
    messages = list(payload.get("messages", []))
    search_cfg = payload.get("search") or {}
    all_tools = []
    last_clean = ""
    for step in range(MAX_AGENT_STEPS):
        if job_cancelled(job_id): raise RuntimeError("Generation stopped")
        turn = dict(payload)
        turn["messages"] = messages
        content = call_llm(turn, job_id)
        if job_cancelled(job_id): raise RuntimeError("Generation stopped")
        clean, tools = run_tools(content, search_cfg)
        last_clean = clean
        if not tools:
            return clean, all_tools, step + 1
        all_tools.extend(tools)
        messages.append({"role": "assistant", "content": content})
        tool_blob = json.dumps(tools, ensure_ascii=False)
        if len(tool_blob) > 36000:
            tool_blob = tool_blob[:36000] + "\n[tool output truncated]"
        messages.append({"role": "user", "content": "Tool results from the previous step. Inspect these results and continue the task if more tool calls are needed.\n" + tool_blob})
    suffix = "\n\nI reached the maximum agent steps before finishing the task."
    return (last_clean + suffix).strip(), all_tools, MAX_AGENT_STEPS


LOGIN_HTML = '''<!doctype html><html lang="pt-PT"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Jolgue AI — Entrar</title><style>body{margin:0;min-height:100vh;display:grid;place-items:center;background:#212121;color:#eee;font:14px -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}.card{width:min(380px,calc(100% - 32px));background:#171717;border:1px solid #3d3d3d;border-radius:18px;padding:28px;box-sizing:border-box}.logo{width:44px;height:44px;border-radius:12px;background:#fff;color:#111;display:grid;place-items:center;font-weight:800;margin-bottom:18px}.title{font-size:25px;font-weight:650;margin-bottom:6px}.sub{color:#aaa;margin-bottom:22px}.field{width:100%;box-sizing:border-box;padding:12px;border:1px solid #454545;border-radius:10px;background:#212121;color:#eee;margin:7px 0;outline:none}.field:focus{border-color:#777}.btn{width:100%;margin-top:8px;padding:12px;border:0;border-radius:10px;background:#fff;color:#111;font-weight:700;cursor:pointer}.err{min-height:18px;color:#ff7777;margin-top:10px}.hint{margin-top:15px;color:#777;font-size:12px;line-height:1.4}</style></head><body><form class="card" method="post" action="/api/login"><div class="logo">J</div><div class="title">Jolgue AI</div><div class="sub">Inicia sessão no teu workspace privado.</div><input class="field" name="username" autocomplete="username" placeholder="Utilizador" required><input class="field" type="password" name="password" autocomplete="current-password" placeholder="Password" required><button class="btn">Entrar</button><div class="err">{{ERROR}}</div><div class="hint">O workspace, shell e definições do provider ficam protegidos por esta sessão.</div></form></body></html>'''


class H(BaseHTTPRequestHandler):
    def send_json(self, obj, status=200):
        b = json.dumps(obj, ensure_ascii=False).encode("utf-8"); self.send_response(status); self.send_header("Content-Type", "application/json; charset=utf-8"); self.send_header("Cache-Control", "no-store"); self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)

    def send_html(self, text, status=200):
        b = text.encode("utf-8"); self.send_response(status); self.send_header("Content-Type", "text/html; charset=utf-8"); self.send_header("Cache-Control", "no-store"); self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)

    def unauthorized(self):
        body = LOGIN_HTML.replace("{{ERROR}}", ""); self.send_html(body, 401)

    def do_GET(self):
        if self.path == "/health": self.send_json({"ok": True, "service": "jolgue-ai"}); return
        if self.path == "/logout":
            raw = self.headers.get("Cookie", ""); c = SimpleCookie(); c.load(raw or ""); token = c.get("jolgue_session")
            if token:
                with SESSIONS_LOCK: SESSIONS.pop(token.value, None)
            self.send_response(302); self.send_header("Set-Cookie", "jolgue_session=; Max-Age=0; Path=/; HttpOnly; SameSite=Lax"); self.send_header("Location", "/"); self.end_headers(); return
        if self.path.startswith("/download?"):
            if not require_auth(self): self.unauthorized(); return
            qs = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query); rel = qs.get("path", [""])[0]
            try:
                p = safe_path(rel)
                if not p.is_file(): self.send_json({"error": "File not found"}, 404); return
                b = p.read_bytes(); name = p.name.replace('"', "")
                self.send_response(200); self.send_header("Content-Type", "application/zip" if p.suffix.lower() == ".zip" else "application/octet-stream"); self.send_header("Content-Disposition", f'attachment; filename="{name}"'); self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)
            except Exception as e: self.send_json({"error": str(e)}, 400)
            return
        if self.path == "/":
            if not require_auth(self): self.send_html(LOGIN_HTML); return
            p = TEMPLATES / "chat.html"; b = p.read_bytes(); self.send_response(200); self.send_header("Content-Type", "text/html; charset=utf-8"); self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b); return
        if not require_auth(self): self.unauthorized(); return
        if self.path == "/api/state": self.send_json(load()); return
        if self.path == "/api/files": self.send_json({"files": list_files()}); return
        self.send_json({"error": "Not found"}, 404)

    def do_POST(self):
        n = int(self.headers.get("Content-Length", "0")); raw = self.rfile.read(n)
        if self.path == "/api/login":
            try:
                data = urllib.parse.parse_qs(raw.decode("utf-8", errors="ignore")); username = data.get("username", [""])[0]; password = data.get("password", [""])[0]; cfg = auth_config()
                if secrets.compare_digest(username, cfg["username"]) and secrets.compare_digest(password, cfg["password"]):
                    token = create_session(username); self.send_response(302); self.send_header("Set-Cookie", f"jolgue_session={token}; Max-Age={SESSION_TTL}; Path=/; HttpOnly; SameSite=Lax"); self.send_header("Location", "/"); self.end_headers(); return
                self.send_html(LOGIN_HTML.replace("{{ERROR}}", "Utilizador ou password inválidos."), 401); return
            except Exception: self.send_html(LOGIN_HTML.replace("{{ERROR}}", "Falha no login."), 400); return
        if self.path.startswith("/api/") and not require_auth(self): self.send_json({"error": "Authentication required"}, 401); return
        try: data = json.loads(raw or b"{}")
        except Exception: self.send_json({"error": "Invalid JSON"}, 400); return
        if self.path == "/api/state": DATA.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"); self.send_json({"ok": True}); return
        if self.path == "/api/files":
            try: self.send_json(file_tool(data.get("name"), data.get("args", {})))
            except Exception as e: self.send_json({"error": str(e)}, 400)
            return
        if self.path == "/api/search":
            try:
                cfg = data.get("search") or {}; results = search_web(cfg.get("query", ""), cfg.get("provider", "duckduckgo"), cfg.get("api_key", "")); self.send_json({"query": cfg.get("query", ""), "results": results})
            except Exception as e: self.send_json({"error": str(e)}, 502)
            return
        if self.path == "/api/shell":
            try: self.send_json(shell_tool(data))
            except Exception as e: self.send_json({"error": str(e)}, 400)
            return
        if self.path == "/api/chat/stop":
            job_id = str(data.get("job_id", ""))
            with JOBS_LOCK:
                job = JOBS.get(job_id)
                if not job: self.send_json({"ok": False, "stopped": False}); return
                job["cancelled"] = True; response = job.get("response")
                if response is not None:
                    try: response.close()
                    except Exception: pass
            self.send_json({"ok": True, "stopped": True}); return
        if self.path == "/api/chat":
            job_id = str(data.get("job_id") or uuid.uuid4())
            with JOBS_LOCK: JOBS[job_id] = {"cancelled": False, "response": None}
            try:
                content, tools, steps = agent_run(data, job_id)
                self.send_json({"content": content, "tools": tools, "steps": steps, "files": list_files(), "job_id": job_id})
            except RuntimeError as e:
                if "stopped" in str(e).lower() or job_cancelled(job_id): self.send_json({"stopped": True, "content": "Generation stopped.", "tools": [], "files": list_files()}, 499)
                else: self.send_json({"error": str(e)}, 500)
            except urllib.error.HTTPError as e: self.send_json({"error": f"Provider HTTP {e.code}: {e.read().decode(errors='ignore')}"}, 502)
            except Exception as e: self.send_json({"error": str(e)}, 500)
            finally:
                with JOBS_LOCK: JOBS.pop(job_id, None)
            return
        self.send_json({"error": "Not found"}, 404)


if __name__ == "__main__":
    WORKSPACE.mkdir(exist_ok=True)
    load()
    cfg = auth_config()
    print(f"Jolgue AI listening on http://{HOST}:{PORT}")
    if cfg.get("generated"):
        print(f"Login username: {cfg['username']}")
        print(f"Generated login password: {cfg['password']}")
    elif os.getenv("JOLGUE_PASSWORD"):
        print(f"Login username: {cfg['username']}")
    ThreadingHTTPServer((HOST, PORT), H).serve_forever()
