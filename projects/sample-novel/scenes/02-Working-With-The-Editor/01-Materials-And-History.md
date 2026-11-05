---
title: Materials and History
status: draft
summary:
codex: []
---
(Sample scene three. This chapter has no prose; it only explains the remaining features. Drag scenes and chapters in the sidebar to reorder them; the files are renamed on disk to match.)

Materials. Put research, notes and earlier drafts into the `materials/` folder of the project (`original material` and `素材` are also recognised). Click "Materials" above the text to open a preview pane beside it: .docx, .md, .txt, .html, .pdf, images and audio. The pane remembers which file you had open. Nothing in it is sent to the model.

History. Before the tool replaces or inserts AI text into a scene, it saves the current version under `.history/`. While you edit normally it saves at most one version every twenty minutes. Click "History" to preview and restore.

Snapshots. "Snapshot" in the top bar zips the whole project (scenes, codex, notes, chats; not materials) into the `snapshots/` folder. Unzip it over the project folder to go back.

Export. "Export" downloads the book, or the chapters you tick, as Markdown or as a .docx that opens in Word, Pages, LibreOffice and Google Docs.

Prompts. Every operation is a template in `app/prompts/`. "Prompts" in the top bar shows the one in effect and lets you save a changed copy for this project or for all projects; the bundled defaults are never modified.

Models. "Settings" holds your API keys and a list of models. Anthropic models call the Claude API directly; any OpenAI-compatible endpoint (OpenRouter, a local server) can be added as a provider. Each chat remembers the model it was started with.
