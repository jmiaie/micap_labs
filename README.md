# micap-imessage-export

Export an **entire** iMessage/SMS conversation from an iPhone into a
**court-ready package**: a paginated PDF exhibit, a faithful HTML rendering, the
full structured data as JSON, and a **SHA-256 manifest** that documents chain of
custody.

It solves the real problem — there is no simple, complete, *verifiable* way to
get a whole conversation off an iPhone — without the manual screenshot / screen-
recording / forward-message workarounds.

> **Read this first:** a one-tap iOS App Store app that screenshots or scrapes
> the Messages app **cannot be built** — Apple's sandbox forbids it. And for
> court use, screenshots are the *weakest* form of this evidence. This tool
> takes the approach that actually holds up: it reads the real Messages
> database and hashes everything. See [`docs/IOS_REALITY.md`](docs/IOS_REALITY.md)
> for the full explanation.

---

## Why not a "single-click iOS app"?

Short version (full version in [`docs/IOS_REALITY.md`](docs/IOS_REALITY.md)):

| You might expect | iOS reality |
| --- | --- |
| An app reads your Messages history | ❌ No public API exposes the iMessage/SMS database to third-party apps. |
| An app screenshots/automates the Messages app | ❌ Apps cannot screenshot or drive *other* apps. |
| A Shortcut dumps a whole thread | ❌ Shortcuts cannot enumerate a conversation. |
| Screen-record + scroll | ⚠️ Possible but manual, lossy, and weak as evidence — the thing you're trying to avoid. |

The message database (`chat.db` on a Mac, `sms.db` in a backup) **does** contain
the whole conversation with every timestamp, the sender/recipient handles, the
iMessage-vs-SMS flag (your **blue vs. green**), and read/delivered receipts.
Reading it directly is both *complete* and *far more defensible* than pictures.

So the workflow this tool targets is **"as close to one click as iOS allows"**:

1. Get the database onto a computer (already there on a Mac that uses Messages;
   one Finder/iTunes backup otherwise — see
   [`docs/GETTING_THE_DATABASE.md`](docs/GETTING_THE_DATABASE.md)).
2. Run one command. Out comes the exhibit + manifest.

---

## Quick start

No third-party dependencies — just Python 3.9+.

```bash
# From a Mac that has Messages (the live database):
python -m imessage_export list --db ~/Library/Messages/chat.db

# Export the conversation with a given contact:
python -m imessage_export export \
    --db ~/Library/Messages/chat.db \
    --contact "+15551234567" \
    --out ./exhibit \
    --case "Smith v. Smith, No. 24-CV-1234" \
    --operator "Jane Doe"
```

From an **unencrypted** iPhone backup instead of a Mac database:

```bash
python -m imessage_export list   --backup "/path/to/Backup/<device-id>"
python -m imessage_export export --backup "/path/to/Backup/<device-id>" --chat 42 --out ./exhibit
```

Install as a command (optional):

```bash
pip install -e .
micap-imessage-export --help
```

## What you get

Each conversation is written to its own folder:

```
exhibit/1_+15551234567/
├── conversation.pdf     # paginated exhibit; running header carries the source hash
├── conversation.html    # faithful view: blue/green bubbles, every bubble tagged with row id + UTC
├── conversation.json     # authoritative structured data (full Unicode, per-message provenance)
├── manifest.json        # machine-readable chain-of-custody record
└── manifest.txt         # the same, human-readable
```

* **PDF** — the thing you file/print. Each page repeats a header with the source
  database's SHA-256 and the case reference, and a numbered footer, so a printout
  is self-identifying page by page.
* **HTML** — looks like the conversation (blue = iMessage, green = SMS/MMS).
  Every bubble has `data-rowid`, `data-guid`, `data-utc`, and `data-source`
  attributes tying it back to the underlying record.
* **JSON** — the data of record. Full original text (emoji and all), every
  timestamp in ISO-8601 UTC, and a `text_source` field per message noting
  whether text came from the `text` column or a decoded `attributedBody` blob.
* **manifest** — tool + version, extraction time, the **SHA-256 of the source
  database and of every output file**, record counts, date range, environment,
  a plain-English method statement, and the known limitations.

## How it stays defensible

* **Read-only, non-mutating.** The database is opened with SQLite
  `mode=ro&immutable=1`, which also prevents `-wal`/`-shm` side files — so the
  source file's hash does not change because you read it. (Covered by a test.)
* **Hash everything.** The source DB and all outputs are SHA-256'd and recorded
  in the manifest. Anyone can re-verify with `shasum -a 256 <file>`.
* **Reproducible.** Re-running against the same database reproduces identical
  JSON. The PDF/HTML are renderings of that JSON.
* **Honest about provenance.** Per-message `text_source`; if an `attributedBody`
  blob can't be decoded, the raw bytes are preserved (base64) rather than
  dropped.

See [`docs/FORENSICS.md`](docs/FORENSICS.md) for chain-of-custody and
admissibility guidance (and what this tool does *not* claim to do).

## Both blue and green

Apple's database stores SMS/MMS alongside iMessage in the same tables, with a
`service` column. This tool reads both and labels each message (`iMessage` vs
`SMS`) and colours it (blue vs green) in the PDF and HTML.

## Limitations (read before relying on this)

* Logical extraction only — it reads message records; it does not perform a
  certified forensic *acquisition* of the device and does not establish, by
  itself, how the database was obtained.
* Encrypted iPhone backups are not yet supported (use a Mac's live database, or
  a backup with encryption turned off). See the roadmap.
* Deleted messages are recovered only if still present in the database.
* The PDF uses base PDF fonts; characters outside Latin/cp1252 (most emoji, many
  non-Latin scripts) render as `[U+XXXX]` placeholders. **The JSON and HTML keep
  the full original text.**
* Attachment binaries are hashed/copied only when their files are present on the
  machine running the export (`--copy-attachments`).

## Project layout

```
src/imessage_export/
├── chatdb.py            # read-only reader; schema-tolerant; date + attributedBody handling
├── attributed_body.py   # recover text from Apple's typedstream blob
├── backup.py            # locate sms.db inside a Finder/iTunes backup
├── models.py            # Conversation / Message / Attachment / Participant
├── pdfwriter.py         # dependency-free PDF generator
├── export.py            # orchestration: read -> render -> manifest
├── cli.py               # `list` and `export` commands
└── exporters/           # json / html / pdf / manifest
tests/                   # synthetic-DB fixture + unit tests (run with unittest or pytest)
docs/                    # IOS_REALITY, FORENSICS, GETTING_THE_DATABASE
```

## Running the tests

```bash
PYTHONPATH=src:tests python -m unittest discover -s tests -p "test_*.py" -v
# or, with pytest installed:
pytest
```

## Roadmap

* Decrypt password-protected iPhone backups (so a backup is a complete path).
* Extract attachment binaries from backups (MediaDomain) and embed thumbnails.
* Optional macOS/iOS **companion app** that wraps this workflow in a GUI. Note
  the iOS side can only *package/sign* a database you supply — it still cannot
  read Messages itself.

## Legal / ethical use

Use this only on conversations you are **legally authorised** to access — your
own device or account, or under proper legal authority. You are responsible for
complying with the laws in your jurisdiction regarding recording, retention, and
disclosure of communications.
