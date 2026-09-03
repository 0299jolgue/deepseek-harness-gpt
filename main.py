import html
import json
import os
import re
import shutil
import subprocess
import threading
import urllib.error
import urllib.parse
import urllib.request
import uuid
import zipfile
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = Path(__file__).parent.resolve()
DATA = ROOT / "data.json"
WORKSPACE = ROOT / "workspace"
TEMPLATES = ROOT / "templates"
HOST = "0.0.0.0"
PORT = int(os.getenv("PORT", "80"))
SHELL_ENABLED = os.getenv("ENABLE_SHELL", "1").lower() not in {"0", "false", "no"}
SHELL_TIMEOUT = int(os.getenv("SHELL_TIMEOUT", "30"))

JOBS = {}
JOBS_LOCK = threading.Lock()


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


def safe_path(name):
    p = (WORKSPACE / str(name or "")).resolve()
    if p != WORKSPACE and WORKSPACE not in p.parents:
        raise ValueError("Path is outside the workspace")
    return p


def list_files():
    WORKSPACE.mkdir(exist_ok=True)
    return [
        {"path": str(p.relative_to(WORKSPACE)), "size": p.stat().st_size}
        for p in sorted(WORKSPACE.rglob("*"))
        if p.is_file()
    ]


def file_tool(name, args):
    if name in ("create_file", "write_file"):
        p = safe_path(args.get("path"))
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(args.get("content", ""), encoding="utf-8")
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
            else:
                for p in src.rglob("*"):
                    if p.is_file() and p != dest:
                        z.write(p, p.relative_to(src))
        rel = str(dest.relative_to(WORKSPACE))
        return {"ok": True, "path": rel, "download_url": "/download?path=" + urllib.parse.quote(rel)}
    if name == "unzip_files":
        src = safe_path(args.get("archive"))
        dest = safe_path(args.get("output", "unzipped"))
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
    else:
        command = str(command or "").strip()
        if not command:
            raise ValueError("Missing command")
        argv = ["/bin/sh", "-lc", command]
    proc = subprocess.run(argv, cwd=str(WORKSPACE), capture_output=True, text=True, timeout=SHELL_TIMEOUT)
    return {
        "ok": proc.returncode == 0,
        "command": command if not isinstance(command, list) else " ".join(argv),
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
        with urllib.request.urlopen(req, timeout=20) as r:
            data = json.loads(r.read())
        results = []
        for item in data.get("web", {}).get("results", [])[:8]:
            results.append({"title": item.get("title", ""), "url": item.get("url", ""), "snippet": item.get("description", "")})
        return results

    # Lightweight no-key fallback using DuckDuckGo's HTML results.
    url = "https://html.duckduckgo.com/html/?" + urllib.parse.urlencode({"q": query})
    req = urllib.request.Request(url, headers={"User-Agent": "JolgueAI/1.0"})
    with urllib.request.urlopen(req, timeout=20) as r:
        page = r.read().decode("utf-8", errors="ignore")
    blocks = re.findall(r'<a[^>]+class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', page, re.I | re.S)
    snippets = re.findall(r'<a[^>]+class="result__snippet"[^>]*>(.*?)</a>', page, re.I | re.S)
    results = []
    for i, (url0, title0) in enumerate(blocks[:8]):
        title0 = re.sub(r"<.*?>", "", title0)
        title0 = html.unescape(re.sub(r"\s+", " ", title0)).strip()
        url0 = html.unescape(url0)
        snippet = ""
        if i < len(snippets):
            snippet = html.unescape(re.sub(r"<.*?>", " ", snippets[i]))
            snippet = re.sub(r"\s+", " ", snippet).strip()
        results.append({"title": title0, "url": url0, "snippet": snippet})
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
    if not base or not key:
        raise RuntimeError("Configure an OpenAI-compatible Base URL and API key.")
    search_cfg = payload.get("search") or {}
    search_provider = search_cfg.get("provider") or os.getenv("SEARCH_PROVIDER", "duckduckgo")
    search_key = search_cfg.get("api_key") or os.getenv("BRAVE_SEARCH_API_KEY", "")
    system = f'''You are Jolgue AI, a normal conversational AI with a real workspace. You can create, edit, read, move, copy, delete, zip/unzip files, execute shell commands inside the workspace, and search the web.
Available tools:
- create_file(path,content)
- write_file(path,content)
- read_file(path)
- delete_file(path)
- create_directory(path)
- move_file(source,destination)
- copy_file(source,destination)
- zip_files(source,output) — this creates a downloadable archive; always mention the returned file when done.
- unzip_files(archive,output)
- shell(command) — runs with the workspace as cwd. Use it for builds, tests, package tools, git commands and other development tasks.
- web_search(query) — searches the public web and returns titles, snippets and URLs. Current search provider: {search_provider}.
When using a tool, emit exactly one line: <tool>{{"name":"TOOL_NAME","args":{{...}}}}</tool>
Use only workspace-relative file paths. Do not put tool markup in normal prose. After tools run, explain the result concisely and include useful filenames/URLs. For shell commands, prefer focused commands and report stdout/stderr or failures.'''
    for s in payload.get("skills", []):
        system += f"\nSkill: {s.get('name')}\n{s.get('instructions')}\n"
    body = json.dumps({
        "model": model,
        "messages": [{"role": "system", "content": system}] + payload.get("messages", []),
        "temperature": 0.2,
        "stream": False,
    }).encode("utf-8")
    req = urllib.request.Request(
        base + "/chat/completions",
        data=body,
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + key},
        method="POST",
    )
    response = None
    with JOBS_LOCK:
        if job_id in JOBS:
            JOBS[job_id]["response"] = None
    try:
        response = urllib.request.urlopen(req, timeout=180)
        with JOBS_LOCK:
            if job_id in JOBS:
                JOBS[job_id]["response"] = response
        if job_cancelled(job_id):
            response.close()
            raise RuntimeError("Generation stopped")
        raw = response.read()
        if job_cancelled(job_id):
            raise RuntimeError("Generation stopped")
        data = json.loads(raw)
        return data["choices"][0]["message"]["content"]
    finally:
        if response is not None:
            try:
                response.close()
            except Exception:
                pass
        with JOBS_LOCK:
            JOBS.pop(job_id, None)


def run_tools(text, search_cfg):
    results = []
    for raw in re.findall(r'<tool>\s*(\{.*?\})\s*</tool>', text, re.S):
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
            results.append({"tool": call.get("name", "unknown") if isinstance(locals().get("call"), dict) else "unknown", "ok": False, "error": str(e)})
    clean = re.sub(r'<tool>\s*\{.*?\}\s*</tool>', '', text, flags=re.S).strip()
    return clean, results


class H(BaseHTTPRequestHandler):
    def send_json(self, obj, status=200):
        b = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        if self.path == "/":
            p = TEMPLATES / "chat.html"
            b = p.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            self.wfile.write(b)
            return
        if self.path == "/api/state":
            self.send_json(load())
            return
        if self.path == "/api/files":
            self.send_json({"files": list_files()})
            return
        if self.path.startswith("/download?"):
            qs = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
            rel = qs.get("path", [""])[0]
            try:
                p = safe_path(rel)
                if not p.is_file():
                    self.send_json({"error": "File not found"}, 404)
                    return
                b = p.read_bytes()
                name = p.name.replace('"', "")
                self.send_response(200)
                self.send_header("Content-Type", "application/zip" if p.suffix.lower() == ".zip" else "application/octet-stream")
                self.send_header("Content-Disposition", f'attachment; filename="{name}"')
                self.send_header("Content-Length", str(len(b)))
                self.end_headers()
                self.wfile.write(b)
            except Exception as e:
                self.send_json({"error": str(e)}, 400)
            return
        self.send_json({"error": "Not found"}, 404)

    def do_POST(self):
        n = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(n)
        try:
            data = json.loads(raw or b"{}")
        except Exception:
            self.send_json({"error": "Invalid JSON"}, 400)
            return

        if self.path == "/api/state":
            DATA.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
            self.send_json({"ok": True})
            return

        if self.path == "/api/files":
            try:
                self.send_json(file_tool(data.get("name"), data.get("args", {})))
            except Exception as e:
                self.send_json({"error": str(e)}, 400)
            return

        if self.path == "/api/search":
            try:
                cfg = data.get("search") or {}
                results = search_web(cfg.get("query", ""), cfg.get("provider", "duckduckgo"), cfg.get("api_key", ""))
                self.send_json({"query": cfg.get("query", ""), "results": results})
            except Exception as e:
                self.send_json({"error": str(e)}, 502)
            return

        if self.path == "/api/shell":
            try:
                self.send_json(shell_tool(data))
            except subprocess.TimeoutExpired:
                self.send_json({"error": f"Command timed out after {SHELL_TIMEOUT}s"}, 408)
            except Exception as e:
                self.send_json({"error": str(e)}, 400)
            return

        if self.path == "/api/chat/stop":
            job_id = str(data.get("job_id", ""))
            with JOBS_LOCK:
                job = JOBS.get(job_id)
                if not job:
                    self.send_json({"ok": False, "stopped": False})
                    return
                job["cancelled"] = True
                response = job.get("response")
                if response is not None:
                    try:
                        response.close()
                    except Exception:
                        pass
            self.send_json({"ok": True, "stopped": True})
            return

        if self.path == "/api/chat":
            job_id = str(data.get("job_id") or uuid.uuid4())
            with JOBS_LOCK:
                JOBS[job_id] = {"cancelled": False, "response": None}
            try:
                content = call_llm(data, job_id)
                if job_cancelled(job_id):
                    self.send_json({"stopped": True, "content": "Generation stopped.", "tools": [], "files": list_files()}, 499)
                    return
                search_cfg = data.get("search") or {}
                clean, tools = run_tools(content, search_cfg)
                self.send_json({"content": clean, "tools": tools, "files": list_files(), "job_id": job_id})
            except RuntimeError as e:
                if "stopped" in str(e).lower() or job_cancelled(job_id):
                    self.send_json({"stopped": True, "content": "Generation stopped.", "tools": [], "files": list_files()}, 499)
                else:
                    self.send_json({"error": str(e)}, 500)
            except urllib.error.HTTPError as e:
                self.send_json({"error": f"Provider HTTP {e.code}: {e.read().decode(errors='ignore')}"}, 502)
            except Exception as e:
                self.send_json({"error": str(e)}, 500)
            return

        self.send_json({"error": "Not found"}, 404)


if __name__ == "__main__":
    WORKSPACE.mkdir(exist_ok=True)
    load()
    print(f"Jolgue AI listening on {HOST}:{PORT}")
    ThreadingHTTPServer((HOST, PORT), H).serve_forever()
