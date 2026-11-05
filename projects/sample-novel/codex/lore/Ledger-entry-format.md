---
name: Ledger entry format
aliases: [ledger]
always: true
type: Format
---
A style-guide entry. Because it is marked `always: true`, it goes into every conversation, so the editor can check inserts against it.

Every lighthouse ledger entry has exactly this shape:

```
[DATE] [TIME] — [WEATHER] — [VISIBILITY in miles]
[One line of fact. No opinion.]
```

Example:

```
14 March — 22:40 — Gale from the west — 1 mile
Lamp relit after a two-minute outage. Cause unknown.
```

Entries never use the first person. Entries in the unknown hand break this rule once, in Chapter 2.
