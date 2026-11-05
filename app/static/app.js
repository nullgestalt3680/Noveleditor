/* Writer front end. Vanilla JS, talks to server.py. Strings live in i18n.js. */
const $ = (s) => document.querySelector(s);
const $$ = (s) => Array.from(document.querySelectorAll(s));

const state = {
  config: null,
  projects: [],
  slug: null,
  info: null,          // project info (chapters, codex, memory, chats)
  file: null,          // {path, meta, body, kind}
  chat: null,          // current chat object
  ctx: { scenes: [], codex: "auto" },
  streaming: null,     // AbortController
  dirty: false,
};

// ---------------------------------------------------------------- api
async function api(method, url, body) {
  const r = await fetch(url, { method, headers: { "Content-Type": "application/json" }, body: body ? JSON.stringify(body) : undefined });
  const j = await r.json();
  if (!r.ok || j.error) throw new Error(j.error || r.statusText);
  return j;
}
const enc = encodeURIComponent;

async function sse(url, body, onDelta, onMeta) {
  const ctrl = new AbortController();
  state.streaming = ctrl;
  $("#stopBtn").classList.remove("hidden");
  $("#sendBtn").disabled = true;
  try {
    const r = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body), signal: ctrl.signal });
    if (!r.ok) { const j = await r.json().catch(() => ({})); throw new Error(j.error || r.statusText); }
    const reader = r.body.getReader();
    const dec = new TextDecoder();
    let buf = "";
    let result = {};
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      let idx;
      while ((idx = buf.indexOf("\n\n")) >= 0) {
        const line = buf.slice(0, idx).trim();
        buf = buf.slice(idx + 2);
        if (!line.startsWith("data:")) continue;
        const obj = JSON.parse(line.slice(5));
        if (obj.t !== undefined) onDelta(obj.t);
        else if (obj.error) throw new Error(obj.error);
        else { result = { ...result, ...obj }; if (onMeta) onMeta(obj); }
      }
    }
    return result;
  } finally {
    state.streaming = null;
    $("#stopBtn").classList.add("hidden");
    $("#sendBtn").disabled = false;
  }
}

// ---------------------------------------------------------------- helpers
function md(text) {
  if (window.marked) {
    try { return marked.parse(text, { breaks: true }); } catch (e) {}
  }
  const d = document.createElement("div"); d.textContent = text; return "<pre>" + d.innerHTML + "</pre>";
}
function esc(s) { const d = document.createElement("div"); d.textContent = s || ""; return d.innerHTML; }
function countWords(t) {
  return (t.match(/[一-鿿㐀-䶿]/g) || []).length + (t.match(/[A-Za-z0-9_']+/g) || []).length;
}
function toast(msg, ms = 2500) {
  const el = document.createElement("div");
  el.textContent = msg;
  el.style.cssText = "position:fixed;bottom:16px;left:50%;transform:translateX(-50%);background:#333;border:1px solid #555;padding:8px 14px;border-radius:8px;z-index:20";
  document.body.appendChild(el);
  setTimeout(() => el.remove(), ms);
}
function modal(title, bodyEl) {
  $("#modalTitle").textContent = title;
  const b = $("#modalBody"); b.innerHTML = ""; b.appendChild(bodyEl);
  $("#modal").classList.remove("hidden");
}
function closeModal() { $("#modal").classList.add("hidden"); }
$("#modalClose").onclick = closeModal;
$("#modal").addEventListener("click", (e) => { if (e.target.id === "modal") closeModal(); });
function el(html) { const tpl = document.createElement("template"); tpl.innerHTML = html.trim(); return tpl.content.firstElementChild; }

// ---------------------------------------------------------------- init
async function init() {
  state.config = await api("GET", "/api/config");
  LANG = state.config.ui_language === "zh" ? "zh" : "en";
  applyI18n();
  renderModels();
  state.projects = await api("GET", "/api/projects");
  const sel = $("#projectSel");
  sel.innerHTML = state.projects.map(p => `<option value="${esc(p.slug)}">${esc(p.title)}</option>`).join("");
  const last = localStorage.getItem("novel.slug");
  if (state.projects.length) {
    await openProject(state.projects.find(p => p.slug === last) ? last : state.projects[0].slug);
  } else {
    newProjectDialog();
  }
}

function renderModels() {
  const sel = $("#modelSel");
  sel.innerHTML = state.config.models.map(m => `<option value="${esc(m.id)}">${esc(m.label)}</option>`).join("");
  sel.value = state.config.default_model;
}

async function openProject(slug) {
  if (state.dirty) await saveFile();
  state.slug = slug;
  state.file = null;
  state.chat = null;
  state.ctx = { scenes: [], codex: "auto" };
  $("#sceneEditor").classList.add("hidden"); $("#codexEditor").classList.add("hidden"); $("#editorEmpty").classList.remove("hidden");
  localStorage.setItem("novel.slug", slug);
  $("#projectSel").value = slug;
  await reloadInfo();
  const lastChat = localStorage.getItem("novel.chat." + slug);
  const c = state.info.chats.find(c => c.id === lastChat) || state.info.chats[0];
  if (c) await openChat(c.id); else await newChat();
  const lastFile = localStorage.getItem("novel.file." + slug);
  if (lastFile) openFile(lastFile).catch(() => {});
}

async function reloadInfo() {
  state.info = await api("GET", `/api/project/${enc(state.slug)}`);
  renderTree(); renderCodex(); renderChats(); renderCtx();
}

$("#projectSel").onchange = (e) => openProject(e.target.value);

// ---------------------------------------------------------------- sidebar
$$(".tabs button").forEach(b => b.onclick = () => {
  $$(".tabs button").forEach(x => x.classList.toggle("active", x === b));
  $$(".tab").forEach(tb => tb.classList.toggle("active", tb.id === "tab-" + b.dataset.tab));
});

function renderTree() {
  const root = $("#sceneTree"); root.innerHTML = "";
  let total = 0;
  for (const ch of state.info.chapters) {
    total += ch.words;
    const d = el(`<div class="chapter" data-dir="${esc(ch.dir)}"><div class="chtitle" draggable="true" title="${esc(t("chapterDragTip"))}"><span>${esc(ch.title)}</span><span class="muted">${ch.words}</span></div></div>`);
    const head = d.querySelector(".chtitle");
    head.addEventListener("dragstart", (e) => startDrag(e, { type: "chapter", dir: ch.dir }, head));
    head.addEventListener("dragover", (e) => overTarget(e, head, "chapter"));
    head.addEventListener("dragleave", () => clearMarks(head));
    head.addEventListener("drop", (e) => dropOn(e, head, { type: "chapter", dir: ch.dir }));
    for (const s of ch.scenes) {
      const row = el(`<div class="scene ${state.file && state.file.path === s.path ? "active" : ""}" data-path="${esc(s.path)}" draggable="true">
        <span class="dot ${esc(s.status)}"></span><span class="name" title="${esc(s.summary)}">${esc(s.title)}</span><span class="w">${s.words}</span></div>`);
      row.onclick = () => openFile(s.path);
      row.addEventListener("dragstart", (e) => startDrag(e, { type: "scene", path: s.path }, row));
      row.addEventListener("dragover", (e) => overTarget(e, row, "scene"));
      row.addEventListener("dragleave", () => clearMarks(row));
      row.addEventListener("drop", (e) => dropOn(e, row, { type: "scene", path: s.path, dir: ch.dir }));
      d.appendChild(row);
    }
    const add = el(`<div class="addscene">${esc(t("addScene"))}</div>`);
    add.onclick = async () => {
      const title = prompt(t("sceneTitlePrompt"), `${t("scenePrefix")} ${ch.scenes.length + 1}`); if (!title) return;
      const r = await api("POST", `/api/project/${enc(state.slug)}/file`, { kind: "scene", chapter: ch.dir, title });
      await reloadInfo(); openFile(r.path);
    };
    add.addEventListener("dragover", (e) => overTarget(e, add, "end"));
    add.addEventListener("dragleave", () => clearMarks(add));
    add.addEventListener("drop", (e) => dropOn(e, add, { type: "end", dir: ch.dir }));
    d.appendChild(add);
    root.appendChild(d);
  }
  $("#totalWords").textContent = total ? t("words", total.toLocaleString()) : "";
}

// ---- drag and drop reordering (order = numeric prefixes on disk; the server renames)
let drag = null;
function startDrag(e, info, elm) {
  drag = info;
  elm.classList.add("dragging");
  e.dataTransfer.effectAllowed = "move";
  try { e.dataTransfer.setData("text/plain", JSON.stringify(info)); } catch (_) {}
  e.stopPropagation();
}
function clearMarks(elm) { elm.classList.remove("drop-before", "drop-after", "drop-into"); }
function overTarget(e, elm, kind) {
  if (!drag) return;
  if (drag.type === "chapter" && kind !== "chapter") return;
  e.preventDefault();
  e.dataTransfer.dropEffect = "move";
  clearMarks(elm);
  if (drag.type === "scene" && kind === "chapter") { elm.classList.add("drop-into"); return; }
  if (kind === "end") { elm.classList.add("drop-into"); return; }
  const r = elm.getBoundingClientRect();
  elm.classList.add(e.clientY < r.top + r.height / 2 ? "drop-before" : "drop-after");
}
async function dropOn(e, elm, target) {
  if (!drag) return;
  e.preventDefault(); e.stopPropagation();
  const before = elm.classList.contains("drop-before");
  clearMarks(elm);
  $$(".dragging").forEach(x => x.classList.remove("dragging"));
  const order = state.info.chapters.map(ch => ({ dir: ch.dir, scenes: ch.scenes.map(s => s.path) }));
  const d = drag; drag = null;
  if (d.type === "chapter") {
    if (target.type !== "chapter" || target.dir === d.dir) return;
    const i = order.findIndex(c => c.dir === d.dir);
    const [moved] = order.splice(i, 1);
    let j = order.findIndex(c => c.dir === target.dir);
    if (!before) j += 1;
    order.splice(j, 0, moved);
  } else {
    if (target.type === "scene" && target.path === d.path) return;
    for (const c of order) c.scenes = c.scenes.filter(p => p !== d.path);
    const tc = order.find(c => c.dir === target.dir);
    if (target.type === "scene") {
      let j = tc.scenes.indexOf(target.path);
      if (!before) j += 1;
      tc.scenes.splice(j, 0, d.path);
    } else if (target.type === "chapter") {
      tc.scenes.unshift(d.path);
    } else {
      tc.scenes.push(d.path);
    }
  }
  await applyOrder(order);
}
async function applyOrder(order) {
  if (state.dirty) await saveFile();
  let r;
  try { r = await api("POST", `/api/project/${enc(state.slug)}/reorder`, { chapters: order }); }
  catch (e) { toast(t("reorderFail") + e.message, 5000); await reloadInfo(); return; }
  const map = r.mapping || {};
  if (state.file && map[state.file.path]) { state.file.path = map[state.file.path]; localStorage.setItem("novel.file." + state.slug, state.file.path); }
  state.ctx.scenes = state.ctx.scenes.map(p => map[p] || p);
  await reloadInfo();
}
document.addEventListener("dragend", () => { drag = null; $$(".dragging, .drop-before, .drop-after, .drop-into").forEach(x => x.classList.remove("dragging", "drop-before", "drop-after", "drop-into")); });

function renderCodex() {
  const root = $("#codexList"); root.innerHTML = "";
  const cats = {};
  for (const e of state.info.codex) (cats[e.category] = cats[e.category] || []).push(e);
  for (const [cat, items] of Object.entries(cats)) {
    root.appendChild(el(`<div class="codexcat">${esc(cat)}</div>`));
    for (const e of items) {
      const row = el(`<div class="codexitem ${state.file && state.file.path === e.path ? "active" : ""}">
        <div>${esc(e.name)}${e.always ? `<span class="pin">${esc(t("always"))}</span>` : ""}</div><div class="prev">${esc(e.preview)}</div></div>`);
      row.onclick = () => openFile(e.path);
      root.appendChild(row);
    }
  }
}

function renderChats() {
  const root = $("#chatList"); root.innerHTML = "";
  for (const c of state.info.chats) {
    const row = el(`<div class="chatitem ${state.chat && state.chat.id === c.id ? "active" : ""}">
      <div class="t">${c.pinned ? "📌 " : ""}${esc(c.title || t("noTitle"))}</div>
      <div class="muted">${new Date(c.updated * 1000).toLocaleDateString()} · ${c.count}</div></div>`);
    row.onclick = () => openChat(c.id);
    root.appendChild(row);
  }
}

// ---------------------------------------------------------------- editor
async function openFile(path) {
  if (state.dirty) await saveFile();
  const f = await api("GET", `/api/project/${enc(state.slug)}/file?path=${enc(path)}`);
  f.kind = path.startsWith("codex/") ? "codex" : "scene";
  state.file = f;
  localStorage.setItem("novel.file." + state.slug, path);
  $("#editorEmpty").classList.add("hidden");
  $("#sceneEditor").classList.toggle("hidden", f.kind !== "scene");
  $("#codexEditor").classList.toggle("hidden", f.kind !== "codex");
  if (f.kind === "scene") {
    $("#sceneTitle").value = f.meta.title || "";
    $("#sceneStatus").value = (f.meta.status || "draft").toLowerCase();
    $("#sceneSummary").value = f.meta.summary || "";
    $("#sceneBody").value = f.body;
    updateWords();
    if (!state.ctx.scenes.length) { state.ctx.scenes = [path]; renderCtx(); }
    const wantOpen = !!localStorage.getItem("novel.matopen." + state.slug);
    if (wantOpen !== mat.open) setMaterialPane(wantOpen);
  } else {
    $("#codexName").value = f.meta.name || "";
    $("#codexCat").textContent = path.split("/")[1];
    $("#codexAliases").value = (f.meta.aliases || []).join(", ");
    $("#codexAlways").checked = !!f.meta.always;
    $("#codexBody").value = f.body;
  }
  renderTree(); renderCodex();
  setSaved(true);
}

function updateWords() { $("#sceneWords").textContent = t("words", countWords($("#sceneBody").value)); }
function setSaved(ok) {
  state.dirty = !ok;
  $("#saveState").textContent = ok ? "" : t("unsaved");
  $("#codexSaveState").textContent = ok ? "" : t("unsaved");
}
for (const id of ["sceneTitle", "sceneStatus", "sceneSummary", "sceneBody", "codexName", "codexAliases", "codexAlways", "codexBody"]) {
  $("#" + id).addEventListener("input", () => { setSaved(false); if (id === "sceneBody") updateWords(); scheduleSave(); });
  $("#" + id).addEventListener("change", () => { setSaved(false); scheduleSave(); });
}
let saveTimer = null;
function scheduleSave() { clearTimeout(saveTimer); saveTimer = setTimeout(saveFile, 1500); }
async function saveFile(opts = {}) {
  if (!state.file || !state.dirty) return;
  const f = state.file;
  let meta, body;
  if (f.kind === "scene") {
    meta = { ...f.meta, title: $("#sceneTitle").value, status: $("#sceneStatus").value, summary: $("#sceneSummary").value.replace(/\n/g, " ") };
    body = $("#sceneBody").value;
  } else {
    meta = { ...f.meta, name: $("#codexName").value, aliases: $("#codexAliases").value.split(/[,，]/).map(s => s.trim()).filter(Boolean), always: $("#codexAlways").checked };
    body = $("#codexBody").value;
  }
  await api("PUT", `/api/project/${enc(state.slug)}/file`, { path: f.path, meta, body, history: !!opts.history });
  f.meta = meta; f.body = body;
  setSaved(true);
  state.info = await api("GET", `/api/project/${enc(state.slug)}`);
  renderTree(); renderCodex();
}
document.addEventListener("keydown", (e) => {
  if ((e.ctrlKey || e.metaKey) && e.key === "s") { e.preventDefault(); saveFile(); }
});
window.addEventListener("beforeunload", () => { if (state.dirty) { saveFile(); } });

$("#deleteSceneBtn").onclick = $("#deleteCodexBtn").onclick = async () => {
  if (!state.file || !confirm(t("confirmTrash"))) return;
  await api("DELETE", `/api/project/${enc(state.slug)}/file?path=${enc(state.file.path)}`);
  state.file = null; state.dirty = false;
  $("#sceneEditor").classList.add("hidden"); $("#codexEditor").classList.add("hidden"); $("#editorEmpty").classList.remove("hidden");
  await reloadInfo();
};

$("#newChapterBtn").onclick = async () => {
  const title = prompt(t("chapterTitle")); if (!title) return;
  await api("POST", `/api/project/${enc(state.slug)}/chapter`, { title });
  await reloadInfo();
};
$("#newCodexBtn").onclick = async () => {
  const box = el(`<div>
    <label>${esc(t("category"))} <select id="ncCat"><option>characters</option><option>locations</option><option>lore</option><option>subplots</option><option>items</option></select></label>
    <label>${esc(t("name"))} <input id="ncName"></label>
    <label>${esc(t("aliases"))}<input id="ncAliases"></label>
    <label><input type="checkbox" id="ncAlways"> ${esc(t("alwaysLong"))}</label>
    <div class="row"><button id="ncOk">${esc(t("create"))}</button></div></div>`);
  modal(t("newCodexTitle"), box);
  box.querySelector("#ncOk").onclick = async () => {
    const r = await api("POST", `/api/project/${enc(state.slug)}/file`, {
      kind: "codex", category: box.querySelector("#ncCat").value, name: box.querySelector("#ncName").value,
      aliases: box.querySelector("#ncAliases").value.split(/[,，]/).map(s => s.trim()).filter(Boolean), always: box.querySelector("#ncAlways").checked });
    closeModal(); await reloadInfo(); openFile(r.path);
  };
};
$("#importBtn").onclick = () => {
  const box = el(`<div>
    <p class="muted">${t("importHelp")}</p>
    <div class="row"><label>${esc(t("importCh"))} <input id="imCh" value="#" style="width:60px"></label><label>${esc(t("importSc"))} <input id="imSc" value="##" style="width:60px"></label></div>
    <textarea id="imText" rows="14"></textarea>
    <div class="row"><button id="imOk">${esc(t("importBtn"))}</button></div></div>`);
  modal(t("importTitle"), box);
  box.querySelector("#imOk").onclick = async () => {
    const r = await api("POST", `/api/project/${enc(state.slug)}/import`, { text: box.querySelector("#imText").value, chapter_heading: box.querySelector("#imCh").value, scene_heading: box.querySelector("#imSc").value });
    closeModal(); toast(t("imported", r.chapters, r.scenes)); await reloadInfo();
  };
};

// ---------------------------------------------------------------- scene ops
function selectionInfo() {
  const ta = $("#sceneBody");
  const s = ta.selectionStart, e = ta.selectionEnd, v = ta.value;
  return { start: s, end: e, selection: v.slice(s, e), before: v.slice(Math.max(0, s - 1200), s), after: v.slice(e, e + 1200) };
}
$$(".ops button").forEach(b => b.onclick = () => runOp(b.dataset.op));

async function runOp(op) {
  if (!state.file || state.file.kind !== "scene") return;
  await saveFile();
  const title = $("#sceneTitle").value;
  const sel = selectionInfo();
  const vars = { scene_title: title, instruction: "", words: 120 };
  let label = "";
  let ctx = { ...state.ctx };
  if (op === "rewrite" || op === "screenplay-rewrite") {
    if (!sel.selection.trim()) return toast(t("selectFirst"));
    const ins = prompt(op === "rewrite" ? t("rewriteAsk") : t("passAsk"), "");
    if (ins === null) return;
    Object.assign(vars, { instruction: ins, selection: sel.selection, before: sel.before, after: sel.after });
    label = `${t("labelRewrite")} ${sel.selection.slice(0, 30)}…`;
    ctx.scenes = [];
  } else if (op === "log-insert") {
    const ch = prompt(t("insertWho"), ""); if (ch === null) return;
    const ins = prompt(t("insertWhat"), ""); if (ins === null) return;
    Object.assign(vars, { character: ch, instruction: ins, before: sel.before, after: sel.after });
    label = `${t("labelInsert")} ${ch}: ${ins.slice(0, 30)}`;
  } else {
    const ins = prompt(t("extraAsk"), ""); if (ins === null) return;
    vars.instruction = ins;
    label = { critique: t("labelCritique"), lineedit: t("labelLineedit"), "style-check": t("labelStyle"), summarize: t("labelSummarize") }[op] + " " + title;
    if (!ctx.scenes.includes(state.file.path)) ctx.scenes = [...ctx.scenes, state.file.path];
  }
  await sendMessage({ op, vars, label, context: ctx });
}

// ---------------------------------------------------------------- chat
async function newChat() {
  const c = await api("POST", `/api/project/${enc(state.slug)}/chat`, { model: $("#modelSel").value, context: state.ctx });
  await reloadInfo();
  await openChat(c.id);
}
$("#newChatBtn").onclick = newChat;

async function openChat(id) {
  state.chat = await api("GET", `/api/project/${enc(state.slug)}/chat/${enc(id)}`);
  localStorage.setItem("novel.chat." + state.slug, id);
  $("#chatTitle").value = state.chat.title || "";
  if (state.chat.model) $("#modelSel").value = state.chat.model;
  if (state.chat.context) state.ctx = { scenes: [], codex: "auto", ...state.chat.context };
  const known = new Set(state.info.chapters.flatMap(ch => ch.scenes.map(s => s.path)));
  state.ctx.scenes = (state.ctx.scenes || []).filter(p => known.has(p));
  renderCtx(); renderMessages(); renderChats();
}
$("#chatTitle").onchange = async () => { if (!state.chat) return; await api("PUT", `/api/project/${enc(state.slug)}/chat/${enc(state.chat.id)}`, { title: $("#chatTitle").value }); await reloadInfo(); };
$("#pinChatBtn").onclick = async () => { if (!state.chat) return; await api("PUT", `/api/project/${enc(state.slug)}/chat/${enc(state.chat.id)}`, { pinned: !state.chat.pinned }); await openChat(state.chat.id); await reloadInfo(); };
$("#deleteChatBtn").onclick = async () => {
  if (!state.chat || !confirm(t("deleteChat") + "?")) return;
  await api("DELETE", `/api/project/${enc(state.slug)}/chat/${enc(state.chat.id)}`);
  state.chat = null; await reloadInfo();
  if (state.info.chats[0]) openChat(state.info.chats[0].id); else newChat();
};
$("#modelSel").onchange = async () => { if (state.chat) await api("PUT", `/api/project/${enc(state.slug)}/chat/${enc(state.chat.id)}`, { model: $("#modelSel").value }); };

function renderCtx() {
  $("#ctxCodex").value = state.ctx.codex || "auto";
  const root = $("#ctxScenes"); root.innerHTML = "";
  const titles = {};
  for (const ch of (state.info ? state.info.chapters : [])) for (const s of ch.scenes) titles[s.path] = s.title;
  for (const p of state.ctx.scenes) {
    const c = el(`<span class="chip" title="${esc(t("chipRemove"))}">${esc(titles[p] || p)} ✕</span>`);
    c.onclick = () => { state.ctx.scenes = state.ctx.scenes.filter(x => x !== p); renderCtx(); persistCtx(); };
    root.appendChild(c);
  }
  const codexWord = { auto: t("ctxAutoShort"), all: t("ctxAll"), none: t("ctxNone") }[state.ctx.codex] || t("ctxAutoShort");
  $("#ctxSummary").textContent = t("ctxSummary", state.ctx.scenes.length, codexWord);
}
async function persistCtx() { if (state.chat) await api("PUT", `/api/project/${enc(state.slug)}/chat/${enc(state.chat.id)}`, { context: state.ctx }); }
$("#ctxCodex").onchange = () => { state.ctx.codex = $("#ctxCodex").value; renderCtx(); persistCtx(); };
$("#ctxCurrent").onclick = () => { if (state.file && state.file.kind === "scene" && !state.ctx.scenes.includes(state.file.path)) { state.ctx.scenes.push(state.file.path); renderCtx(); persistCtx(); } };
$("#ctxClear").onclick = () => { state.ctx.scenes = []; renderCtx(); persistCtx(); };
$("#ctxChapter").onclick = () => {
  if (!state.file || state.file.kind !== "scene") return toast(t("openScene"));
  const ch = state.info.chapters.find(c => c.scenes.some(s => s.path === state.file.path));
  if (!ch) return;
  state.ctx.scenes = ch.scenes.filter(s => s.words > 0).map(s => s.path); renderCtx(); persistCtx();
};
$("#ctxAll").onclick = () => {
  state.ctx.scenes = state.info.chapters.flatMap(c => c.scenes.filter(s => s.words > 0).map(s => s.path)); renderCtx(); persistCtx();
};
$("#sceneTree").addEventListener("click", (e) => {
  const row = e.target.closest(".scene"); if (!row || !e.shiftKey) return;
  e.stopPropagation();
  const p = row.dataset.path;
  if (!state.ctx.scenes.includes(p)) state.ctx.scenes.push(p); else state.ctx.scenes = state.ctx.scenes.filter(x => x !== p);
  renderCtx(); persistCtx();
}, true);

function renderMessages() {
  const root = $("#messages"); root.innerHTML = "";
  if (!state.chat) return;
  for (const m of state.chat.messages) root.appendChild(messageEl(m));
  root.scrollTop = root.scrollHeight;
}
function messageEl(m) {
  if (m.role === "user") {
    if (m.op) {
      const d = el(`<div class="msg user op"><span>${esc(m.label || m.op)}</span><div class="full">${esc(m.content)}</div></div>`);
      d.onclick = () => d.classList.toggle("open");
      return d;
    }
    return el(`<div class="msg user">${esc(m.content)}</div>`);
  }
  const d = el(`<div class="msg assistant"><div class="md">${md(m.content)}</div><div class="meta"></div><div class="actions"></div></div>`);
  if (m.error) d.classList.add("error");
  const meta = [];
  if (m.model) meta.push(m.model);
  if (m.usage) meta.push(`${m.usage.in || 0}→${m.usage.out || 0}${m.usage.cached ? " (cache " + m.usage.cached + ")" : ""}`);
  d.querySelector(".meta").textContent = meta.join(" · ");
  addActions(d, m);
  return d;
}
function addActions(d, m) {
  const a = d.querySelector(".actions");
  const copy = el(`<button class="ghost small">${esc(t("copy"))}</button>`);
  copy.onclick = () => { navigator.clipboard.writeText(m.content); toast(t("copied")); };
  a.appendChild(copy);
  if (m.op === "summarize") {
    const b = el(`<button class="ghost small">${esc(t("saveSummary"))}</button>`);
    b.onclick = () => { $("#sceneSummary").value = m.content.trim(); $("#summaryBox").open = true; setSaved(false); scheduleSave(); toast(t("savedSummary")); };
    a.appendChild(b);
  }
  if (m.op === "rewrite" || m.op === "screenplay-rewrite" || m.op === "log-insert") {
    const b = el(`<button class="ghost small">${esc(m.op === "log-insert" ? t("insertAtCursor") : t("replaceSelection"))}</button>`);
    b.onclick = () => applyToEditor(m);
    a.appendChild(b);
  }
}
async function applyToEditor(m) {
  if (!state.file || state.file.kind !== "scene") return toast(t("openScene"));
  state.dirty = true; await saveFile({ history: true });
  const ta = $("#sceneBody");
  let text = m.content.trim();
  if (text.includes("\n---\n")) text = text.split("\n---\n")[0].trim();
  const s = ta.selectionStart, e = ta.selectionEnd;
  const v = ta.value;
  if (m.op === "log-insert") {
    ta.value = v.slice(0, s) + "\n" + text + "\n" + v.slice(s);
  } else {
    if (s === e) return toast(t("selectFirstReplace"));
    ta.value = v.slice(0, s) + text + v.slice(e);
  }
  ta.dispatchEvent(new Event("input"));
  toast(t("applied"));
}

async function sendMessage(payload) {
  if (!state.chat) await newChat();
  if (state.streaming) return;
  const chat = state.chat;
  const model = $("#modelSel").value;
  const body = { model, context: payload.context || state.ctx, ...payload };
  if (payload.op) chat.messages.push({ role: "user", content: "", label: payload.label, op: payload.op });
  else chat.messages.push({ role: "user", content: payload.content });
  renderMessages();
  const root = $("#messages");
  const d = el(`<div class="msg assistant"><div class="md"></div><div class="meta">…</div><div class="actions"></div></div>`);
  root.appendChild(d);
  let acc = "";
  let lastRender = 0;
  try {
    const res = await sse(`/api/project/${enc(state.slug)}/chat/${enc(chat.id)}/message`, body, (tx) => {
      acc += tx;
      const now = Date.now();
      if (now - lastRender > 120) { d.querySelector(".md").innerHTML = md(acc); lastRender = now; root.scrollTop = root.scrollHeight; }
    }, (meta) => {
      if (meta.used) d.querySelector(".meta").textContent = t("ctxLine", meta.used.scenes.length, meta.used.codex.length, meta.model);
    });
    d.querySelector(".md").innerHTML = md(acc);
    if (res.usage) d.querySelector(".meta").textContent += ` · ${res.usage.in || 0}→${res.usage.out || 0}`;
    if (res.stop === "refusal") d.querySelector(".meta").textContent += " · " + t("refused");
  } catch (e) {
    d.classList.add("error");
    d.querySelector(".md").innerHTML += `<p>${esc(t("errorPrefix"))}${esc(e.message)}</p>`;
  }
  addActions(d, { content: acc, op: payload.op });
  state.chat = await api("GET", `/api/project/${enc(state.slug)}/chat/${enc(chat.id)}`);
  await reloadInfo();
  renderMessages();
}

$("#sendBtn").onclick = () => {
  const tx = $("#chatInput").value.trim(); if (!tx) return;
  $("#chatInput").value = "";
  sendMessage({ content: tx });
};
$("#chatInput").addEventListener("keydown", (e) => { if ((e.ctrlKey || e.metaKey) && e.key === "Enter") $("#sendBtn").click(); });
$("#stopBtn").onclick = () => { if (state.streaming) state.streaming.abort(); };

$("#extractBtn").onclick = async () => {
  if (!state.chat || !state.chat.messages.length) return;
  const box = el(`<div><p class="muted">${esc(t("extracting"))}</p><pre id="exOut" style="white-space:pre-wrap"></pre></div>`);
  modal(t("extractTitle"), box);
  let acc = "";
  try {
    const r = await sse(`/api/project/${enc(state.slug)}/chat/${enc(state.chat.id)}/extract-memory`, { last: 12, model: $("#modelSel").value },
      (tx) => { acc += tx; box.querySelector("#exOut").textContent = acc; });
    box.querySelector("p").textContent = r.appended ? t("extractDone") : t("extractNone");
    await reloadInfo();
  } catch (e) { box.querySelector("p").textContent = t("errorPrefix") + e.message; }
};

// ---------------------------------------------------------------- materials pane
const mat = { files: [], open: false };
async function loadMaterials() {
  const r = await api("GET", `/api/project/${enc(state.slug)}/materials`);
  mat.files = r.files; mat.root = r.root;
  renderMaterialList();
}
function renderMaterialList() {
  const sel = $("#materialSel");
  const q = ($("#materialFilter").value || "").toLowerCase();
  const files = mat.files.filter(f => !q || f.path.toLowerCase().includes(q));
  const groups = {};
  for (const f of files) (groups[f.dir || "/"] = groups[f.dir || "/"] || []).push(f);
  sel.innerHTML = `<option value="">— ${mat.root ? mat.files.length + t("materialCount") : t("materialNone")} —</option>` +
    Object.entries(groups).map(([dir, fs]) => `<optgroup label="${esc(dir)}">` + fs.map(f => `<option value="${esc(f.path)}">${esc(f.name)}</option>`).join("") + `</optgroup>`).join("");
  const last = localStorage.getItem("novel.material." + state.slug);
  if (last && mat.files.some(f => f.path === last)) sel.value = last;
}
async function showMaterial(path) {
  const view = $("#materialView");
  if (!path) { view.innerHTML = `<div class="empty">${esc(t("materialPick"))}</div>`; return; }
  localStorage.setItem("novel.material." + state.slug, path);
  view.innerHTML = `<div class="empty">${esc(t("materialLoading"))}</div>`;
  const raw = `/api/project/${enc(state.slug)}/material/raw?path=${enc(path)}`;
  try {
    const r = await api("GET", `/api/project/${enc(state.slug)}/material?path=${enc(path)}`);
    if (r.kind === "html") view.innerHTML = r.html;
    else if (r.kind === "markdown") view.innerHTML = `<div class="md">${md(r.text)}</div>`;
    else if (r.kind === "iframe-html") { view.innerHTML = ""; const fr = document.createElement("iframe"); fr.setAttribute("sandbox", ""); fr.srcdoc = r.html; view.appendChild(fr); }
    else if (r.kind === "pdf") view.innerHTML = `<iframe src="${raw}"></iframe>`;
    else if (r.kind === "image") view.innerHTML = `<img src="${raw}">`;
    else if (r.kind === "audio") view.innerHTML = `<audio controls src="${raw}" style="width:100%"></audio>`;
    else view.innerHTML = `<div class="empty">${esc(t("materialUnsupported"))}</div>`;
  } catch (e) { view.innerHTML = `<div class="empty">${esc(t("materialErr"))}${esc(e.message)}</div>`; }
  view.scrollTop = 0;
}
function setMaterialPane(open) {
  mat.open = open;
  $("#materialPane").classList.toggle("hidden", !open);
  $("#gutM").classList.toggle("hidden", !open);
  $("#writeSplit").classList.toggle("split", open);
  applySizes();
  localStorage.setItem("novel.matopen." + state.slug, open ? "1" : "");
  if (open) loadMaterials().then(() => { const v = $("#materialSel").value; if (v) showMaterial(v); });
}
$("#materialBtn").onclick = () => setMaterialPane(!mat.open);
$("#materialClose").onclick = () => setMaterialPane(false);
$("#materialSel").onchange = () => showMaterial($("#materialSel").value);
$("#materialFilter").oninput = () => renderMaterialList();

// ---------------------------------------------------------------- resizable panes
const sizes = { sidebar: 260, chat: 420, mat: 0.5 };
try { Object.assign(sizes, JSON.parse(localStorage.getItem("novel.sizes") || "{}")); } catch (_) {}
function applySizes() {
  if (window.innerWidth <= 800) { $("#layout").style.gridTemplateColumns = ""; $("#writeSplit").style.gridTemplateColumns = ""; return; }
  $("#layout").style.gridTemplateColumns = `${sizes.sidebar}px 6px 1fr 6px ${sizes.chat}px`;
  $("#writeSplit").style.gridTemplateColumns = mat.open ? `minmax(0, ${sizes.mat}fr) 6px minmax(0, ${1 - sizes.mat}fr)` : "";
}
function saveSizes() { localStorage.setItem("novel.sizes", JSON.stringify(sizes)); }
function makeResizer(gutter, onMove) {
  gutter.addEventListener("mousedown", (e) => {
    e.preventDefault();
    gutter.classList.add("active"); document.body.classList.add("resizing");
    const move = (ev) => { onMove(ev.clientX); applySizes(); };
    const up = () => { gutter.classList.remove("active"); document.body.classList.remove("resizing"); window.removeEventListener("mousemove", move); window.removeEventListener("mouseup", up); saveSizes(); };
    window.addEventListener("mousemove", move); window.addEventListener("mouseup", up);
  });
  gutter.addEventListener("dblclick", () => { onMove(null); applySizes(); saveSizes(); });
}
const clamp = (v, a, b) => Math.min(b, Math.max(a, v));
makeResizer($("#gutL"), (x) => { sizes.sidebar = x === null ? 260 : clamp(x - $("#layout").getBoundingClientRect().left, 160, 560); });
makeResizer($("#gutR"), (x) => { sizes.chat = x === null ? 420 : clamp($("#layout").getBoundingClientRect().right - x, 280, 900); });
makeResizer($("#gutM"), (x) => {
  const r = $("#writeSplit").getBoundingClientRect();
  sizes.mat = x === null ? 0.5 : clamp((x - r.left) / r.width, 0.1, 0.9);
});
window.addEventListener("resize", applySizes);
applySizes();

// ---------------------------------------------------------------- history
$("#historyBtn").onclick = async () => {
  if (!state.file || state.file.kind !== "scene") return;
  await saveFile();
  const path = state.file.path;
  const list = await api("GET", `/api/project/${enc(state.slug)}/history?path=${enc(path)}`);
  const box = el(`<div><p class="muted">${esc(t("historyHelp"))}</p>
    <div id="histList"></div><div id="histView" class="histview hidden"></div><div class="row"><button id="histRestore" class="hidden">${esc(t("restore"))}</button></div></div>`);
  const hl = box.querySelector("#histList");
  if (!list.length) hl.innerHTML = `<div class="muted">${esc(t("noHistory"))}</div>`;
  let chosen = null;
  for (const v of list) {
    const row = el(`<div class="hist"><span>${esc(v.ts.replace("_", " ").replace(/-(\d\d)-(\d\d)$/, ":$1:$2"))}</span><span class="muted">${t("words", v.words)}</span></div>`);
    row.onclick = async () => {
      $$(".hist").forEach(x => x.classList.toggle("active", x === row));
      const r = await api("GET", `/api/project/${enc(state.slug)}/history/get?path=${enc(path)}&ts=${enc(v.ts)}`);
      chosen = v.ts;
      const hv = box.querySelector("#histView"); hv.classList.remove("hidden"); hv.textContent = r.body;
      box.querySelector("#histRestore").classList.remove("hidden");
    };
    hl.appendChild(row);
  }
  box.querySelector("#histRestore").onclick = async () => {
    if (!chosen || !confirm(t("confirmRestore"))) return;
    await api("POST", `/api/project/${enc(state.slug)}/history/restore`, { path, ts: chosen });
    closeModal(); state.dirty = false; await openFile(path); toast(t("restored"));
  };
  modal(t("historyTitle", state.file.meta.title || ""), box);
};

// ---------------------------------------------------------------- snapshots
$("#snapshotBtn").onclick = async () => {
  const list = await api("GET", `/api/project/${enc(state.slug)}/snapshots`);
  const box = el(`<div><p class="muted">${esc(t("snapshotHelp"))}</p>
    <div class="row"><input id="snapNote" placeholder="${esc(t("snapshotNote"))}" style="flex:1"><button id="snapNew">${esc(t("snapshotNew"))}</button></div><div id="snapList"></div></div>`);
  const render = (items) => { box.querySelector("#snapList").innerHTML = items.length ? items.map(s => `<div class="hist"><span>${esc(s.file)}</span><span class="muted">${(s.size / 1024).toFixed(0)} KB</span></div>`).join("") : `<div class="muted">${esc(t("noSnapshots"))}</div>`; };
  render(list);
  box.querySelector("#snapNew").onclick = async () => {
    await saveFile();
    const r = await api("POST", `/api/project/${enc(state.slug)}/snapshot`, { note: box.querySelector("#snapNote").value });
    toast(t("snapshotSaved", r.file)); render(await api("GET", `/api/project/${enc(state.slug)}/snapshots`));
  };
  modal(t("snapshotTitle"), box);
};

// ---------------------------------------------------------------- checks
$("#checkBtn").onclick = async () => {
  const box = el(`<div>
    <h3 style="margin:0">${esc(t("logsHeading"))}</h3><div id="logReport" class="muted">${esc(t("checking"))}</div>
    <h3 style="margin:10px 0 0">${esc(t("kwHeading"))}</h3>
    <div class="row"><input id="kwWords" placeholder="${esc(t("kwPh"))}" style="flex:1"><button id="kwRun">${esc(t("find"))}</button></div>
    <div id="kwOut"></div></div>`);
  modal(t("checkTitle"), box);
  const r = await api("GET", `/api/project/${enc(state.slug)}/check-logs`);
  const rep = box.querySelector("#logReport");
  const tagline = Object.entries(r.tags).map(([tg, n]) => `${tg}×${n}`).join(", ");
  const lineWord = (n) => LANG === "zh" ? ` 第 ${n} 行` : ` line ${n}`;
  rep.innerHTML = `<div>${esc(t("logSummary", r.count, r.first, r.last))}</div>
    <div>${esc(t("sessionLine"))}${Object.entries(r.sessions).map(([k, v]) => `<code>${esc(k)}</code>${esc(t("appearsIn"))}${v.map(esc).join(", ")}`).join("; ") || esc(t("none"))}</div>
    <div class="muted">${esc(t("tags"))}${esc(tagline)}</div>
    <div style="margin-top:6px">${r.issues.length ? r.issues.map(i => `<div class="issue"><b>${esc(i.type)}</b> ${esc(i.scene)}${i.line ? lineWord(i.line) : ""}: ${esc(i.detail)}</div>`).join("") : esc(t("noIssues"))}</div>`;
  box.querySelector("#kwRun").onclick = async () => {
    const words = box.querySelector("#kwWords").value.split(/[,，]/).map(s => s.trim()).filter(Boolean);
    if (!words.length) return;
    const out = await api("POST", `/api/project/${enc(state.slug)}/keyword-index`, { words });
    box.querySelector("#kwOut").innerHTML = out.length ? out.map(s => `<div class="issue"><b>${esc(s.chapter)} · ${esc(s.scene)}</b>${s.hits.map(h => `<div>“${esc(h.word)}” …${esc(h.snippet)}…</div>`).join("")}</div>`).join("") : `<div class="muted">${esc(t("kwNone"))}</div>`;
  };
};

// ---------------------------------------------------------------- export
$("#exportBtn").onclick = () => {
  const box = el(`<div><p class="muted">${esc(t("exportHelp"))}</p><div id="exChs"></div>
    <div class="row"><button id="exMd">${esc(t("downloadMd"))}</button><button id="exDocx">${esc(t("downloadDocx"))}</button></div></div>`);
  box.querySelector("#exChs").innerHTML = state.info.chapters.map(c => `<label style="flex-direction:row;align-items:center;gap:6px"><input type="checkbox" value="${esc(c.dir)}"> ${esc(c.title)} <span class="muted">${c.words}</span></label>`).join("");
  const go = (fmt) => {
    const chs = Array.from(box.querySelectorAll("input:checked")).map(i => i.value).join(",");
    window.location.href = `/api/project/${enc(state.slug)}/export?format=${fmt}&chapters=${enc(chs)}`;
  };
  box.querySelector("#exMd").onclick = () => go("md");
  box.querySelector("#exDocx").onclick = () => go("docx");
  modal(t("exportTitle"), box);
};

// ---------------------------------------------------------------- memory / notes / prompts / settings
$("#memoryBtn").onclick = async () => {
  const r = await api("GET", `/api/project/${enc(state.slug)}/memory`);
  const box = el(`<div><p class="muted">${esc(t("memoryHelp"))}</p><textarea id="memText" rows="18"></textarea><div class="row"><button id="memSave">${esc(t("save"))}</button><button id="memTidy" class="ghost">${esc(t("tidy"))}</button><span id="memState" class="muted"></span></div></div>`);
  box.querySelector("#memText").value = r.text;
  modal(t("memoryTitle"), box);
  box.querySelector("#memSave").onclick = async () => { await api("PUT", `/api/project/${enc(state.slug)}/memory`, { text: box.querySelector("#memText").value }); closeModal(); toast(t("saved")); await reloadInfo(); };
  box.querySelector("#memTidy").onclick = async () => {
    const ta = box.querySelector("#memText"); const st = box.querySelector("#memState");
    const original = ta.value; st.textContent = t("tidying"); let acc = "";
    try {
      await sse(`/api/project/${enc(state.slug)}/memory/consolidate`, { model: $("#modelSel").value }, (tx) => { acc += tx; ta.value = acc; });
      st.textContent = t("tidyDone");
      const undo = el(`<button class="ghost">${esc(t("cancel"))}</button>`); undo.onclick = () => { ta.value = original; st.textContent = ""; undo.remove(); };
      st.after(undo);
    } catch (e) { ta.value = original; st.textContent = t("errorPrefix") + e.message; }
  };
};
$("#notesBtn").onclick = async () => {
  const i = state.info;
  const box = el(`<div>
    <div class="row"><label>${esc(t("title"))} <input id="pTitle"></label><label>${esc(t("author"))} <input id="pAuthor"></label><label>${esc(t("language"))} <input id="pLang"></label><label>${esc(t("pov"))} <input id="pPov"></label></div>
    <p class="muted">${esc(t("notesHelp"))}</p>
    <textarea id="pNotes" rows="14"></textarea><div class="row"><button id="pSave">${esc(t("save"))}</button></div></div>`);
  box.querySelector("#pTitle").value = i.meta.title || ""; box.querySelector("#pAuthor").value = i.meta.author || "";
  box.querySelector("#pLang").value = i.meta.language || ""; box.querySelector("#pPov").value = i.meta.pov || ""; box.querySelector("#pNotes").value = i.notes;
  modal(t("notesTitle"), box);
  box.querySelector("#pSave").onclick = async () => {
    await api("PUT", `/api/project/${enc(state.slug)}/notes`, { meta: { title: box.querySelector("#pTitle").value, author: box.querySelector("#pAuthor").value, language: box.querySelector("#pLang").value, pov: box.querySelector("#pPov").value }, notes: box.querySelector("#pNotes").value });
    closeModal(); toast(t("saved")); await reloadInfo(); init();
  };
};
$("#promptsBtn").onclick = async () => {
  const ps = await api("GET", `/api/prompts?slug=${enc(state.slug)}`);
  const names = Object.keys(ps);
  const box = el(`<div><div class="row"><select id="prSel">${names.map(n => `<option>${esc(n)}</option>`).join("")}</select><span id="prSrc" class="muted"></span><span class="grow"></span><button id="prSaveP">${esc(t("saveForProject"))}</button><button id="prSaveU" class="ghost">${esc(t("saveForAll"))}</button></div>
    <p class="muted">${esc(t("promptsHelp"))}</p>
    <textarea id="prText" rows="18"></textarea></div>`);
  const sel = box.querySelector("#prSel"), ta = box.querySelector("#prText"), src = box.querySelector("#prSrc");
  const show = () => { ta.value = ps[sel.value].text; src.textContent = t("source", ps[sel.value].source); };
  show();
  sel.onchange = show;
  const save = async (scope) => {
    const r = await api("PUT", "/api/prompts", { name: sel.value, text: ta.value, slug: state.slug, scope });
    ps[sel.value] = { text: ta.value, source: scope === "project" ? "project" : "user" }; show(); toast(`${t("saved")}: ${r.saved_to}`);
  };
  box.querySelector("#prSaveP").onclick = () => save("project");
  box.querySelector("#prSaveU").onclick = () => save("user");
  modal(t("promptsTitle"), box);
};
$("#settingsBtn").onclick = async () => {
  const c = await api("GET", "/api/config");
  const box = el(`<div>
    <label>${esc(t("uiLang"))} <select id="sLang"><option value="en">English</option><option value="zh">中文</option></select></label>
    <label>${esc(t("anthKey"))} <input id="sAnth" placeholder="sk-ant-…" value="${esc(c.anthropic_api_key)}"></label>
    <div id="sProv"></div>
    <p class="muted">${esc(t("modelsHelp"))}</p>
    <div id="sModels"></div>
    <div class="row"><button id="sAddModel" class="ghost small">${esc(t("addModel"))}</button><label>${esc(t("defaultModel"))} <select id="sDefault"></select></label><label>${esc(t("historyN"))} <input id="sHist" style="width:70px"></label><label>${esc(t("maxTokens"))} <input id="sMax" style="width:90px"></label></div>
    <div class="row"><button id="sSave">${esc(t("save"))}</button></div></div>`);
  box.querySelector("#sLang").value = c.ui_language === "zh" ? "zh" : "en";
  const prov = box.querySelector("#sProv");
  for (const p of c.providers) {
    prov.appendChild(el(`<label>${esc(t("providerKey", p.label, p.base_url))}<input data-pid="${esc(p.id)}" value="${esc(p.api_key)}"></label>`));
  }
  const mroot = box.querySelector("#sModels");
  const addRow = (m = { id: "", label: "", provider: "anthropic", model: "", effort: "" }) => {
    const r = el(`<div class="modelrow"><input placeholder="id" value="${esc(m.id)}"><input placeholder="label" value="${esc(m.label)}"><input placeholder="provider" value="${esc(m.provider)}"><input placeholder="model" value="${esc(m.model)}"><input placeholder="effort" style="width:70px" value="${esc(m.effort || "")}"><button class="ghost small danger">✕</button></div>`);
    r.querySelector("button").onclick = () => r.remove();
    mroot.appendChild(r);
  };
  c.models.forEach(addRow);
  box.querySelector("#sAddModel").onclick = () => addRow();
  const def = box.querySelector("#sDefault");
  def.innerHTML = c.models.map(m => `<option value="${esc(m.id)}">${esc(m.label)}</option>`).join(""); def.value = c.default_model;
  box.querySelector("#sHist").value = c.history_messages; box.querySelector("#sMax").value = c.max_tokens;
  modal(t("settingsTitle"), box);
  box.querySelector("#sSave").onclick = async () => {
    const models = Array.from(mroot.children).map(r => { const i = r.querySelectorAll("input"); return { id: i[0].value.trim(), label: i[1].value.trim(), provider: i[2].value.trim(), model: i[3].value.trim(), effort: i[4].value.trim() || undefined }; }).filter(m => m.id && m.model);
    const providers = c.providers.map(p => ({ ...p, api_key: prov.querySelector(`input[data-pid="${p.id}"]`).value }));
    await api("PUT", "/api/config", { ui_language: box.querySelector("#sLang").value, anthropic_api_key: box.querySelector("#sAnth").value, providers, models, default_model: def.value, history_messages: +box.querySelector("#sHist").value, max_tokens: +box.querySelector("#sMax").value });
    closeModal(); toast(t("saved")); location.reload();
  };
};

function newProjectDialog() {
  const box = el(`<div><label>${esc(t("title"))} <input id="npTitle"></label><label>${esc(t("penName"))} <input id="npAuthor"></label><label>${esc(t("language"))} <input id="npLang" value="${LANG === "zh" ? "中文" : "English"}"></label><div class="row"><button id="npOk">${esc(t("create"))}</button></div></div>`);
  modal(t("newProjectTitle"), box);
  box.querySelector("#npOk").onclick = async () => {
    const r = await api("POST", "/api/projects", { title: box.querySelector("#npTitle").value, author: box.querySelector("#npAuthor").value, language: box.querySelector("#npLang").value });
    closeModal(); await init(); openProject(r.slug);
  };
}
$("#newProjectBtn").onclick = newProjectDialog;

init().catch(e => { console.error(e); toast(t("initFail") + e.message, 6000); });
