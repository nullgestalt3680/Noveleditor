# Writer

A local editor for writing long fiction with an AI *editor*, not an AI writer.

You write the prose. The editor reads your scenes together with your story bible and the decisions you have already made, and gives you critique, line edits, voice checks, summaries, rewrites of selected passages, and in-world inserts. It remembers what you decided last month because those decisions live in a file it reads every time, not in a chat window that forgets.

Everything is plain files on your disk. There is no account, no sync, no subscription. You bring your own API key.

## Why

Tools like NovelCrafter and Sudowrite are built around generating prose from beats. Writer is built around the other half of the job: a writer who drafts in their own voice and wants an editor with a complete memory of the book. The three ideas that make that work:

1. **Codex entries are injected by name.** Character, place and lore entries are pulled into the prompt when their name or an alias appears in the scene. Style guides and format rules are marked *always* and go in every time.
2. **Editor memory.** After a conversation settles something, one click extracts the decisions into `editor-memory.md`. Every later reply starts from that file.
3. **Nothing is hidden.** Prompts are Markdown files you can read and change. Scenes are Markdown files with a small front matter block. Chats are JSON. Put the folder in git if you like.

## Requirements

- Python 3.10 or newer
- `pip install anthropic`
- An Anthropic API key, and optionally a key for any OpenAI-compatible provider (OpenRouter, a local model server)

No Node, no build step, no database.

## Run

```bash
python app/server.py
```

Open http://localhost:8765. Click **Settings**, paste your API key, save. The sample project *The Lighthouse Ledger* is loaded; its scenes explain the interface as you read them.

Set `NOVEL_PORT` to use a different port. Interface language is English or Chinese, chosen in Settings.

## Layout of a project

```
projects/<your-book>/
  project.md            global notes (front matter: title, author, language, pov)
  editor-memory.md      decisions the editor reads before every reply
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

All of these are templates in `app/prompts/ops/`. **Prompts** in the top bar edits them; a changed template is saved as an override for the project or for all projects, the bundled defaults are never touched. Resolution order: `projects/<book>/prompts/` → `prompts/` (next to `app/`) → `app/prompts/`.

## Other features

- **Materials pane**: preview .docx, .md, .txt, .html, .pdf, images and audio from the project's `materials/` folder beside the text. Never sent to the model.
- **History**: a version of the scene is saved before every AI replace or insert, and at most once every 20 minutes while you type. Preview and restore from the scene header.
- **Snapshots**: zip the whole project into `snapshots/` with one click.
- **Check**: a scripted checker for in-world logs (timestamps that go backwards, format slips, tags that appear only once) and a keyword index across the book. No model calls.
- **Export**: Markdown or .docx, whole book or chosen chapters.
- **Resizable panes**, drag-and-drop ordering, whole-book word count.
- **Models**: any number of Claude models, and any OpenAI-compatible endpoint as a provider. Each chat remembers its model.

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

The server binds to 127.0.0.1 only. Your text goes to the model provider you chose and nowhere else. The `materials/` folder is never sent. The `.gitignore` excludes `projects/` (except the sample), `snapshots/`, `config.json` and all generated data, so a public fork of this repository never carries a book by accident.

## License

MIT. See `LICENSE`.
