# Getting the Messages database

This tool reads Apple's Messages database. Here is how to obtain it, easiest
first. Whichever route you use, **hash the file before processing**
(`shasum -a 256 <file>`) and keep a note of where it came from.

## Option A — From a Mac that uses Messages (easiest)

If you sign in to Messages on a Mac (including with Messages-in-iCloud enabled),
the conversation is already on disk:

```
~/Library/Messages/chat.db
```

Point the tool straight at it (it opens read-only and won't modify it):

```bash
python -m imessage_export list --db ~/Library/Messages/chat.db
```

For a pristine working copy, copy the file first (include side files if present):

```bash
mkdir ~/msg-work && cp ~/Library/Messages/chat.db* ~/msg-work/
shasum -a 256 ~/msg-work/chat.db
python -m imessage_export export --db ~/msg-work/chat.db --contact "+15551234567" --out ./exhibit
```

> Tip: Messages-in-iCloud syncs your iPhone threads to the Mac, so this often
> contains the same conversation you see on the phone.

## Option B — From an iPhone backup (no Mac Messages needed)

Make a local backup, then point the tool at the backup folder.

1. **Create an unencrypted backup**
   * **macOS (Finder):** connect iPhone → select it in Finder → *Back up all of
     the data on your iPhone to this Mac* → make sure *Encrypt local backup* is
     **unchecked** → *Back Up Now*.
   * **Windows (iTunes / Apple Devices app):** connect iPhone → *Back up now* to
     *This computer*, encryption **unchecked**.

2. **Find the backup folder**
   * macOS: `~/Library/Application Support/MobileSync/Backup/<device-id>/`
   * Windows: `%APPDATA%\Apple Computer\MobileSync\Backup\<device-id>\` (iTunes)
     or `%USERPROFILE%\Apple\MobileSync\Backup\<device-id>\` (Apple Devices app)

3. **Run against the backup folder** — the tool reads the backup's `Manifest.db`
   to locate `sms.db` automatically:

   ```bash
   python -m imessage_export list   --backup "/path/to/Backup/<device-id>"
   python -m imessage_export export --backup "/path/to/Backup/<device-id>" \
       --contact "+15551234567" --out ./exhibit
   ```

### Encrypted backups

If the backup is **encrypted**, the tool will detect it and stop, because v1
cannot decrypt it. Either:

* turn off *Encrypt local backup* and make a fresh backup, or
* use Option A (a Mac's live database) instead.

(Decrypting password-protected backups is on the roadmap.)

## Which conversation?

Run `list` first to see chats and their row ids:

```
 ROWID    MSGS  LAST (UTC)            PARTICIPANTS / TITLE
------------------------------------------------------------------------------
     1     842  2026-06-15T18:22:10   +15551234567
     2      37  2026-06-10T09:01:55   Weekend Plans
```

Then export by `--contact "<phone-or-email>"`, by `--chat <ROWID>`, or `--all`.
