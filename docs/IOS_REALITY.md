# Why a "single-click iOS app that exports a conversation" can't exist

This is the honest engineering answer to the original request. It is not a
matter of effort — it is what Apple's platform does and does not allow.

## The two hard walls

### 1. No app can read the Messages database

The full conversation lives in a SQLite database:

* **macOS:** `~/Library/Messages/chat.db` (present whenever you use Messages on
  a Mac, including with Messages-in-iCloud).
* **iOS:** `/var/mobile/Library/SMS/sms.db`, inside the app sandbox.

On iOS this file is **outside every third-party app's sandbox**. There is **no
public API** that hands a third-party app the contents of Messages. The only
`Messages`-related framework Apple publishes is for *iMessage app extensions*
(stickers, mini-games, payment-style apps) that live *inside* a conversation the
user is already in — and even those **cannot read message history**.

So an App Store app cannot, on its own, obtain the conversation at all.

### 2. No app can screenshot or drive another app

The fallback idea — "have the app screenshot the Messages app and stitch the
images" — also isn't possible:

* An app can only capture **its own** view hierarchy. There is no API to
  screenshot another app.
* iOS has no general UI-automation API for third-party apps to scroll/tap
  *another* app (there is nothing like Android's `AccessibilityService` screen-
  scraping, and even that is restricted).
* **ReplayKit** can record the screen, but it records a **video** the user
  drives by **manually scrolling** — which is exactly the lossy "screen-video"
  workaround you're trying to escape, and it produces no metadata.

### 3. Shortcuts can't do it either

The Shortcuts app can *send* a message and read *incoming* message content in
narrow automation contexts, but it **cannot enumerate or export an existing
conversation thread**.

## What that leaves (ranked, best first)

| Method | Complete? | Metadata? | Tamper-evident? | Effort |
| --- | --- | --- | --- | --- |
| **Read `chat.db` / `sms.db` (this tool)** | ✅ entire thread | ✅ timestamps, handles, service, receipts | ✅ SHA-256 manifest | Low (one command) |
| Forward messages to another number | ⚠️ partial, manual selection | ❌ reformatted | ❌ | High |
| Manual screenshots | ⚠️ misses content | ❌ | ❌ trivially edited | High |
| Screen-recording + scroll | ⚠️ if you scroll past everything | ❌ | ❌ | High |

Reading the database wins on every axis that matters in a proceeding, *and* it's
the least manual.

## Why the database is also the *better* evidence

A screenshot is a picture of a screen. An opposing party can argue it was edited,
cropped, or staged, and it carries no timestamps or routing data. The database
record carries, per message: the exact send time (to the second), who sent it,
whether it went over iMessage or SMS, and read/delivered receipts. Hashing the
source file and the outputs lets anyone independently verify nothing changed.

## "As close to one click as iOS allows"

1. **If you have a Mac that uses Messages:** the conversation is already in
   `~/Library/Messages/chat.db`. Running this tool is genuinely one command.
2. **If you only have the iPhone:** make one Finder/iTunes backup to a computer,
   then point this tool at the backup folder. See
   [`GETTING_THE_DATABASE.md`](GETTING_THE_DATABASE.md).

## Could a companion iOS/macOS app still help?

* A **macOS** app could wrap this exact workflow in a friendly GUI (pick a
  contact, click *Export*) because on a Mac the database is reachable. That's a
  reasonable future addition.
* An **iOS** app fundamentally cannot read Messages. The most it could honestly
  do is let you import a database/backup you obtained elsewhere and *package or
  digitally sign* it on the phone — which adds little over doing it on the
  computer that already has the file.

That's why this project ships the engine that does the real work, rather than an
App Store app that would have to pretend.
