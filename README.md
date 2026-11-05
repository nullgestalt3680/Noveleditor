# Noveleditor

A local, file-based editor for long fiction, with an AI editor that remembers your book.

Noveleditor is for writers who draft in their own voice and want a second reader who knows the whole manuscript: the characters, the places, the style rules, and every decision made along the way. It does not generate chapters for you. It critiques, line-edits, checks voices against your story bible, summarizes, rewrites a selected passage on request, and drafts in-world documents in the formats you define. Because the decisions it is told to respect live in a file it reads before every reply, it does not forget them when the conversation gets long.

Everything is plain files on your own disk: Markdown scenes, Markdown codex entries, a Markdown memory file, JSON chats. No account, no cloud, no subscription. You bring an API key for the model you want to use.

## Features

- **Scenes and chapters** as Markdown files with a small front matter block (title, status, summary, codex tags). Drag to reorder in the sidebar; files are renamed to match.
- **Codex** (story bible): characters, locations, lore. Entries are pulled into the model's context when their name or an alias appears in the scene; entries marked *always* (style guides, format rules) go in every time.
- **Editor memory**: one click extracts the decisions from a conversation into `editor-memory.md`. Every later reply starts from that file. A *Tidy* action consolidates the file when it grows.
- **Operations on a scene**: critique, line edit, voice check, summarize, rewrite selection, insert an in-world document at the cursor, render a draft in screenplay format. Results apply back into the text with one click.
- **Materials pane**: preview your research, notes and earlier drafts (.docx, .md, .txt, .html, .pdf, images, audio) beside the text. Never sent to the model.
- **History and snapshots**: automatic versions of each scene before any AI change, and one-click zip snapshots of the whole project.
- **Checks without a model**: a scripted checker for in-world logs (timestamps, formats, stray tags) and a keyword index across the book.
- **Export** to Markdown or Word (.docx).
- **Models**: any Claude model through the Anthropic API, plus any OpenAI-compatible endpoint (OpenRouter, a local server) as an extra provider. Each chat remembers its model.
- **Prompts are files.** Every operation is a Markdown template you can read and override, per project or globally.
- Interface in English or Chinese. Resizable panes. Whole-book word count.

## System requirements

- Windows 10/11, macOS 12+, or Linux
- Python 3.10 or newer
- A modern browser (Chrome, Edge, Firefox, Safari)
- Internet access for model calls; the app itself runs offline
- An Anthropic API key (https://console.anthropic.com), and optionally an OpenRouter key or a local model server

No Node, no build step, no database, no admin rights.

## Installation

**1. Get the code.** Either clone it:

```bash
git clone https://github.com/nullgestalt3680/Noveleditor.git
cd Noveleditor
```

or download the ZIP from the GitHub page and unzip it anywhere.

**2. Install Python** if you do not have it: https://www.python.org/downloads/ (on Windows tick "Add python.exe to PATH" in the installer). Check with:

```bash
python --version
```

**3. Install the one dependency:**

```bash
pip install anthropic
```

**4. Start the server.** On Windows double-click `run.bat`, or from a terminal:

```bash
python app/server.py
```

On macOS/Linux you can also use `./run.sh`. The server prints `Noveleditor running at http://localhost:8765`.

**5. Open http://localhost:8765** in your browser. Click **Settings** (top right), paste your API key, save.

The sample project *The Lighthouse Ledger* opens first; its scenes explain the interface as you read them. Create your own project with the **＋** button next to the project name.

To use a different port, set `NOVEL_PORT` before starting, for example `NOVEL_PORT=9000 python app/server.py`.

**Updating:** replace the `app/` folder with the new one. Your projects, keys, prompt overrides and snapshots all live outside `app/`.

## Layout of a project

```
projects/<your-book>/
  project.md            global notes (front matter: title, author, language, pov)
  editor-memory.md      decisions the editor reads before every reply
  prompts/              optional per-project prompt overrides
  scenes/01-Chapter/    one folder per chapter, chapter.md holds the title
      01-Scene.md       front matter: title, status, summary, codex; prose below
  codex/characters/     one Markdown file per entry: name, aliases, always, type
  codex/locations/
  codex/lore/
  materials/            research, notes, earlier drafts; previewed next to the text
  chats/                conversations with the editor (JSON)
  .history/             automatic versions of scenes
```

Order is the numeric prefix on folders and files. Drag in the sidebar to reorder; the files are renamed.

## What the editor is told

For every message, the server assembles a system prompt from:

1. `app/prompts/editor-system.md` (the editor's role and principles)
2. the global notes in `project.md`
3. `editor-memory.md`
4. codex entries: *Auto* (name or alias appears in the included scenes or your message) plus every *always* entry; or *All*; or *None*
5. the scenes listed in the chat's **Context** box (current scene, chapter, book, or any set you pick)

followed by the last 40 messages of the conversation. Anthropic prompt caching is used on the system block, so repeated calls on the same context are cheap.

## Operations

| Button | What it does | Result |
|---|---|---|
| Critique | Objective critique of the scene: pacing, voice, details, ending | in the chat |
| Line edit | Grammar, typos, awkward phrasing, in every voice | list of original → suggestion |
| Voice | Checks each character against their style entry in the codex | in the chat |
| Summarize | Third-person present-tense summary | one click saves it to the scene |
| Rewrite selection | Rewrites the selected text to your instruction, voice preserved | one click replaces the selection |
| Insert | Generates an in-world document (log, letter, message) at the cursor in the format defined in the codex | one click inserts it |
| Screenplay | Renders a draft in the project's screenplay format | one click replaces the selection |
| Save to editor notes | Extracts decisions from the recent exchange into `editor-memory.md` | |

All of these are templates in `app/prompts/ops/`. **Prompts** in the top bar shows the one in effect and lets you save a changed copy for this project or for all projects; the bundled defaults are never modified. Resolution order: `projects/<book>/prompts/` → `prompts/` (next to `app/`) → `app/prompts/`.

## Importing an existing book

- **Markdown**: *Import Markdown* in the sidebar. `#` is a chapter, `##` a scene (configurable).
- **From a web app**: the server has an `/api/project/<slug>/ingest` endpoint and `app/static/ingest.html` that accept raw dumps pushed from a browser tab (navigate the tab to `ingest.html#<url-encoded JSON>`), and `app/tools/build_from_ingest.py` turns them into scene and codex files. Adapt the parsing to your source.

## Configuration

`app/config.json` (created on first save from Settings):

```json
{
  "ui_language": "en",
  "anthropic_api_key": "sk-ant-…",
  "providers": [{ "id": "openrouter", "label": "OpenRouter", "type": "openai_compatible", "base_url": "https://openrouter.ai/api/v1", "api_key": "" }],
  "models": [
    { "id": "opus", "label": "Claude Opus 5.5 (editor)", "provider": "anthropic", "model": "claude-opus-5-5", "effort": "high" },
    { "id": "sonnet", "label": "Claude Sonnet 5.5 (fast)", "provider": "anthropic", "model": "claude-sonnet-5-5", "effort": "medium" }
  ],
  "default_model": "opus",
  "history_messages": 40,
  "max_tokens": 16000
}
```

The file is in `.gitignore`. Keys are never sent anywhere except to the provider you configured.

## Privacy

The server binds to 127.0.0.1 only; nothing on your network can reach it. Your text goes to the model provider you chose and nowhere else. The `materials/` folder is never sent. The `.gitignore` excludes `projects/` (except the sample), `snapshots/`, `config.json` and all generated data, so a fork of this repository never carries a book by accident.

## License

MIT, © 2026 Wind Witch. See `LICENSE`.
