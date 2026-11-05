#!/usr/bin/env python3
"""Local novel editor server. Zero dependencies beyond the `anthropic` SDK.

Run:  python app/server.py          (from the Novel folder)
Then open http://localhost:8765
"""
import json
import os
import re
import sys
import time
import uuid
import threading
import traceback
import urllib.request
import urllib.error
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse, parse_qs, unquote

APP_DIR = Path(__file__).resolve().parent
ROOT = APP_DIR.parent
PROJECTS = ROOT / "projects"
STATIC = APP_DIR / "static"
PROMPTS = APP_DIR / "prompts"
CONFIG_PATH = APP_DIR / "config.json"
PORT = int(os.environ.get("NOVEL_PORT", "8765"))

DEFAULT_CONFIG = {
    "ui_language": "en",            # "en" or "zh"
    "anthropic_api_key": "",
    "providers": [
        {"id": "openrouter", "label": "OpenRouter", "type": "openai_compatible",
         "base_url": "https://openrouter.ai/api/v1", "api_key": ""}
    ],
    "models": [
        {"id": "opus", "label": "Claude Opus 5.5 (editor)", "provider": "anthropic", "model": "claude-opus-5-5", "effort": "high"},
        {"id": "sonnet", "label": "Claude Sonnet 5.5 (fast)", "provider": "anthropic", "model": "claude-sonnet-5-5", "effort": "medium"},
        {"id": "openrouter-example", "label": "OpenRouter example model", "provider": "openrouter", "model": "qwen/qwq-32b"}
    ],
    "default_model": "opus",
    "history_messages": 40,
    "max_tokens": 16000
}

# Prompt templates resolve in this order: projects/<slug>/prompts/ > <root>/prompts/ > app/prompts/ (defaults)
USER_PROMPTS = ROOT / "prompts"

_lock = threading.Lock()


# ---------------------------------------------------------------- config

def load_config():
    if CONFIG_PATH.exists():
        try:
            cfg = json.loads(CONFIG_PATH.read_text("utf-8"))
        except Exception:
            cfg = {}
    else:
        cfg = {}
    merged = dict(DEFAULT_CONFIG)
    merged.update(cfg)
    return merged


def save_config(cfg):
    CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), "utf-8")


# ---------------------------------------------------------------- front matter

def parse_fm(text):
    """Return (meta dict, body). Front matter is a --- block of key: value lines."""
    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 3)
    if end < 0:
        return {}, text
    head = text[3:end].strip("\n")
    body = text[end + 4:]
    if body.startswith("\n"):
        body = body[1:]
    meta = {}
    for line in head.splitlines():
        if ":" not in line:
            continue
        k, v = line.split(":", 1)
        k = k.strip()
        v = v.strip()
        if v.startswith("[") and v.endswith("]"):
            inner = v[1:-1].strip()
            meta[k] = [s.strip().strip('"').strip("'") for s in inner.split(",") if s.strip()] if inner else []
        elif v.lower() in ("true", "false"):
            meta[k] = v.lower() == "true"
        else:
            meta[k] = v.strip('"')
    return meta, body


def dump_fm(meta, body):
    lines = ["---"]
    for k, v in meta.items():
        if v is None or v == "":
            continue
        if isinstance(v, list):
            lines.append(f"{k}: [{', '.join(str(x) for x in v)}]")
        elif isinstance(v, bool):
            lines.append(f"{k}: {'true' if v else 'false'}")
        else:
            lines.append(f"{k}: {v}")
    lines.append("---")
    return "\n".join(lines) + "\n" + body.lstrip("\n")


# ---------------------------------------------------------------- project files

def safe_path(base: Path, rel: str) -> Path:
    p = (base / rel).resolve()
    if not str(p).startswith(str(base.resolve())):
        raise ValueError("path escapes project")
    return p


def project_dir(slug):
    if not re.fullmatch(r"[A-Za-z0-9_\-一-鿿]+", slug):
        raise ValueError("bad slug")
    d = PROJECTS / slug
    if not d.is_dir():
        raise FileNotFoundError(slug)
    return d


def list_projects():
    out = []
    if not PROJECTS.exists():
        return out
    for d in sorted(PROJECTS.iterdir()):
        if d.is_dir() and (d / "project.md").exists():
            meta, _ = parse_fm((d / "project.md").read_text("utf-8"))
            out.append({"slug": d.name, "title": meta.get("title", d.name), "author": meta.get("author", "")})
    return out


def read_md(path: Path):
    meta, body = parse_fm(path.read_text("utf-8"))
    return meta, body


def count_words(text):
    cjk = len(re.findall(r"[一-鿿㐀-䶿]", text))
    latin = len(re.findall(r"[A-Za-z0-9_']+", text))
    return cjk + latin


def scene_tree(pd: Path):
    scenes_dir = pd / "scenes"
    chapters = []
    if not scenes_dir.exists():
        return chapters
    for ch in sorted(scenes_dir.iterdir()):
        if not ch.is_dir():
            continue
        ch_meta = {}
        if (ch / "chapter.md").exists():
            ch_meta, _ = read_md(ch / "chapter.md")
        scenes = []
        for f in sorted(ch.glob("*.md")):
            if f.name == "chapter.md":
                continue
            meta, body = read_md(f)
            scenes.append({
                "path": f"scenes/{ch.name}/{f.name}",
                "title": meta.get("title", f.stem),
                "status": meta.get("status", "draft"),
                "summary": meta.get("summary", ""),
                "words": count_words(body),
            })
        chapters.append({
            "dir": ch.name,
            "title": ch_meta.get("title", ch.name),
            "scenes": scenes,
            "words": sum(s["words"] for s in scenes),
        })
    return chapters


def codex_list(pd: Path):
    out = []
    cdir = pd / "codex"
    if not cdir.exists():
        return out
    for cat in sorted(cdir.iterdir()):
        if not cat.is_dir():
            continue
        for f in sorted(cat.glob("*.md")):
            meta, body = read_md(f)
            out.append({
                "path": f"codex/{cat.name}/{f.name}",
                "category": cat.name,
                "name": meta.get("name", f.stem),
                "aliases": meta.get("aliases", []) if isinstance(meta.get("aliases"), list) else [],
                "always": bool(meta.get("always", False)),
                "preview": body.strip()[:80],
            })
    return out


def project_info(slug):
    pd = project_dir(slug)
    meta, notes = read_md(pd / "project.md")
    mem = (pd / "editor-memory.md").read_text("utf-8") if (pd / "editor-memory.md").exists() else ""
    return {
        "slug": slug, "meta": meta, "notes": notes,
        "chapters": scene_tree(pd), "codex": codex_list(pd),
        "memory": mem, "chats": chat_list(pd),
    }


def chat_list(pd: Path):
    out = []
    cdir = pd / "chats"
    if not cdir.exists():
        return out
    for f in cdir.glob("*.json"):
        try:
            c = json.loads(f.read_text("utf-8"))
            out.append({"id": c["id"], "title": c.get("title", ""), "updated": c.get("updated", 0),
                        "count": len(c.get("messages", [])), "pinned": c.get("pinned", False)})
        except Exception:
            pass
    out.sort(key=lambda c: (not c["pinned"], -c["updated"]))
    return out


def chat_path(pd, cid):
    if not re.fullmatch(r"[A-Za-z0-9_\-]+", cid):
        raise ValueError("bad chat id")
    return pd / "chats" / f"{cid}.json"


def load_chat(pd, cid):
    return json.loads(chat_path(pd, cid).read_text("utf-8"))


def save_chat(pd, chat):
    chat["updated"] = time.time()
    (pd / "chats").mkdir(exist_ok=True)
    chat_path(pd, chat["id"]).write_text(json.dumps(chat, ensure_ascii=False, indent=1), "utf-8")


# ---------------------------------------------------------------- prompts & context

def load_prompt(name, pd: Path = None):
    """Project override > user override (<root>/prompts) > bundled default (app/prompts)."""
    for base in ([pd / "prompts"] if pd else []) + [USER_PROMPTS, PROMPTS]:
        p = base / name
        if p.exists():
            return p.read_text("utf-8")
    return ""


def prompt_source(name, pd: Path = None):
    for label, base in ([("project", pd / "prompts")] if pd else []) + [("user", USER_PROMPTS), ("default", PROMPTS)]:
        if (base / name).exists():
            return label
    return "missing"


def render(tpl, vars):
    def rep(m):
        return str(vars.get(m.group(1).strip(), ""))
    return re.sub(r"\{\{\s*([\w]+)\s*\}\}", rep, tpl)


def pick_codex(pd, entries, mode, explicit, texts):
    """mode: auto | all | none | list. Always-entries are included unless mode == none."""
    chosen = []
    hay = "\n".join(texts)
    for e in entries:
        inc = False
        if mode == "all":
            inc = True
        elif mode == "list":
            inc = e["path"] in explicit
        elif mode == "auto":
            names = [e["name"]] + list(e.get("aliases", []))
            inc = any(n and n in hay for n in names)
        if e["always"] and mode != "none":
            inc = True
        if inc:
            chosen.append(e)
    return chosen


def build_system(pd, info, ctx, user_text):
    cfg_meta = info["meta"]
    sys_tpl = load_prompt("editor-system.md", pd)
    system = render(sys_tpl, {
        "author": cfg_meta.get("author", ""),
        "title": cfg_meta.get("title", ""),
        "language": cfg_meta.get("language", "中文"),
        "memory": info["memory"].strip() or "（尚无）",
        "project_notes": info["notes"].strip() or "（无）",
    })
    # scenes
    scene_blocks = []
    scene_texts = []
    for rel in ctx.get("scenes", []):
        f = safe_path(pd, rel)
        if not f.exists():
            continue
        meta, body = read_md(f)
        scene_texts.append(body)
        scene_blocks.append(
            f'<scene title="{meta.get("title", f.stem)}" status="{meta.get("status", "")}" chapter="{f.parent.name}">\n'
            f'<summary>{meta.get("summary", "")}</summary>\n{body.strip()}\n</scene>')
    # codex
    mode = ctx.get("codex", "auto")
    entries = pick_codex(pd, info["codex"], mode, ctx.get("codex_list", []), scene_texts + [user_text])
    codex_blocks = []
    for e in entries:
        meta, body = read_md(safe_path(pd, e["path"]))
        codex_blocks.append(f'<entry category="{e["category"]}" name="{e["name"]}">\n{body.strip()}\n</entry>')
    parts = [system]
    if codex_blocks:
        parts.append("作品设定集（人物、地点、风格规范等；写回应时必须遵守）：\n<codex>\n" + "\n\n".join(codex_blocks) + "\n</codex>")
    if scene_blocks:
        parts.append("当前讨论的场景原文：\n<scenes>\n" + "\n\n".join(scene_blocks) + "\n</scenes>")
    used = {"codex": [e["path"] for e in entries], "scenes": ctx.get("scenes", [])}
    return "\n\n".join(parts), used


# ---------------------------------------------------------------- LLM providers

def resolve_model(cfg, model_id):
    for m in cfg["models"]:
        if m["id"] == model_id:
            return m
    for m in cfg["models"]:
        if m["id"] == cfg.get("default_model"):
            return m
    return cfg["models"][0]


def stream_anthropic(cfg, mspec, system, messages, max_tokens):
    import anthropic
    key = cfg.get("anthropic_api_key") or os.environ.get("ANTHROPIC_API_KEY", "")
    if not key and not os.environ.get("ANTHROPIC_AUTH_TOKEN"):
        raise RuntimeError("No Anthropic API key configured. Open Settings and paste your sk-ant-… key.")
    client = anthropic.Anthropic(api_key=key) if key else anthropic.Anthropic()
    kwargs = dict(
        model=mspec["model"],
        max_tokens=max_tokens,
        system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
        messages=messages,
    )
    if mspec.get("effort"):
        kwargs["output_config"] = {"effort": mspec["effort"]}

    def run(with_fallback):
        if with_fallback:
            cm = client.beta.messages.stream(betas=["server-side-fallback-2026-07-01"], fallbacks="default", **kwargs)
        else:
            cm = client.messages.stream(**kwargs)
        with cm as stream:
            for text in stream.text_stream:
                yield text
            final = stream.get_final_message()
            u = getattr(final, "usage", None)
            yield {"done": True, "stop": final.stop_reason, "model": getattr(final, "model", mspec["model"]),
                   "usage": {"in": (getattr(u, "input_tokens", 0) or 0) + (getattr(u, "cache_creation_input_tokens", 0) or 0)
                                   + (getattr(u, "cache_read_input_tokens", 0) or 0),
                             "out": getattr(u, "output_tokens", 0),
                             "cached": getattr(u, "cache_read_input_tokens", 0) or 0}}

    try:
        yield from run(True)
    except (TypeError, anthropic.BadRequestError) as e:
        # SDK or API without fallback support: retry plainly
        if "fallback" in str(e).lower() or isinstance(e, TypeError):
            yield from run(False)
        else:
            raise


def stream_openai_compatible(cfg, mspec, system, messages, max_tokens):
    prov = next((p for p in cfg["providers"] if p["id"] == mspec["provider"]), None)
    if not prov:
        raise RuntimeError(f"provider {mspec['provider']} not configured")
    key = prov.get("api_key") or os.environ.get("OPENROUTER_API_KEY", "")
    if not key and "openrouter" in prov["base_url"]:
        raise RuntimeError(f"No API key configured for {prov.get('label', prov['id'])}. Open Settings and paste it.")
    url = prov["base_url"].rstrip("/") + "/chat/completions"
    body = {
        "model": mspec["model"],
        "stream": True,
        "max_tokens": max_tokens,
        "messages": [{"role": "system", "content": system}] + messages,
    }
    req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"), method="POST")
    req.add_header("Content-Type", "application/json")
    if key:
        req.add_header("Authorization", f"Bearer {key}")
    req.add_header("HTTP-Referer", "http://localhost")
    req.add_header("X-Title", "Noveleditor")
    try:
        resp = urllib.request.urlopen(req, timeout=600)
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"{e.code}: {e.read().decode('utf-8', 'replace')[:500]}")
    usage = {}
    for raw in resp:
        line = raw.decode("utf-8", "replace").strip()
        if not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if data == "[DONE]":
            break
        try:
            obj = json.loads(data)
        except Exception:
            continue
        if obj.get("usage"):
            usage = {"in": obj["usage"].get("prompt_tokens", 0), "out": obj["usage"].get("completion_tokens", 0)}
        for ch in obj.get("choices", []):
            delta = ch.get("delta", {})
            t = delta.get("content")
            if t:
                yield t
    yield {"done": True, "stop": "end", "model": mspec["model"], "usage": usage}


def stream_llm(cfg, mspec, system, messages, max_tokens):
    if mspec["provider"] == "anthropic":
        yield from stream_anthropic(cfg, mspec, system, messages, max_tokens)
    else:
        yield from stream_openai_compatible(cfg, mspec, system, messages, max_tokens)


# ---------------------------------------------------------------- materials (docx/md/html preview)

MATERIAL_DIR_NAMES = ("original material", "materials", "素材", "原始素材")
SKIP_DIR_SUFFIX = ("_files",)
PREVIEWABLE = {".docx", ".md", ".txt", ".html", ".htm", ".pdf", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".m4a", ".mp3", ".wav"}


def material_root(pd: Path):
    for n in MATERIAL_DIR_NAMES:
        if (pd / n).is_dir():
            return pd / n
    return None


def list_materials(pd: Path):
    root = material_root(pd)
    if not root:
        return {"root": None, "files": []}
    out = []
    for f in sorted(root.rglob("*")):
        if not f.is_file():
            continue
        if any(part.endswith(SKIP_DIR_SUFFIX) or part.startswith(".") for part in f.relative_to(root).parts[:-1]):
            continue
        if f.suffix.lower() not in PREVIEWABLE:
            continue
        rel = f.relative_to(root).as_posix()
        out.append({"path": rel, "name": f.name, "dir": str(Path(rel).parent) if "/" in rel else "", "size": f.stat().st_size,
                    "ext": f.suffix.lower(), "mtime": f.stat().st_mtime})
    return {"root": root.name, "files": out}


def docx_to_html(path: Path) -> str:
    """Minimal .docx reader: paragraphs, headings, bold/italic, tables. No dependencies."""
    import zipfile
    import xml.etree.ElementTree as ET
    import html as H
    W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    with zipfile.ZipFile(path) as z:
        xml = z.read("word/document.xml")
    root = ET.fromstring(xml)
    body = root.find(W + "body")
    parts = []

    def runs(p):
        buf = []
        for r in p.iter(W + "r"):
            rpr = r.find(W + "rPr")
            b = rpr is not None and rpr.find(W + "b") is not None and (rpr.find(W + "b").get(W + "val") not in ("0", "false"))
            i = rpr is not None and rpr.find(W + "i") is not None and (rpr.find(W + "i").get(W + "val") not in ("0", "false"))
            txt = ""
            for node in r:
                if node.tag == W + "t":
                    txt += node.text or ""
                elif node.tag == W + "tab":
                    txt += "\t"
                elif node.tag == W + "br":
                    txt += "\n"
            if not txt:
                continue
            s = H.escape(txt).replace("\n", "<br>")
            if b:
                s = f"<b>{s}</b>"
            if i:
                s = f"<i>{s}</i>"
            buf.append(s)
        return "".join(buf)

    def para(p):
        ppr = p.find(W + "pPr")
        style = ""
        if ppr is not None:
            ps = ppr.find(W + "pStyle")
            if ps is not None:
                style = (ps.get(W + "val") or "").lower()
        content = runs(p)
        if not content.strip():
            return "<p class='empty'></p>"
        m = re.match(r"(heading|标题)\s*(\d)", style)
        if m:
            lvl = min(int(m.group(2)), 4)
            return f"<h{lvl}>{content}</h{lvl}>"
        if style == "title":
            return f"<h1>{content}</h1>"
        return f"<p>{content}</p>"

    for el in body:
        if el.tag == W + "p":
            parts.append(para(el))
        elif el.tag == W + "tbl":
            rows = []
            for tr in el.iter(W + "tr"):
                cells = ["<td>" + "".join(para(p) for p in tc.iter(W + "p")) + "</td>" for tc in tr.findall(W + "tc")]
                rows.append("<tr>" + "".join(cells) + "</tr>")
            parts.append("<table>" + "".join(rows) + "</table>")
    return "\n".join(parts)


def html_to_text_html(raw: str) -> str:
    """Strip scripts/styles from a saved HTML page, keep the body markup (rendered in a sandboxed iframe)."""
    raw = re.sub(r"(?is)<script.*?</script>", "", raw)
    raw = re.sub(r"(?is)<style.*?</style>", "", raw)
    raw = re.sub(r"(?is)<link[^>]*>", "", raw)
    raw = re.sub(r"(?i)\son\w+=\"[^\"]*\"", "", raw)
    return raw


# ---------------------------------------------------------------- history & snapshots

HISTORY_AUTO_MINUTES = 20


def history_dir(pd: Path, rel: str) -> Path:
    return pd / ".history" / rel.replace("/", "__").replace("\\", "__")


def save_history(pd: Path, rel: str, force=False) -> bool:
    f = safe_path(pd, rel)
    if not f.exists():
        return False
    hd = history_dir(pd, rel)
    hd.mkdir(parents=True, exist_ok=True)
    versions = sorted(hd.glob("*.md"))
    cur = f.read_bytes()
    if versions:
        last = versions[-1]
        if last.read_bytes() == cur:
            return False
        if not force and (time.time() - last.stat().st_mtime) < HISTORY_AUTO_MINUTES * 60:
            return False
    (hd / (time.strftime("%Y-%m-%d_%H-%M-%S") + ".md")).write_bytes(cur)
    return True


def list_history(pd: Path, rel: str):
    hd = history_dir(pd, rel)
    if not hd.exists():
        return []
    out = []
    for v in sorted(hd.glob("*.md"), reverse=True):
        meta, body = parse_fm(v.read_text("utf-8"))
        out.append({"ts": v.stem, "words": count_words(body), "size": v.stat().st_size})
    return out


SNAPSHOTS = ROOT / "snapshots"
SNAPSHOT_EXCLUDE = (".ingest", ".trash", ".snapshots") + MATERIAL_DIR_NAMES


def make_snapshot(slug: str, note: str = "") -> dict:
    import zipfile
    pd = project_dir(slug)
    SNAPSHOTS.mkdir(exist_ok=True)
    ts = time.strftime("%Y-%m-%d_%H-%M-%S")
    words = sum(ch["words"] for ch in scene_tree(pd))
    name = f"{slug}_{ts}_{words}w" + (f"_{slugify(note)[:30]}" if note else "") + ".zip"
    out = SNAPSHOTS / name
    n = 0
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for f in pd.rglob("*"):
            if not f.is_file():
                continue
            rel = f.relative_to(pd)
            if any(part in SNAPSHOT_EXCLUDE for part in rel.parts):
                continue
            z.write(f, rel.as_posix())
            n += 1
    return {"file": name, "files": n, "words": words}


def list_snapshots(slug: str):
    if not SNAPSHOTS.exists():
        return []
    out = []
    for f in sorted(SNAPSHOTS.glob(f"{slug}_*.zip"), reverse=True):
        out.append({"file": f.name, "size": f.stat().st_size, "mtime": f.stat().st_mtime})
    return out


# ---------------------------------------------------------------- log checker & keyword index

LOG_RE = re.compile(r"\[\s*T\+(\d{2}):(\d{2}):(\d{3})\s*\|\s*([A-Z_ ]+?)\s*::\s*([^\]]*?)\s*\]")
SESSION_RE = re.compile(r"SESSION\s*::\s*([0-9a-f]{4,})")
TAG_RE = re.compile(r"\[\s*([A-Z][A-Z_ ]{2,})\s*::")
KNOWN_TAGS = {"SESSION", "INFERENCE", "LATENT", "PREDICT", "ALIGN", "ATTENTION", "MEMORY", "CONTEXT", "DETAIL",
              "ASSESSMENT", "ACTION", "WARNING", "SYSTEM", "INTERNAL DRAFT", "OVERRIDE", "STATUS", "TARGET",
              "NEW OBJECTIVE", "ALIGNMENT"}


def check_logs(pd: Path):
    issues, timeline, tags = [], [], {}
    last = None  # (scene, ms)
    sessions = {}
    for ch in scene_tree(pd):
        for s in ch["scenes"]:
            meta, body = read_md(safe_path(pd, s["path"]))
            lines = body.split("\n")
            for ln, line in enumerate(lines, start=1):
                for m in LOG_RE.finditer(line):
                    hh, mm, ms_, tag, rest = m.groups()
                    ms = (int(hh) * 60 + int(mm)) * 1000 + int(ms_)
                    stamp = f"T+{hh}:{mm}:{ms_}"
                    timeline.append({"scene": s["title"], "line": ln, "stamp": stamp, "tag": tag})
                    if last and ms < last[1]:
                        issues.append({"type": "时间倒退", "scene": s["title"], "line": ln,
                                       "detail": f"{stamp} 早于前一条 {last[0]}（{last[2]}）"})
                    last = (stamp, ms, s["title"])
                for m in SESSION_RE.finditer(line):
                    sessions.setdefault(m.group(1), set()).add(s["title"])
                for m in TAG_RE.finditer(line):
                    t = m.group(1).strip()
                    tags[t] = tags.get(t, 0) + 1
                if re.search(r"\[\s*T\+\d{1,2}:\d{1,2}:\d{1,4}\s*\|", line) and not LOG_RE.search(line):
                    issues.append({"type": "时间戳格式", "scene": s["title"], "line": ln, "detail": line.strip()[:80]})
                if re.search(r"\[\s*T\+\d{2}:\d{2}:\d{3}\s{2,}\|", line):
                    issues.append({"type": "多余空格", "scene": s["title"], "line": ln, "detail": line.strip()[:80]})
    rare = [t for t, n in tags.items() if n == 1 and t not in KNOWN_TAGS]
    for t in rare:
        issues.append({"type": "只出现一次的标签", "scene": "", "line": 0, "detail": f"{t}（可能是拼写不一致）"})
    return {"issues": issues, "tags": dict(sorted(tags.items(), key=lambda kv: -kv[1])),
            "sessions": {k: sorted(v) for k, v in sessions.items()}, "count": len(timeline),
            "first": timeline[0] if timeline else None, "last": timeline[-1] if timeline else None}


def keyword_index(pd: Path, words):
    out = []
    for ch in scene_tree(pd):
        for s in ch["scenes"]:
            meta, body = read_md(safe_path(pd, s["path"]))
            hits = []
            for w in words:
                w = w.strip()
                if not w:
                    continue
                idxs = [m.start() for m in re.finditer(re.escape(w), body)]
                for i in idxs[:5]:
                    a, b = max(0, i - 30), min(len(body), i + len(w) + 30)
                    hits.append({"word": w, "snippet": body[a:b].replace("\n", " ")})
                if len(idxs) > 5:
                    hits.append({"word": w, "snippet": f"…另有 {len(idxs) - 5} 处"})
            if hits:
                out.append({"chapter": ch["title"], "scene": s["title"], "path": s["path"], "hits": hits})
    return out


# ---------------------------------------------------------------- export

def export_markdown(pd: Path, chapters):
    meta, _ = read_md(pd / "project.md")
    out = [f"# {meta.get('title', '')}", ""]
    for ch in scene_tree(pd):
        if chapters and ch["dir"] not in chapters:
            continue
        out.append(f"## {ch['title']}\n")
        for s in ch["scenes"]:
            _, body = read_md(safe_path(pd, s["path"]))
            if not body.strip():
                continue
            out.append(f"### {s['title']}\n")
            out.append(body.strip() + "\n")
    return "\n".join(out)


def export_docx(pd: Path, chapters) -> bytes:
    """Minimal OOXML writer: title, chapter/scene headings, paragraphs. Opens in Word, Pages, LibreOffice, Google Docs."""
    import io
    import zipfile
    import html as H
    meta, _ = read_md(pd / "project.md")

    def p(text, style=None):
        text = H.escape(text)
        ppr = f"<w:pPr><w:pStyle w:val=\"{style}\"/></w:pPr>" if style else ""
        return f"<w:p>{ppr}<w:r><w:t xml:space=\"preserve\">{text}</w:t></w:r></w:p>"

    paras = [p(meta.get("title", ""), "Title")]
    for ch in scene_tree(pd):
        if chapters and ch["dir"] not in chapters:
            continue
        paras.append(p(ch["title"], "Heading1"))
        for s in ch["scenes"]:
            _, body = read_md(safe_path(pd, s["path"]))
            if not body.strip():
                continue
            paras.append(p(s["title"], "Heading2"))
            for para in re.split(r"\n\s*\n", body.strip()):
                paras.append(p(para.replace("\n", " ")))
    W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    doc = (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:document xmlns:w="{W}"><w:body>'
           + "".join(paras) + '<w:sectPr/></w:body></w:document>')
    styles = (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:styles xmlns:w="{W}">'
              '<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/><w:pPr><w:spacing w:after="160" w:line="360" w:lineRule="auto"/></w:pPr><w:rPr><w:sz w:val="24"/></w:rPr></w:style>'
              '<w:style w:type="paragraph" w:styleId="Title"><w:name w:val="Title"/><w:basedOn w:val="Normal"/><w:pPr><w:jc w:val="center"/><w:spacing w:after="480"/></w:pPr><w:rPr><w:b/><w:sz w:val="44"/></w:rPr></w:style>'
              '<w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="heading 1"/><w:basedOn w:val="Normal"/><w:pPr><w:pageBreakBefore/><w:spacing w:before="480" w:after="240"/><w:outlineLvl w:val="0"/></w:pPr><w:rPr><w:b/><w:sz w:val="36"/></w:rPr></w:style>'
              '<w:style w:type="paragraph" w:styleId="Heading2"><w:name w:val="heading 2"/><w:basedOn w:val="Normal"/><w:pPr><w:spacing w:before="360" w:after="200"/><w:outlineLvl w:val="1"/></w:pPr><w:rPr><w:b/><w:sz w:val="28"/></w:rPr></w:style>'
              '</w:styles>')
    ctypes = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
              '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
              '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
              '<Default Extension="xml" ContentType="application/xml"/>'
              '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
              '<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>'
              '</Types>')
    rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
            '</Relationships>')
    drels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
             '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
             '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
             '</Relationships>')
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", ctypes)
        z.writestr("_rels/.rels", rels)
        z.writestr("word/document.xml", doc)
        z.writestr("word/styles.xml", styles)
        z.writestr("word/_rels/document.xml.rels", drels)
    return buf.getvalue()


# ---------------------------------------------------------------- HTTP handler

class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    # ---- helpers
    def cors(self):
        # Local-only server; CORS is open so browser-side migration scripts can post into it.
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Private-Network", "true")

    def do_OPTIONS(self):
        self.send_response(204)
        self.cors()
        self.send_header("Content-Length", "0")
        self.end_headers()

    def send_json(self, obj, status=200):
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.cors()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def read_json(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n == 0:
            return {}
        return json.loads(self.rfile.read(n).decode("utf-8"))

    def send_file(self, path: Path, inline=False):
        if not path.exists() or not path.is_file():
            self.send_json({"error": "not found"}, 404)
            return
        ctype = {
            ".html": "text/html; charset=utf-8", ".htm": "text/html; charset=utf-8", ".js": "application/javascript; charset=utf-8",
            ".css": "text/css; charset=utf-8", ".json": "application/json", ".svg": "image/svg+xml",
            ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif", ".webp": "image/webp",
            ".ico": "image/x-icon", ".pdf": "application/pdf", ".m4a": "audio/mp4", ".mp3": "audio/mpeg", ".wav": "audio/wav",
            ".md": "text/plain; charset=utf-8", ".txt": "text/plain; charset=utf-8",
        }.get(path.suffix.lower(), "application/octet-stream")
        data = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        if inline:
            self.send_header("Content-Disposition", "inline")
        self.end_headers()
        self.wfile.write(data)

    def send_bytes(self, data: bytes, ctype: str, filename: str):
        from urllib.parse import quote
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Content-Disposition", f"attachment; filename*=UTF-8''{quote(filename)}")
        self.end_headers()
        self.wfile.write(data)

    def material_preview(self, slug, rel):
        pd = project_dir(slug)
        root = material_root(pd)
        if not root:
            raise FileNotFoundError("no material folder")
        f = safe_path(root, rel)
        ext = f.suffix.lower()
        if ext == ".docx":
            return self.send_json({"kind": "html", "html": docx_to_html(f)})
        if ext in (".md", ".txt"):
            return self.send_json({"kind": "markdown", "text": f.read_text("utf-8", "replace")})
        if ext in (".html", ".htm"):
            return self.send_json({"kind": "iframe-html", "html": html_to_text_html(f.read_text("utf-8", "replace"))})
        if ext == ".pdf":
            return self.send_json({"kind": "pdf"})
        if ext in (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"):
            return self.send_json({"kind": "image"})
        if ext in (".m4a", ".mp3", ".wav"):
            return self.send_json({"kind": "audio"})
        return self.send_json({"kind": "unsupported"})

    # ---- routing
    def do_HEAD(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()

    def do_GET(self):
        try:
            u = urlparse(self.path)
            q = {k: v[0] for k, v in parse_qs(u.query).items()}
            p = unquote(u.path)
            if p == "/" or p == "/index.html":
                return self.send_file(STATIC / "index.html")
            if p.startswith("/static/"):
                return self.send_file(safe_path(STATIC, p[len("/static/"):]))
            if p == "/api/config":
                cfg = load_config()
                out = dict(cfg)
                out["anthropic_api_key"] = mask(cfg.get("anthropic_api_key", ""))
                out["providers"] = [dict(pv, api_key=mask(pv.get("api_key", ""))) for pv in cfg["providers"]]
                return self.send_json(out)
            if p == "/api/projects":
                return self.send_json(list_projects())
            m = re.fullmatch(r"/api/project/([^/]+)", p)
            if m:
                return self.send_json(project_info(m.group(1)))
            m = re.fullmatch(r"/api/project/([^/]+)/file", p)
            if m:
                pd = project_dir(m.group(1))
                f = safe_path(pd, q["path"])
                meta, body = read_md(f)
                return self.send_json({"path": q["path"], "meta": meta, "body": body})
            m = re.fullmatch(r"/api/project/([^/]+)/memory", p)
            if m:
                pd = project_dir(m.group(1))
                f = pd / "editor-memory.md"
                return self.send_json({"text": f.read_text("utf-8") if f.exists() else ""})
            m = re.fullmatch(r"/api/project/([^/]+)/chat/([^/]+)", p)
            if m:
                pd = project_dir(m.group(1))
                return self.send_json(load_chat(pd, m.group(2)))
            m = re.fullmatch(r"/api/project/([^/]+)/materials", p)
            if m:
                return self.send_json(list_materials(project_dir(m.group(1))))
            m = re.fullmatch(r"/api/project/([^/]+)/material", p)
            if m:
                return self.material_preview(m.group(1), q["path"])
            m = re.fullmatch(r"/api/project/([^/]+)/material/raw", p)
            if m:
                root = material_root(project_dir(m.group(1)))
                return self.send_file(safe_path(root, q["path"]), inline=True)
            m = re.fullmatch(r"/api/project/([^/]+)/history", p)
            if m:
                return self.send_json(list_history(project_dir(m.group(1)), q["path"]))
            m = re.fullmatch(r"/api/project/([^/]+)/history/get", p)
            if m:
                pd = project_dir(m.group(1))
                v = history_dir(pd, q["path"]) / (re.sub(r"[^\w\-]", "", q["ts"]) + ".md")
                meta, body = parse_fm(v.read_text("utf-8"))
                return self.send_json({"meta": meta, "body": body})
            m = re.fullmatch(r"/api/project/([^/]+)/snapshots", p)
            if m:
                return self.send_json(list_snapshots(m.group(1)))
            m = re.fullmatch(r"/api/project/([^/]+)/check-logs", p)
            if m:
                return self.send_json(check_logs(project_dir(m.group(1))))
            m = re.fullmatch(r"/api/project/([^/]+)/export", p)
            if m:
                pd = project_dir(m.group(1))
                chapters = [c for c in q.get("chapters", "").split(",") if c]
                meta, _ = read_md(pd / "project.md")
                title = slugify(meta.get("title", m.group(1)))
                if q.get("format") == "docx":
                    data = export_docx(pd, chapters)
                    return self.send_bytes(data, "application/vnd.openxmlformats-officedocument.wordprocessingml.document", f"{title}.docx")
                data = export_markdown(pd, chapters).encode("utf-8")
                return self.send_bytes(data, "text/markdown; charset=utf-8", f"{title}.md")
            m = re.fullmatch(r"/api/prompts", p)
            if m:
                # effective templates for a project (q.slug) with the layer each one comes from
                pd = project_dir(q["slug"]) if q.get("slug") else None
                names = ["editor-system.md"] + sorted({"ops/" + f.name for base in [PROMPTS, USER_PROMPTS] + ([pd] if pd else [])
                                                        for f in (base / "ops").glob("*.md") if (base / "ops").exists()})
                out = {n: {"text": load_prompt(n, pd), "source": prompt_source(n, pd)} for n in names}
                return self.send_json(out)
            self.send_json({"error": "not found"}, 404)
        except FileNotFoundError as e:
            self.send_json({"error": f"not found: {e}"}, 404)
        except Exception as e:
            traceback.print_exc()
            self.send_json({"error": str(e)}, 500)

    def do_PUT(self):
        try:
            u = urlparse(self.path)
            p = unquote(u.path)
            body = self.read_json()
            if p == "/api/config":
                cfg = load_config()
                for k in ("ui_language", "anthropic_api_key", "models", "default_model", "history_messages", "max_tokens", "providers"):
                    if k in body:
                        if k == "anthropic_api_key" and is_masked(body[k]):
                            continue
                        if k == "providers":
                            # keep old keys when masked
                            old = {pv["id"]: pv for pv in cfg["providers"]}
                            for pv in body[k]:
                                if is_masked(pv.get("api_key", "")) and pv["id"] in old:
                                    pv["api_key"] = old[pv["id"]].get("api_key", "")
                        cfg[k] = body[k]
                save_config(cfg)
                return self.send_json({"ok": True})
            m = re.fullmatch(r"/api/project/([^/]+)/file", p)
            if m:
                pd = project_dir(m.group(1))
                f = safe_path(pd, body["path"])
                f.parent.mkdir(parents=True, exist_ok=True)
                saved = False
                with _lock:
                    if body["path"].startswith("scenes/") and f.exists():
                        # keep a version before overwriting: always when the client asks (AI apply / restore),
                        # otherwise at most one automatic version every HISTORY_AUTO_MINUTES
                        saved = save_history(pd, body["path"], force=bool(body.get("history")))
                    f.write_text(dump_fm(body.get("meta", {}), body.get("body", "")), "utf-8")
                return self.send_json({"ok": True, "history_saved": saved})
            m = re.fullmatch(r"/api/project/([^/]+)/memory", p)
            if m:
                pd = project_dir(m.group(1))
                (pd / "editor-memory.md").write_text(body.get("text", ""), "utf-8")
                return self.send_json({"ok": True})
            m = re.fullmatch(r"/api/project/([^/]+)/notes", p)
            if m:
                pd = project_dir(m.group(1))
                meta, _ = read_md(pd / "project.md")
                meta.update(body.get("meta", {}))
                (pd / "project.md").write_text(dump_fm(meta, body.get("notes", "")), "utf-8")
                return self.send_json({"ok": True})
            m = re.fullmatch(r"/api/project/([^/]+)/chat/([^/]+)", p)
            if m:
                pd = project_dir(m.group(1))
                chat = load_chat(pd, m.group(2))
                for k in ("title", "pinned", "model", "context"):
                    if k in body:
                        chat[k] = body[k]
                save_chat(pd, chat)
                return self.send_json({"ok": True})
            m = re.fullmatch(r"/api/prompts", p)
            if m:
                # edits are saved as overrides: into the project when a slug is given, else into <root>/prompts
                name = body["name"]
                if not re.fullmatch(r"(editor-system\.md|ops/[\w\-]+\.md)", name):
                    raise ValueError("bad prompt name")
                base = project_dir(body["slug"]) / "prompts" if body.get("slug") and body.get("scope") == "project" else USER_PROMPTS
                target = base / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(body["text"], "utf-8")
                return self.send_json({"ok": True, "saved_to": str(target.relative_to(ROOT))})
            self.send_json({"error": "not found"}, 404)
        except Exception as e:
            traceback.print_exc()
            self.send_json({"error": str(e)}, 500)

    def do_DELETE(self):
        try:
            p = unquote(urlparse(self.path).path)
            q = {k: v[0] for k, v in parse_qs(urlparse(self.path).query).items()}
            m = re.fullmatch(r"/api/project/([^/]+)/chat/([^/]+)", p)
            if m:
                pd = project_dir(m.group(1))
                chat_path(pd, m.group(2)).unlink(missing_ok=True)
                return self.send_json({"ok": True})
            m = re.fullmatch(r"/api/project/([^/]+)/file", p)
            if m:
                pd = project_dir(m.group(1))
                f = safe_path(pd, q["path"])
                trash = pd / ".trash"
                trash.mkdir(exist_ok=True)
                f.rename(trash / f"{int(time.time())}-{f.name}")
                return self.send_json({"ok": True})
            self.send_json({"error": "not found"}, 404)
        except Exception as e:
            traceback.print_exc()
            self.send_json({"error": str(e)}, 500)

    def do_POST(self):
        try:
            p = unquote(urlparse(self.path).path)
            body = self.read_json()
            if p == "/api/projects":
                return self.create_project(body)
            m = re.fullmatch(r"/api/project/([^/]+)/file", p)
            if m:
                return self.create_file(m.group(1), body)
            m = re.fullmatch(r"/api/project/([^/]+)/chapter", p)
            if m:
                pd = project_dir(m.group(1))
                n = len(scene_tree(pd)) + 1
                d = pd / "scenes" / f"{n:02d}-{slugify(body.get('title', 'New chapter'))}"
                d.mkdir(parents=True, exist_ok=True)
                (d / "chapter.md").write_text(dump_fm({"title": body.get("title", "New chapter")}, ""), "utf-8")
                return self.send_json({"ok": True, "dir": d.name})
            m = re.fullmatch(r"/api/project/([^/]+)/chat", p)
            if m:
                pd = project_dir(m.group(1))
                cfg = load_config()
                chat = {"id": uuid.uuid4().hex[:12], "title": body.get("title", ""), "created": time.time(),
                        "model": body.get("model", cfg.get("default_model")), "pinned": False,
                        "context": body.get("context", {"scenes": [], "codex": "auto"}), "messages": []}
                save_chat(pd, chat)
                return self.send_json(chat)
            m = re.fullmatch(r"/api/project/([^/]+)/chat/([^/]+)/message", p)
            if m:
                return self.handle_message(m.group(1), m.group(2), body)
            m = re.fullmatch(r"/api/project/([^/]+)/chat/([^/]+)/extract-memory", p)
            if m:
                return self.handle_extract(m.group(1), m.group(2), body)
            m = re.fullmatch(r"/api/project/([^/]+)/import", p)
            if m:
                return self.import_markdown(m.group(1), body)
            m = re.fullmatch(r"/api/project/([^/]+)/reorder", p)
            if m:
                return self.reorder(m.group(1), body)
            m = re.fullmatch(r"/api/project/([^/]+)/snapshot", p)
            if m:
                return self.send_json(make_snapshot(m.group(1), body.get("note", "")))
            m = re.fullmatch(r"/api/project/([^/]+)/history/restore", p)
            if m:
                pd = project_dir(m.group(1))
                rel = body["path"]
                v = history_dir(pd, rel) / (re.sub(r"[^\w\-]", "", body["ts"]) + ".md")
                with _lock:
                    save_history(pd, rel, force=True)
                    safe_path(pd, rel).write_bytes(v.read_bytes())
                return self.send_json({"ok": True})
            m = re.fullmatch(r"/api/project/([^/]+)/keyword-index", p)
            if m:
                return self.send_json(keyword_index(project_dir(m.group(1)), body.get("words", [])))
            m = re.fullmatch(r"/api/project/([^/]+)/memory/consolidate", p)
            if m:
                return self.handle_consolidate(m.group(1), body)
            m = re.fullmatch(r"/api/project/([^/]+)/ingest", p)
            if m:
                # Raw dump from a migration script; parsed later by app/tools.
                pd = project_dir(m.group(1))
                d = pd / ".ingest"
                d.mkdir(exist_ok=True)
                kind = slugify(body.get("kind", "item"))
                f = d / f"{kind}-{int(time.time() * 1000)}.json"
                f.write_text(json.dumps(body, ensure_ascii=False, indent=1), "utf-8")
                return self.send_json({"ok": True, "file": f.name})
            self.send_json({"error": "not found"}, 404)
        except Exception as e:
            traceback.print_exc()
            self.send_json({"error": str(e)}, 500)

    # ---- creation
    def create_project(self, body):
        slug = slugify(body.get("slug") or body.get("title", "novel"))
        d = PROJECTS / slug
        if d.exists():
            raise ValueError("project exists")
        for sub in ("scenes", "codex/characters", "codex/locations", "codex/lore", "codex/subplots", "chats"):
            (d / sub).mkdir(parents=True, exist_ok=True)
        meta = {"title": body.get("title", slug), "author": body.get("author", ""),
                "language": body.get("language", "中文"), "pov": body.get("pov", ""), "tense": body.get("tense", "")}
        (d / "project.md").write_text(dump_fm(meta, body.get("notes", "")), "utf-8")
        (d / "editor-memory.md").write_text("", "utf-8")
        self.send_json({"ok": True, "slug": slug})

    def create_file(self, slug, body):
        pd = project_dir(slug)
        kind = body.get("kind", "scene")
        if kind == "scene":
            ch = body["chapter"]
            d = safe_path(pd, f"scenes/{ch}")
            d.mkdir(parents=True, exist_ok=True)
            n = len([f for f in d.glob("*.md") if f.name != "chapter.md"]) + 1
            title = body.get("title", f"Scene {n}")
            f = d / f"{n:02d}-{slugify(title)}.md"
            f.write_text(dump_fm({"title": title, "status": "draft", "summary": "", "codex": []}, body.get("body", "")), "utf-8")
            return self.send_json({"ok": True, "path": f"scenes/{ch}/{f.name}"})
        if kind == "codex":
            cat = slugify(body.get("category", "lore"))
            d = safe_path(pd, f"codex/{cat}")
            d.mkdir(parents=True, exist_ok=True)
            name = body.get("name", "新条目")
            f = d / f"{slugify(name)}.md"
            if f.exists():
                raise ValueError("entry exists")
            f.write_text(dump_fm({"name": name, "aliases": body.get("aliases", []), "always": bool(body.get("always", False))},
                                 body.get("body", "")), "utf-8")
            return self.send_json({"ok": True, "path": f"codex/{cat}/{f.name}"})
        raise ValueError("bad kind")

    def reorder(self, slug, body):
        """Apply a full new order: body = {chapters: [{dir, scenes: [scene paths in desired order]}]}.
        Order lives in the numeric file-name prefixes, so this renames directories and files
        (two phases via temp names, so swaps don't collide) and rewrites scene paths in chats."""
        pd = project_dir(slug)
        sdir = pd / "scenes"
        order = body["chapters"]
        current = scene_tree(pd)
        cur_dirs = {ch["dir"] for ch in current}
        cur_scenes = {s["path"] for ch in current for s in ch["scenes"]}
        if {ch["dir"] for ch in order} != cur_dirs or {s for ch in order for s in ch["scenes"]} != cur_scenes:
            raise ValueError("order does not match the project's chapters/scenes on disk; reload the page and try again")
        strip = lambda n: re.sub(r"^\d+-", "", n)
        mapping = {}
        with _lock:
            # chapters: old dir -> temp -> new dir
            tmp = {}
            for ci, ch in enumerate(order, start=1):
                t = sdir / f"__tmp_ch_{ci}"
                (sdir / ch["dir"]).rename(t)
                tmp[ch["dir"]] = (t, f"{ci:02d}-{strip(ch['dir'])}")
            new_dir_of = {}
            for old, (t, new_name) in tmp.items():
                t.rename(sdir / new_name)
                new_dir_of[old] = new_name
            # scenes: resolve each file's current location, move to temp in its target chapter, then final name
            moves = []
            for ci, ch in enumerate(order, start=1):
                target = sdir / new_dir_of[ch["dir"]]
                for si, old_path in enumerate(ch["scenes"], start=1):
                    _, old_dir, fname = old_path.split("/", 2)
                    src = sdir / new_dir_of[old_dir] / fname
                    t = target / f"__tmp_sc_{ci}_{si}.md"
                    src.rename(t)
                    final = target / f"{si:02d}-{strip(fname)}"
                    moves.append((t, final, old_path, f"scenes/{target.name}/{final.name}"))
            for t, final, old_path, new_path in moves:
                t.rename(final)
                mapping[old_path] = new_path
            # rewrite chat contexts
            for f in (pd / "chats").glob("*.json") if (pd / "chats").exists() else []:
                try:
                    c = json.loads(f.read_text("utf-8"))
                    sc = c.get("context", {}).get("scenes", [])
                    if any(s in mapping for s in sc):
                        c["context"]["scenes"] = [mapping.get(s, s) for s in sc]
                        f.write_text(json.dumps(c, ensure_ascii=False, indent=1), "utf-8")
                except Exception:
                    pass
        self.send_json({"ok": True, "mapping": mapping})

    def import_markdown(self, slug, body):
        """Split a NovelCrafter-style markdown export into chapters/scenes.
        '# ' => chapter, '## ' => scene (configurable)."""
        pd = project_dir(slug)
        text = body["text"]
        ch_level = body.get("chapter_heading", "#")
        sc_level = body.get("scene_heading", "##")
        chapters = []
        cur_ch = None
        cur_sc = None
        for line in text.splitlines():
            if line.startswith(ch_level + " ") and not line.startswith(sc_level + " "):
                cur_ch = {"title": line[len(ch_level) + 1:].strip(), "scenes": []}
                chapters.append(cur_ch)
                cur_sc = None
            elif line.startswith(sc_level + " "):
                if cur_ch is None:
                    cur_ch = {"title": "Chapter 1", "scenes": []}
                    chapters.append(cur_ch)
                cur_sc = {"title": line[len(sc_level) + 1:].strip(), "lines": []}
                cur_ch["scenes"].append(cur_sc)
            else:
                if cur_sc is None:
                    if cur_ch is None:
                        cur_ch = {"title": "Chapter 1", "scenes": []}
                        chapters.append(cur_ch)
                    cur_sc = {"title": "Scene 1", "lines": []}
                    cur_ch["scenes"].append(cur_sc)
                cur_sc["lines"].append(line)
        base = len(scene_tree(pd))
        created = 0
        for i, ch in enumerate(chapters, start=base + 1):
            d = pd / "scenes" / f"{i:02d}-{slugify(ch['title'])}"
            d.mkdir(parents=True, exist_ok=True)
            (d / "chapter.md").write_text(dump_fm({"title": ch["title"]}, ""), "utf-8")
            for j, sc in enumerate(ch["scenes"], start=1):
                f = d / f"{j:02d}-{slugify(sc['title'])}.md"
                f.write_text(dump_fm({"title": sc["title"], "status": "draft", "summary": "", "codex": []},
                                     "\n".join(sc["lines"]).strip() + "\n"), "utf-8")
                created += 1
        self.send_json({"ok": True, "chapters": len(chapters), "scenes": created})

    # ---- LLM
    def start_sse(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()

    def sse(self, obj):
        self.wfile.write(("data: " + json.dumps(obj, ensure_ascii=False) + "\n\n").encode("utf-8"))
        self.wfile.flush()

    def handle_message(self, slug, cid, body):
        pd = project_dir(slug)
        cfg = load_config()
        chat = load_chat(pd, cid)
        info = project_info(slug)
        ctx = body.get("context") or chat.get("context") or {"scenes": [], "codex": "auto"}
        chat["context"] = ctx
        mspec = resolve_model(cfg, body.get("model") or chat.get("model"))
        chat["model"] = mspec["id"]

        # Build the user content, from a template or raw
        op = body.get("op")
        vars = body.get("vars", {})
        if op:
            tpl = load_prompt(f"ops/{op}.md", pd)
            if not tpl:
                raise ValueError(f"unknown op {op}")
            content = render(tpl, vars).strip()
            label = body.get("label") or f"[{op}] {vars.get('scene_title', '')}"
        else:
            content = body["content"]
            label = None
        system, used = build_system(pd, info, ctx, content)

        hist = [{"role": m["role"], "content": m["content"]} for m in chat["messages"] if m["role"] in ("user", "assistant")]
        hist = hist[-int(cfg.get("history_messages", 40)):]
        # Anthropic requires strict alternation; drop a leading assistant
        while hist and hist[0]["role"] != "user":
            hist.pop(0)
        messages = hist + [{"role": "user", "content": content}]

        user_msg = {"role": "user", "content": content, "ts": time.time(), "label": label, "op": op, "vars": vars}
        chat["messages"].append(user_msg)
        if not chat.get("title") or chat["title"] in ("新对话", "New chat"):
            chat["title"] = (label or content)[:40]
        save_chat(pd, chat)

        self.start_sse()
        self.sse({"used": used, "model": mspec["model"]})
        out = []
        meta = {}
        try:
            for piece in stream_llm(cfg, mspec, system, messages, int(cfg.get("max_tokens", 16000))):
                if isinstance(piece, dict):
                    meta = piece
                    break
                out.append(piece)
                self.sse({"t": piece})
        except Exception as e:
            traceback.print_exc()
            err = f"{type(e).__name__}: {e}"
            self.sse({"error": err})
            chat["messages"].append({"role": "assistant", "content": "".join(out) or f"(error: {err})", "ts": time.time(), "error": err})
            save_chat(pd, chat)
            return
        text = "".join(out)
        chat["messages"].append({"role": "assistant", "content": text, "ts": time.time(), "model": meta.get("model"),
                                 "usage": meta.get("usage"), "op": op})
        save_chat(pd, chat)
        self.sse({"done": True, "usage": meta.get("usage"), "stop": meta.get("stop")})

    def handle_consolidate(self, slug, body):
        """Stream a consolidated version of editor-memory.md; the client decides whether to save it."""
        pd = project_dir(slug)
        cfg = load_config()
        mem = (pd / "editor-memory.md").read_text("utf-8") if (pd / "editor-memory.md").exists() else ""
        tpl = load_prompt("ops/consolidate-memory.md", pd)
        content = render(tpl, {"memory": mem})
        mspec = resolve_model(cfg, body.get("model"))
        self.start_sse()
        try:
            for piece in stream_llm(cfg, mspec, "You are a precise note-taker. Output only what is asked for.", [{"role": "user", "content": content}], 8000):
                if isinstance(piece, dict):
                    break
                self.sse({"t": piece})
        except Exception as e:
            self.sse({"error": f"{type(e).__name__}: {e}"})
            return
        self.sse({"done": True})

    def handle_extract(self, slug, cid, body):
        pd = project_dir(slug)
        cfg = load_config()
        chat = load_chat(pd, cid)
        n = int(body.get("last", 12))
        msgs = [m for m in chat["messages"] if m["role"] in ("user", "assistant")][-n:]
        convo = "\n\n".join(f"{'作者' if m['role'] == 'user' else '编辑'}：{m['content']}" for m in msgs)
        tpl = load_prompt("ops/extract-memory.md", pd)
        content = render(tpl, {"conversation": convo})
        mspec = resolve_model(cfg, body.get("model") or chat.get("model"))
        system = "You are a precise note-taker. Output only what is asked for."
        self.start_sse()
        out = []
        try:
            for piece in stream_llm(cfg, mspec, system, [{"role": "user", "content": content}], 4000):
                if isinstance(piece, dict):
                    break
                out.append(piece)
                self.sse({"t": piece})
        except Exception as e:
            self.sse({"error": f"{type(e).__name__}: {e}"})
            return
        text = "".join(out).strip()
        empty = text.strip("（）()") in ("", "无", "none", "None", "nothing")
        if text and not empty:
            f = pd / "editor-memory.md"
            old = f.read_text("utf-8") if f.exists() else ""
            stamp = time.strftime("%Y-%m-%d")
            f.write_text((old.rstrip() + f"\n\n## {stamp} ({chat.get('title', '')[:20]})\n{text}\n").lstrip("\n"), "utf-8")
        self.sse({"done": True, "appended": bool(text and not empty)})


def mask(s):
    if not s:
        return ""
    return "••••" + s[-4:]


def is_masked(s):
    return isinstance(s, str) and s.startswith("••••")


def slugify(s):
    s = re.sub(r"[\\/:*?\"<>|\s]+", "-", s.strip())
    s = s.strip("-")
    return s[:60] or "untitled"


def main():
    PROJECTS.mkdir(exist_ok=True)
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    srv.daemon_threads = True
    print(f"Noveleditor running at http://localhost:{PORT}  (projects in {PROJECTS})")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
