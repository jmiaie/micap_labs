# Forensic & admissibility notes

This tool is built to make a message export **defensible**: complete, faithful,
and verifiable. This document explains what the export proves, how to handle it,
and — importantly — what it does *not* claim. None of this is legal advice;
rules of evidence vary by jurisdiction, so confirm specifics with counsel.

## What the export is

A **logical extraction** of message records from Apple's Messages database,
rendered into human- and machine-readable form, accompanied by a cryptographic
manifest. It is designed so that a neutral third party can independently confirm
that the outputs faithfully reflect the source database and were not altered.

## What it is *not*

* It is **not** a certified forensic *acquisition* of the device. It does not,
  by itself, establish how the database was obtained or that the device wasn't
  tampered with beforehand. For high-stakes matters, pair it with a proper
  acquisition (e.g., a documented backup or an examiner's image) and a
  chain-of-custody log for the device itself.
* It does **not** recover deleted messages beyond what remains in the database.
* It is **not** a substitute for authenticating testimony. Someone with
  knowledge typically still needs to testify to what the conversation is.

## How integrity is preserved

1. **Non-mutating read.** The database is opened `mode=ro&immutable=1`. SQLite
   creates no `-wal`/`-shm` files and writes nothing back, so the source file's
   bytes — and therefore its hash — are unchanged by the act of reading. (There
   is a regression test asserting the file size and absence of side files.)
2. **Hash before and after.** The manifest records the SHA-256 of the source
   database and of every output file. Record the source hash *before* you run
   the tool too (`shasum -a 256 chat.db`) and confirm it matches the manifest.
3. **Reproducibility.** Re-running against the same database produces identical
   JSON. The PDF and HTML are deterministic renderings of that JSON.

## Recommended chain-of-custody workflow

1. **Acquire** the database in a documented way and note who, when, and how:
   * Mac live database: copy `~/Library/Messages/chat.db` (and `chat.db-wal`,
     `chat.db-shm` if present) to a working copy; hash the copy.
   * iPhone backup: make the backup, then locate `sms.db` (this tool does that).
2. **Hash the source** and write it down before processing.
3. **Run the export** with `--operator` (who performed it) and `--case` (the
   matter reference). These are embedded in the manifest and on every PDF page.
4. **Preserve** the entire output folder *plus the original database* on
   write-once or hash-verified storage.
5. **Verify on receipt.** Anyone can recompute hashes:
   ```bash
   shasum -a 256 conversation.pdf conversation.html conversation.json
   # compare against manifest.txt / manifest.json
   ```

## What an examiner will scrutinize — and how this helps

| Question | Where the answer is |
| --- | --- |
| Is anything altered? | SHA-256 of source + outputs in the manifest. |
| Where did the text come from? | Per-message `text_source` in the JSON. |
| Exact times? | ISO-8601 **UTC** timestamps everywhere (converted from Apple epoch). |
| iMessage or SMS? | `service` field + colour, per message. |
| Read/delivered? | `date_read` / `date_delivered` in JSON and PDF. |
| Completeness of the thread? | Database row ids (`#n`) are shown; gaps are visible. |
| Method used? | Plain-English method statement in the manifest. |

## Time zones

All displayed timestamps are **UTC** to avoid ambiguity. The raw Apple values
are converted from the Mac absolute-time epoch (2001-01-01 UTC). If a proceeding
needs local time, convert from the UTC values and state the offset explicitly.

## Unicode / emoji in the PDF

The PDF uses base PDF fonts and shows characters it cannot draw (most emoji,
many non-Latin scripts) as a visible `[U+XXXX]` placeholder — never silently
dropped. The **JSON and HTML preserve the exact original text**, so the JSON
should be treated as authoritative for content; the PDF is the readable exhibit.

## Authorisation

Only export conversations you are legally entitled to access. Lawful authority
to obtain message content is a precondition, not something this tool establishes.
