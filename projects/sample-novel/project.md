---
title: The Lighthouse Ledger
author: Sample Author
language: English
pov: Third person, past tense
tense: Past
---
This is the sample project that ships with Noveleditor. It exists to show how the pieces fit together; replace it with your own book or delete it.

Everything the AI editor is told comes from files in this folder:

- This file: global notes about the book. Genre, structure, overall style, things to avoid. Included in every conversation.
- `editor-memory.md`: decisions already made. The editor reads it before every reply.
- `codex/`: characters, locations, lore. Entries are pulled into context when their name or an alias appears in the scene; entries marked `always: true` are included every time.
- `scenes/`: chapters as folders, scenes as Markdown files with a small front matter block.
- `materials/`: anything you wrote the book from (notes, research, earlier drafts). Previewed next to the text, never sent to the model unless you paste it.

Style for this sample: plain, concrete, a little dry. Short paragraphs.
