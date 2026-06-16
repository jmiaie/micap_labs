"""Command-line interface.

    # List conversations in a Mac's live Messages database
    micap-imessage-export list --db ~/Library/Messages/chat.db

    # ...or from an (unencrypted) iPhone backup folder
    micap-imessage-export list --backup ~/Apple/MobileSync/Backup/<id>

    # Export one conversation to PDF + HTML + JSON + manifest
    micap-imessage-export export --db ~/Library/Messages/chat.db \\
        --contact "+15551234567" --out ./exhibit --case "Smith v. Smith" \\
        --operator "J. Doe"
"""

from __future__ import annotations

import argparse
import sys

from .backup import BackupError, locate_sms_db
from .chatdb import ChatDB, ChatDBError
from .export import export_target_dir, run_export
from .version import TOOL_NAME, __version__

_LAWFUL_USE = (
    "Use only on conversations you are legally authorised to access "
    "(your own device/account, or with proper legal authority)."
)


def _resolve_db(args) -> str:
    if args.backup:
        return locate_sms_db(args.backup)
    if args.db:
        return args.db
    raise SystemExit("error: provide --db <chat.db> or --backup <backup folder>")


def _cmd_list(args) -> int:
    db_path = _resolve_db(args)
    with ChatDB(db_path) as db:
        convos = db.list_conversations()
    if not args.all_empty:
        convos = [c for c in convos if c["message_count"] > 0]
    print(f"{'ROWID':>6}  {'MSGS':>6}  {'LAST (UTC)':<20}  PARTICIPANTS / TITLE")
    print("-" * 78)
    for c in convos:
        last = (c["last_date_utc"] or "")[:19]
        who = c["display_name"] or ", ".join(c["participants"]) or c["chat_identifier"] or "?"
        print(f"{c['chat_rowid']:>6}  {c['message_count']:>6}  {last:<20}  {who}")
    print(f"\n{len(convos)} conversation(s).")
    return 0


def _select_chats(db: ChatDB, args) -> list[int]:
    if args.all:
        return [c["chat_rowid"] for c in db.list_conversations()
                if c["message_count"] > 0]
    rowids: list[int] = list(args.chat or [])
    if args.contact:
        found = db.find_chats_by_contact(args.contact)
        if not found:
            raise SystemExit(f"error: no conversation matched contact "
                             f"{args.contact!r}. Try `list` to see options.")
        rowids.extend(found)
    if not rowids:
        raise SystemExit("error: select with --chat ROWID, --contact STR, or --all")
    # De-duplicate, preserve order.
    seen: set[int] = set()
    return [r for r in rowids if not (r in seen or seen.add(r))]


def _cmd_export(args) -> int:
    db_path = _resolve_db(args)
    with ChatDB(db_path) as db:
        titles = {c["chat_rowid"]: (c["display_name"]
                                    or ", ".join(c["participants"])
                                    or str(c["chat_rowid"]))
                  for c in db.list_conversations()}
        rowids = _select_chats(db, args)

    print(f"Source database: {db_path}")
    print(f"Exporting {len(rowids)} conversation(s) to {args.out}\n")
    for rowid in rowids:
        out_dir = export_target_dir(args.out, titles.get(rowid, str(rowid)), rowid)
        manifest = run_export(
            db_path=db_path,
            chat_rowid=rowid,
            out_dir=out_dir,
            operator=args.operator,
            case_reference=args.case,
            copy_attachments=args.copy_attachments,
        )
        c = manifest["conversation"]
        print(f"  [chat {rowid}] {c['title']}  ({c['message_count']} messages)")
        print(f"      -> {out_dir}")
        for o in manifest["outputs"]:
            print(f"         {o['name']}  sha256:{o['sha256'][:16]}…")
    print(f"\nDone. {_LAWFUL_USE}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog=TOOL_NAME,
        description="Export a full iMessage/SMS conversation to a "
                    "court-ready PDF + HTML + JSON with a SHA-256 manifest. "
                    + _LAWFUL_USE,
    )
    p.add_argument("--version", action="version",
                   version=f"{TOOL_NAME} {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    def add_source(sp):
        g = sp.add_mutually_exclusive_group()
        g.add_argument("--db", help="path to chat.db / sms.db")
        g.add_argument("--backup", help="path to an (unencrypted) iPhone backup folder")

    lp = sub.add_parser("list", help="list conversations in the database")
    add_source(lp)
    lp.add_argument("--all-empty", action="store_true",
                    help="include conversations with zero messages")
    lp.set_defaults(func=_cmd_list)

    ep = sub.add_parser("export", help="export conversation(s)")
    add_source(ep)
    ep.add_argument("--chat", type=int, action="append",
                    help="chat ROWID to export (repeatable)")
    ep.add_argument("--contact", help="export the chat matching this phone/email")
    ep.add_argument("--all", action="store_true", help="export every conversation")
    ep.add_argument("--out", default="./export", help="output directory")
    ep.add_argument("--case", help="case reference recorded in the manifest")
    ep.add_argument("--operator", help="name of the person performing the export")
    ep.add_argument("--copy-attachments", action="store_true",
                    help="copy + hash attachment files when present on disk")
    ep.set_defaults(func=_cmd_export)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except (ChatDBError, BackupError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
