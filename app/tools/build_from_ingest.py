"""Turn raw .ingest/*.json dumps (from the NovelCrafter migration script) into scene and codex files.

Usage: python app/tools/build_from_ingest.py <project-slug>
"""
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from server import dump_fm, slugify, PROJECTS  # noqa: E402


def clean_text(t: str) -> str:
    t = t.replace("\r", "")
    t = re.sub(r"^\s*Included in AI\s*$", "", t, flags=re.M)
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip() + "\n"


def parse_scene(d):
    tail = d.get("tail", "")
    m = re.search(r"CHAPTER (\d+): (.+?) - SCENE (\d+)\s+[–-]\s+([\d,]+) words\n(.+?)\n", tail)
    if not m:
        return None
    ch_n, ch_title, sc_n, _words, title = m.groups()
    rest = tail[m.end():]
    # summary = text up to the first "Status:" / "Actions" line
    body_lines = []
    statuses = []
    tags = []
    for line in rest.splitlines():
        s = line.strip()
        if s.startswith("Status:"):
            statuses.append(s.split(":", 1)[1].strip().lower())
            continue
        if s in ("Actions", "Label", "Codex"):
            if s == "Actions":
                break
            continue
        if statuses and s:
            tags.append(s)
            continue
        body_lines.append(s)
    summary = " ".join(x for x in body_lines if x).strip()
    status = statuses[-1] if statuses else "draft"
    return {
        "chapter_n": int(ch_n), "chapter_title": ch_title.strip(), "scene_n": int(sc_n),
        "title": title.strip(), "summary": summary, "status": status, "tags": tags,
        "text": clean_text(d.get("text", "")), "url": d.get("url", ""),
    }


def main(slug):
    pd = PROJECTS / slug
    ing = pd / ".ingest"
    scenes, codex = [], []
    for f in sorted(ing.glob("*.json")):
        d = json.loads(f.read_text("utf-8"))
        if d.get("kind") == "scene":
            s = parse_scene(d)
            if s:
                scenes.append(s)
            else:
                print("could not parse", f.name)
        elif d.get("kind") == "codex":
            codex.append(d)
    # dedupe scenes by url (last wins)
    by_url = {}
    for s in scenes:
        by_url[s["url"] or s["title"]] = s
    scenes = sorted(by_url.values(), key=lambda s: (s["chapter_n"], s["scene_n"]))
    for s in scenes:
        chdir = pd / "scenes" / f"{s['chapter_n']:02d}-{slugify(s['chapter_title'])}"
        chdir.mkdir(parents=True, exist_ok=True)
        chfile = chdir / "chapter.md"
        if not chfile.exists():
            chfile.write_text(dump_fm({"title": s["chapter_title"]}, ""), "utf-8")
        f = chdir / f"{s['scene_n']:02d}-{slugify(s['title'])}.md"
        meta = {"title": s["title"], "status": s["status"], "summary": s["summary"], "codex": s["tags"],
                "source": s["url"]}
        f.write_text(dump_fm(meta, s["text"]), "utf-8")
        print("scene", f.relative_to(pd), len(s["text"]))
    cat_map = {"characters": "characters", "character": "characters", "locations": "locations", "location": "locations",
               "lore": "lore", "subplots": "subplots", "subplot": "subplots", "items": "items", "item": "items"}
    for d in codex:
        cat = cat_map.get(str(d.get("category", "lore")).lower(), slugify(str(d.get("category", "lore")).lower()))
        name = d.get("name", "untitled").strip()
        aliases = [a.strip() for a in d.get("aliases", []) if a.strip() and a.strip() != name]
        always = bool(d.get("always", False))
        cdir = pd / "codex" / cat
        cdir.mkdir(parents=True, exist_ok=True)
        f = cdir / f"{slugify(name)}.md"
        meta = {"name": name, "aliases": aliases, "always": always, "type": d.get("type", "")}
        f.write_text(dump_fm(meta, clean_text(d.get("text", ""))), "utf-8")
        print("codex", f.relative_to(pd))


if __name__ == "__main__":
    main(sys.argv[1])
