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
PORT = 80
DEFAULT_BASE_URL = "https://integrate.api.nvidia.com/v1"
DEFAULT_MODEL = "deepseek-ai/deepseek-v4-flash-0731"
SHELL_ENABLED = os.getenv("ENABLE_SHELL", "1").lower() not in {"0", "false", "no"}
SHELL_TIMEOUT = int(os.getenv("SHELL_TIMEOUT", "0"))
SESSION_TTL = int(os.getenv("SESSION_TTL", "86400"))
MAX_AGENT_STEPS = int(os.getenv("MAX_AGENT_STEPS", "0"))
JOB_RETENTION = int(os.getenv("JOB_RETENTION", "86400"))

JOBS = {}
JOBS_LOCK = threading.RLock()
SESSIONS = {}
SESSIONS_LOCK = threading.Lock()


def defaults():
    return {
        "provider": {
            "base_url": DEFAULT_BASE_URL,
            "model": DEFAULT_MODEL,
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
                for key, value in current.items():
                    if key == "provider" and isinstance(value, dict):
                        merged = dict(d["provider"])
                        merged.update({k: v for k, v in value.items() if v not in (None, "")})
                        d["provider"] = merged
                    elif key == "search" and isinstance(value, dict):
                        merged = dict(d["search"])
                        merged.update({k: v for k, v in value.items() if v not in (None, "")})
                        d["search"] = merged
                    else:
                        d[key] = value
        except Exception:
            pass
    else:
        DATA.write_text(json.dumps(d, indent=2, ensure_ascii=False), encoding="utf-8")
    if not d.get("provider", {}).get("base_url"):
        d["provider"]["base_url"] = DEFAULT_BASE_URL
    if not d.get("provider", {}).get("model"):
        d["provider"]["model"] = DEFAULT_MODEL
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
    result = []
    for p in sorted(WORKSPACE.rglob("*")):
        if p.is_file():
            try:
                result.append({"path": str(p.relative_to(WORKSPACE)), "size": p.stat().st_size})
            except OSError:
                pass
    return result


def file_tool(name, args):
    if name in ("create_file", "write_file"):
        p = safe_path(args.get("path"))
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(str(args.get("content", "")), encoding="utf-8")
        return {"ok": True, "path": str(p.relative_to(WORKSPACE))}
    if name == "read_file":
        p = safe_path(args.get("path"))
        if not p.is_file():
            raise ValueError("File not found")
        return {"ok": True, "path": str(p.relative_to(WORKSPACE)), "content": p.read_text(encoding="utf-8")}
    if name == "delete_file":
        p = safe_path(args.get("path"))
        if p.is_dir():
            shutil.rmtree(p)
        elif p.exists():
            p.unlink()
        return {"ok": True, "path": str(p.relative_to(WORKSPACE))}
    if name == "create_directory":
        p = safe_path(args.get("path"))
        p.mkdir(parents=True, exist_ok=True)
        return {"ok": True, "path": str(p.relative_to(WORKSPACE))}
    if name == "move_file":
        src = safe_path(args.get("source"))
        dest = safe_path(args.get("destination"))
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dest))
        return {"ok": True, "path": str(dest.relative_to(WORKSPACE))}
    if name == "copy_file":
        src = safe_path(args.get("source"))
        dest = safe_path(args.get("destination"))
        dest.parent.mkdir(parents=True, exist_ok=True)
        if src.is_dir():
            shutil.copytree(src, dest, dirs_exist_ok=True)
        else:
            shutil.copy2(src, dest)
        return {"ok": True, "path": str(dest.relative_to(WORKSPACE))}
    if name == "zip_files":
        src = safe_path(args.get("source", "."))
        dest = safe_path(args.get("output", "archive.zip"))
        dest.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as z:
            if src.is_file():
                z.write(src, src.name)
            elif src.exists():
                for p in src.rglob("*"):
                    if p.is_file() and p != dest:
                        z.write(p, p.relative_to(src))
            else:
                raise ValueError("Source not found")
        rel = str(dest.relative_to(WORKSPACE))
        return {"ok": True, "path": rel, "download_url": "/download?path=" + urllib.parse.quote(rel)}
    if name == "unzip_files":
        src = safe_path(args.get("archive"))
        dest = safe_path(args.get("output", "unzipped"))
        if not src.is_file():
            raise ValueError("Archive not found")
        dest.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(src) as z:
            for member in z.infolist():
                target = (dest / member.filename).resolve()
                if target != WORKSPACE and WORKSPACE not in target.parents:
                    raise ValueError("Unsafe ZIP path")
            z.extractall(dest)
        return {"ok": True, "path": str(dest.relative_to(WORKSPACE))}
    raise ValueError("Unknown file tool")


def shell_tool(args):
    if not SHELL_ENABLED:
        raise RuntimeError("Shell is disabled. Set ENABLE_SHELL=1 to enable it.")
    command = args.get("command")
    if isinstance(command, list):
        argv = [str(x) for x in command]
        display_command = " ".join(argv)
    else:
        command = str(command or "").strip()
        if not command:
            raise ValueError("Missing command")
        argv = ["/bin/sh", "-lc", command]
        display_command = command
    kwargs = {"cwd": str(WORKSPACE), "capture_output": True, "text": True}
    if SHELL_TIMEOUT > 0:
        kwargs["timeout"] = SHELL_TIMEOUT
    proc = subprocess.run(argv, **kwargs)
    return {
        "ok": proc.returncode == 0,
        "command": display_command,
        "exit_code": proc.returncode,
        "stdout": proc.stdout[-12000:],
        "stderr": proc.stderr[-12000:],
    }


def search_web(query, provider="duckduckgo", api_key=""):
    query = str(query or "").strip()
    if not query:
        raise ValueError("Missing search query")
    provider = (provider or "duckduckgo").lower()
    if provider == "brave" and api_key:
        url = "https://api.search.brave.com/res/v1/web/search?" + urllib.parse.urlencode({"q": query, "count": 8})
        req = urllib.request.Request(url, headers={"Accept": "application/json", "X-Subscription-Token": api_key})
        with urllib.request.urlopen(req) as r:
            data = json.loads(r.read())
        return [{"title": i.get("title", ""), "url": i.get("url", ""), "snippet": i.get("description", "")} for i in data.get("web", {}).get("results", [])[:8]]
    url = "https://html.duckduckgo.com/html/?" + urllib.parse.urlencode({"q": query})
    req = urllib.request.Request(url, headers={"User-Agent": "JolgueAI/2.0"})
    with urllib.request.urlopen(req) as r:
        page = r.read().decode("utf-8", errors="ignore")
    blocks = re.findall(r'<a[^>]+class=["\']result__a["\'][^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', page, re.I | re.S)
    snippets = re.findall(r'<a[^>]+class=["\']result__snippet["\'][^>]*>(.*?)</a>', page, re.I | re.S)
    results = []
    for i, (url0, title0) in enumerate(blocks[:8]):
        clean_title = html.unescape(re.sub(r"\s+", " ", re.sub(r"<.*?>", "", title0))).strip()
        snippet = html.unescape(re.sub(r"\s+", " ", re.sub(r"<.*?>", " ", snippets[i]))).strip() if i < len(snippets) else ""
        results.append({"title": clean_title, "url": html.unescape(url0), "snippet": snippet})
    return results


def job_cancelled(job_id):
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        return bool(job and job.get("cancelled"))


def set_job(job_id, **updates):
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if job:
            job.update(updates)
            job["updated"] = time.time()


def llm_request(payload, job_id):
    p = payload.get("provider") or {}
    base = (p.get("base_url") or DEFAULT_BASE_URL).rstrip("/")
    model = p.get("model") or DEFAULT_MODEL
    key = p.get("api_key") or os.getenv("NVIDIA_API_KEY", "")
    if not key:
        raise RuntimeError("NVIDIA_API_KEY is not configured. Add it to the server environment or Provider settings.")
    system = """You are Jolgue AI, an autonomous coding and research agent with a real workspace.

Actually use tools instead of merely explaining them. Continue until the user's task is completed.

Available tool names:
create_file, write_file, read_file, delete_file, create_directory, move_file, copy_file, zip_files, unzip_files, shell, web_search

For every tool action emit exactly:
<tool>{\"name\":\"TOOL_NAME\",\"args\":{...}}</tool>

Rules:
- Use workspace-relative paths only.
- Use shell for running programs, inspecting the environment, builds and tests.
- Use file tools for creating/editing/reading/moving/copying/deleting/zipping files.
- Use web_search for current or external information.
- Inspect tool results and continue with more tools when necessary.
- Do not claim an operation happened unless its tool result confirms it.
- zip_files creates a downloadable archive; mention that the UI will provide a download button after the tool completes.
"""
    for skill in payload.get("skills", []):
        if isinstance(skill, dict):
            system += "\nSkill: " + str(skill.get("name", "unnamed")) + "\n" + str(skill.get("instructions", ""))
    messages = [{"role": "system", "content": system}] + list(payload.get("messages", []))
    body = json.dumps({"model": model, "messages": messages, "temperature": 0.2, "stream": False}).encode("utf-8")
    req = urllib.request.Request(
        base + "/chat/completions",
        data=body,
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + key},
        method="POST",
    )
    set_job(job_id, phase="provider")
    with urllib.request.urlopen(req) as response:
        raw = response.read()
    if job_cancelled(job_id):
        raise RuntimeError("Generation stopped")
    data = json.loads(raw.decode("utf-8"))
    try:
        return data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        raise RuntimeError("Provider returned an unexpected response: " + json.dumps(data, ensure_ascii=False)[:4000])


def execute_tool_calls(text, search_cfg):
    results = []
    pattern = re.compile(r"<tool>\s*(\{.*?\})\s*</tool>", re.S)
    for raw in pattern.findall(text):
        call = {}
        try:
            call = json.loads(raw)
            name = str(call["name"])
            args = call.get("args") or {}
            if name in {"create_file", "write_file", "read_file", "delete_file", "create_directory", "move_file", "copy_file", "zip_files", "unzip_files"}:
                result = file_tool(name, args)
            elif name == "shell":
                result = shell_tool(args)
            elif name == "web_search":
                result = {
                    "ok": True,
                    "query": args.get("query", ""),
                    "results": search_web(args.get("query", ""), search_cfg.get("provider"), search_cfg.get("api_key") or os.getenv("BRAVE_SEARCH_API_KEY", "")),
                }
            else:
                raise ValueError("Unknown tool: " + name)
            results.append({"tool": name, **result})
        except subprocess.TimeoutExpired:
            results.append({"tool": call.get("name", "unknown"), "ok": False, "error": "Command timed out"})
        except Exception as exc:
            results.append({"tool": call.get("name", "unknown"), "ok": False, "error": str(exc)})
    clean = pattern.sub("", text).strip()
    return clean, results


def agent_run(payload, job_id):
    messages = list(payload.get("messages", []))
    search_cfg = payload.get("search") or {}
    all_tools = []
    last_clean = ""
    step = 0
    while True:
        if MAX_AGENT_STEPS > 0 and step >= MAX_AGENT_STEPS:
            return (last_clean + "\n\nReached the configured maximum agent steps.").strip(), all_tools, step
        if job_cancelled(job_id):
            raise RuntimeError("Generation stopped")
        step += 1
        set_job(job_id, step=step, phase="thinking")
        turn = dict(payload)
        turn["messages"] = messages
        content = llm_request(turn, job_id)
        clean, tools = execute_tool_calls(content, search_cfg)
        last_clean = clean
        if not tools:
            return clean, all_tools, step
        all_tools.extend(tools)
        set_job(job_id, phase="tools", last_tools=tools, tools=all_tools)
        messages.append({"role": "assistant", "content": content})
        tool_blob = json.dumps(tools, ensure_ascii=False)
        if len(tool_blob) > 36000:
            tool_blob = tool_blob[:36000] + "\n[tool output truncated]"
        messages.append({"role": "user", "content": "Tool results from the previous step. Inspect them and continue the task if needed.\n" + tool_blob})


def cleanup_jobs():
    cutoff = time.time() - JOB_RETENTION
    with JOBS_LOCK:
        for job_id, job in list(JOBS.items()):
            if job.get("status") in {"completed", "error", "stopped"} and job.get("updated", 0) < cutoff:
                JOBS.pop(job_id, None)


def active_jobs_for(username):
    with JOBS_LOCK:
        return [
            {"job_id": jid, "status": j.get("status"), "phase": j.get("phase"), "step": j.get("step", 0), "created": j.get("created"), "updated": j.get("updated")}
            for jid, j in JOBS.items()
            if j.get("username") == username and j.get("status") == "running"
        ]


def job_public(job):
    out = {
        "job_id": job["job_id"],
        "status": job.get("status"),
        "phase": job.get("phase"),
        "step": job.get("step", 0),
        "tools": job.get("tools", []),
        "files": list_files(),
        "updated": job.get("updated"),
    }
    if job.get("status") == "completed":
        out["content"] = job.get("content", "")
        out["steps"] = job.get("steps", 0)
    elif job.get("status") == "error":
        out["error"] = job.get("error", "Unknown error")
    elif job.get("status") == "stopped":
        out["content"] = job.get("content", "Generation stopped.")
    return out


def run_job(job_id, payload):
    try:
        content, tools, steps = agent_run(payload, job_id)
        set_job(job_id, status="completed", phase="done", content=content, tools=tools, steps=steps)
    except RuntimeError as exc:
        if "stopped" in str(exc).lower() or job_cancelled(job_id):
            set_job(job_id, status="stopped", phase="stopped", content="Generation stopped.", error=str(exc))
        else:
            set_job(job_id, status="error", phase="error", error=str(exc))
    except urllib.error.HTTPError as exc:
        try:
            detail = exc.read().decode(errors="ignore")
        except Exception:
            detail = str(exc)
        set_job(job_id, status="error", phase="error", error=f"Provider HTTP {exc.code}: {detail}")
    except Exception as exc:
        set_job(job_id, status="error", phase="error", error=str(exc))


LOGIN_HTML = '''<!doctype html><html lang="pt-PT"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Jolgue AI — Entrar</title><style>body{margin:0;min-height:100vh;display:grid;place-items:center;background:#212121;color:#eee;font:14px -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}.card{width:min(380px,calc(100% - 32px));background:#171717;border:1px solid #3d3d3d;border-radius:18px;padding:28px;box-sizing:border-box}.logo{width:44px;height:44px;border-radius:12px;background:#fff;color:#111;display:grid;place-items:center;font-weight:800;margin-bottom:18px}.title{font-size:25px;font-weight:650;margin-bottom:6px}.sub{color:#aaa;margin-bottom:22px}.field{width:100%;box-sizing:border-box;padding:12px;border:1px solid #454545;border-radius:10px;background:#212121;color:#eee;margin:7px 0;outline:none}.field:focus{border-color:#777}.btn{width:100%;margin-top:8px;padding:12px;border:0;border-radius:10px;background:#fff;color:#111;font-weight:700;cursor:pointer}.err{min-height:18px;color:#ff7777;margin-top:10px}.hint{margin-top:15px;color:#777;font-size:12px;line-height:1.4}</style></head><body><form class="card" method="post" action="/api/login"><div class="logo">J</div><div class="title">Jolgue AI</div><div class="sub">Inicia sessão no teu workspace privado.</div><input class="field" name="username" autocomplete="username" placeholder="Utilizador" required><input class="field" type="password" name="password" autocomplete="current-password" placeholder="Password" required><button class="btn">Entrar</button><div class="err">{{ERROR}}</div><div class="hint">Workspace, shell e provider protegidos por sessão.</div></form></body></html>'''


class H(BaseHTTPRequestHandler):
    def send_json(self, obj, status=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def send_html(self, text, status=200):
        body = text.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def unauthorized(self):
        self.send_html(LOGIN_HTML.replace("{{ERROR}}", ""), 401)

    def do_GET(self):
        cleanup_jobs()
        if self.path == "/health":
            self.send_json({"ok": True, "service": "jolgue-ai", "port": PORT, "model": DEFAULT_MODEL})
            return
        if self.path == "/logout":
            c = SimpleCookie()
            try:
                c.load(self.headers.get("Cookie", ""))
            except Exception:
                pass
            token = c.get("jolgue_session")
            if token:
                with SESSIONS_LOCK:
                    SESSIONS.pop(token.value, None)
            self.send_response(302)
            self.send_header("Set-Cookie", "jolgue_session=; Max-Age=0; Path=/; HttpOnly; SameSite=Lax")
            self.send_header("Location", "/")
            self.end_headers()
            return
        if self.path.startswith("/download?"):
            if not require_auth(self):
                self.unauthorized()
                return
            qs = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
            rel = qs.get("path", [""])[0]
            try:
                p = safe_path(rel)
                if not p.is_file():
                    self.send_json({"error": "File not found"}, 404)
                    return
                data = p.read_bytes()
                name = p.name.replace('"', "")
                self.send_response(200)
                self.send_header("Content-Type", "application/zip" if p.suffix.lower() == ".zip" else "application/octet-stream")
                self.send_header("Content-Disposition", f'attachment; filename="{name}"')
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                try:
                    self.wfile.write(data)
                except (BrokenPipeError, ConnectionResetError):
                    pass
            except Exception as exc:
                self.send_json({"error": str(exc)}, 400)
            return
        if self.path == "/":
            if not require_auth(self):
                self.send_html(LOGIN_HTML.replace("{{ERROR}}", ""))
                return
            p = TEMPLATES / "chat.html"
            self.send_html(p.read_text(encoding="utf-8"))
            return
        if not require_auth(self):
            self.unauthorized()
            return
        if self.path == "/api/state":
            self.send_json(load())
            return
        if self.path == "/api/files":
            self.send_json({"files": list_files()})
            return
        if self.path == "/api/jobs/active":
            self.send_json({"jobs": active_jobs_for(session_user(self))})
            return
        if self.path.startswith("/api/jobs/"):
            job_id = urllib.parse.unquote(self.path.split("/", 3)[3].split("?", 1)[0])
            with JOBS_LOCK:
                job = JOBS.get(job_id)
                if not job or job.get("username") != session_user(self):
                    self.send_json({"error": "Job not found"}, 404)
                    return
                self.send_json(job_public(job))
            return
        if self.path.startswith("/api/job"):
            qs = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
            job_id = qs.get("id", [""])[0]
            with JOBS_LOCK:
                job = JOBS.get(job_id)
                if not job or job.get("username") != session_user(self):
                    self.send_json({"error": "Job not found"}, 404)
                    return
                self.send_json(job_public(job))
            return
        self.send_json({"error": "Not found"}, 404)

    def do_POST(self):
        try:
            size = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            size = 0
        raw = self.rfile.read(size)
        if self.path == "/api/login":
            try:
                form = urllib.parse.parse_qs(raw.decode("utf-8", errors="ignore"))
                username = form.get("username", [""])[0]
                password = form.get("password", [""])[0]
                cfg = auth_config()
                if secrets.compare_digest(username, cfg["username"]) and secrets.compare_digest(password, cfg["password"]):
                    token = create_session(username)
                    self.send_response(302)
                    self.send_header("Set-Cookie", f"jolgue_session={token}; Max-Age={SESSION_TTL}; Path=/; HttpOnly; SameSite=Lax")
                    self.send_header("Location", "/")
                    self.end_headers()
                    return
                self.send_html(LOGIN_HTML.replace("{{ERROR}}", "Utilizador ou password inválidos."), 401)
                return
            except Exception:
                self.send_html(LOGIN_HTML.replace("{{ERROR}}", "Falha no login."), 400)
                return
        if self.path.startswith("/api/") and not require_auth(self):
            self.send_json({"error": "Authentication required"}, 401)
            return
        try:
            data = json.loads(raw or b"{}")
        except Exception:
            self.send_json({"error": "Invalid JSON"}, 400)
            return
        if self.path == "/api/state":
            current = load()
            if isinstance(data, dict):
                current.update(data)
                DATA.write_text(json.dumps(current, indent=2, ensure_ascii=False), encoding="utf-8")
            self.send_json({"ok": True, "state": load()})
            return
        if self.path == "/api/files":
            try:
                self.send_json(file_tool(data.get("name"), data.get("args", {})))
            except Exception as exc:
                self.send_json({"error": str(exc)}, 400)
            return
        if self.path == "/api/search":
            try:
                cfg = data.get("search") or {}
                self.send_json({"query": cfg.get("query", ""), "results": search_web(cfg.get("query", ""), cfg.get("provider", "duckduckgo"), cfg.get("api_key", ""))})
            except Exception as exc:
                self.send_json({"error": str(exc)}, 502)
            return
        if self.path == "/api/shell":
            try:
                self.send_json(shell_tool(data))
            except Exception as exc:
                self.send_json({"error": str(exc)}, 400)
            return
        if self.path in {"/api/chat/stop", "/api/jobs/stop"}:
            job_id = str(data.get("job_id", ""))
            with JOBS_LOCK:
                job = JOBS.get(job_id)
                if not job or job.get("username") != session_user(self):
                    self.send_json({"ok": False, "stopped": False}, 404)
                    return
                job["cancelled"] = True
                job["updated"] = time.time()
            self.send_json({"ok": True, "stopped": True})
            return
        if self.path == "/api/chat":
            username = session_user(self)
            job_id = str(data.get("job_id") or uuid.uuid4())
            now = time.time()
            with JOBS_LOCK:
                JOBS[job_id] = {
                    "job_id": job_id,
                    "username": username,
                    "cancelled": False,
                    "status": "running",
                    "phase": "queued",
                    "step": 0,
                    "created": now,
                    "updated": now,
                    "content": "",
                    "tools": [],
                }
            threading.Thread(target=run_job, args=(job_id, data), daemon=True, name="jolgue-job-" + job_id[:8]).start()
            self.send_json({"ok": True, "job_id": job_id, "status": "running"})
            return
        self.send_json({"error": "Not found"}, 404)


if __name__ == "__main__":
    WORKSPACE.mkdir(exist_ok=True)
    cfg = load()
    auth = auth_config()
    print(f"Jolgue AI listening on http://{HOST}:{PORT}")
    print(f"Provider Base URL: {cfg['provider']['base_url']}")
    print(f"Provider Model: {cfg['provider']['model']}")
    print("Agent jobs run independently of the browser.")
    print("Agent steps: unlimited" if MAX_AGENT_STEPS == 0 else f"Agent steps: {MAX_AGENT_STEPS}")
    if auth.get("generated"):
        print(f"Login username: {auth['username']}")
        print(f"Generated login password: {auth['password']}")
    ThreadingHTTPServer((HOST, PORT), H).serve_forever()
