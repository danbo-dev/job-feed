#!/usr/bin/env python3
"""
Email the latest job-feed report.

Reads data/last_run.json (written by find_jobs.py) and the report it points at,
renders the markdown to inline-styled HTML, and sends it over SMTP.

Gmail SMTP on 587 with STARTTLS, credentials from the environment only, and a
bracketed subject prefix so mail filters can catch it.

    EMAIL_PASSWORD    Gmail app password (GitHub secret). Required to send.

Usage:
    python3 notify_email.py            # send
    python3 notify_email.py --dry-run  # render to data/preview.html, send nothing

Never fails the build: if email is unconfigured or SMTP errors, it logs and exits 0.
On a successful send it writes data/delivered.flag, which the workflow uses to
decide whether committing the dedupe state is safe. Without delivery the state
must NOT advance, or roles get marked seen that Dan never saw.
"""

import argparse
import html
import json
import os
import re
import smtplib
import ssl
import sys
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(ROOT, "config.json")
LAST_RUN = os.path.join(ROOT, "data", "last_run.json")

SUBJECT_PREFIX = "[Job Feed]"
DELIVERED_FLAG = os.path.join(ROOT, "data", "delivered.flag")


def mark_delivered():
    """Signal to the workflow that the report actually reached Dan.

    The dedupe state must only be committed when delivery succeeded. Otherwise a
    broken mailer silently marks roles as seen and he never hears about them -
    which is exactly what happened on 2026-07-31 before this existed.
    """
    with open(DELIVERED_FLAG, "w") as f:
        f.write("ok\n")


INK = "#1f2933"
MUTED = "#6b7280"
ACCENT = "#1f3b57"
RULE = "#e4e8f0"
WARN_BG = "#fff4e5"
WARN_BD = "#f0a500"


def log(msg):
    print(f"  {msg}", file=sys.stderr)


# ---------------------------------------------------------------- md -> html

def inline(text):
    """Bold, links, and code inside a line of markdown. Escapes everything else."""
    # Protect link targets before escaping, then restore as anchors.
    links = []

    def stash(m):
        links.append((m.group(1), m.group(2)))
        return f"\x00{len(links) - 1}\x00"

    text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", stash, text)
    text = html.escape(text)
    text = re.sub(r"\*\*(.+?)\*\*", rf'<strong style="color:{INK}">\1</strong>', text)
    # Single asterisks after the double-asterisk pass, so **bold** is already gone.
    text = re.sub(r"(?<!\*)\*([^*\n]+)\*(?!\*)", r"<em>\1</em>", text)
    text = re.sub(r"`([^`]+)`",
                  r'<code style="background:#f3f4f6;padding:1px 4px;'
                  r'border-radius:3px;font-size:13px">\1</code>', text)

    def restore(m):
        label, href = links[int(m.group(1))]
        return (f'<a href="{html.escape(href, quote=True)}" '
                f'style="color:{ACCENT};text-decoration:underline">'
                f'{html.escape(label)}</a>')

    return re.sub(r"\x00(\d+)\x00", restore, text)


def bare_urls(text):
    """Linkify plain URLs sitting on their own (the report's '- **Link:** https://…')."""
    return re.sub(
        r'(?<!["\'>=])(https?://[^\s<>"]+)',
        rf'<a href="\1" style="color:{ACCENT};text-decoration:underline">\1</a>',
        text)


def md_to_html(md):
    """Render our own report format. Not a general markdown implementation."""
    out = []
    rows = []          # buffered table rows
    in_table = False

    def flush_table():
        nonlocal rows, in_table
        if not rows:
            return
        head, body = rows[0], rows[1:]
        out.append(f'<table style="border-collapse:collapse;width:100%;'
                   f'margin:12px 0;font-size:13px">')
        out.append("<tr>" + "".join(
            f'<th style="text-align:left;padding:6px 8px;border-bottom:2px solid '
            f'{RULE};color:{MUTED};font-weight:600">{inline(c)}</th>'
            for c in head) + "</tr>")
        for r in body:
            out.append("<tr>" + "".join(
                f'<td style="padding:6px 8px;border-bottom:1px solid {RULE};'
                f'vertical-align:top">{inline(c)}</td>' for c in r) + "</tr>")
        out.append("</table>")
        rows, in_table = [], False

    for raw in md.split("\n"):
        line = raw.rstrip()

        if line.startswith("|"):
            cells = [c.strip() for c in line.strip("|").split("|")]
            if all(re.fullmatch(r":?-{2,}:?", c) for c in cells if c):
                continue                      # separator row
            rows.append(cells)
            in_table = True
            continue
        if in_table:
            flush_table()

        if not line.strip():
            continue

        if line.startswith("> "):
            out.append(
                f'<div style="background:{WARN_BG};border-left:4px solid {WARN_BD};'
                f'padding:10px 14px;margin:14px 0;border-radius:3px;font-size:14px">'
                f'{inline(line[2:])}</div>')
        elif line.startswith("### "):
            out.append(f'<h3 style="font-size:16px;margin:20px 0 4px;color:{INK}">'
                       f'{inline(line[4:])}</h3>')
        elif line.startswith("## "):
            out.append(f'<h2 style="font-size:13px;letter-spacing:.06em;'
                       f'text-transform:uppercase;color:{ACCENT};margin:26px 0 6px;'
                       f'padding-bottom:4px;border-bottom:1px solid {RULE}">'
                       f'{inline(line[3:])}</h2>')
        elif line.startswith("# "):
            out.append(f'<h1 style="font-size:22px;margin:0 0 6px;color:{ACCENT}">'
                       f'{inline(line[2:])}</h1>')
        elif line.startswith("- "):
            out.append(f'<div style="margin:3px 0 3px 4px;font-size:14px;'
                       f'line-height:1.5">&bull;&nbsp;{bare_urls(inline(line[2:]))}</div>')
        elif line.startswith("---"):
            out.append(f'<hr style="border:0;border-top:1px solid {RULE};margin:22px 0">')
        elif line.startswith("<sub>"):
            out.append(f'<div style="font-size:12px;color:{MUTED};margin:6px 0">'
                       f'{inline(re.sub(r"</?sub>", "", line))}</div>')
        elif line.startswith("*") and line.endswith("*") and "·" in line:
            out.append(f'<div style="font-size:14px;color:{MUTED};margin:2px 0 10px">'
                       f'{inline(line.strip("*"))}</div>')
        else:
            out.append(f'<p style="margin:8px 0;font-size:14px;line-height:1.55;'
                       f'color:{INK}">{bare_urls(inline(line))}</p>')

    flush_table()

    # The charset meta is not optional: the report is full of em-dashes, middots
    # and status emoji, and a client that guesses Latin-1 renders them as "â€"".
    return ('<meta charset="utf-8">'
            f'<div style="font-family:-apple-system,BlinkMacSystemFont,\'Segoe UI\','
            f'Roboto,Helvetica,Arial,sans-serif;color:{INK};max-width:720px;'
            f'margin:0 auto;padding:8px 16px">' + "\n".join(out) +
            f'<hr style="border:0;border-top:1px solid {RULE};margin:24px 0 10px">'
            f'<div style="font-size:11px;color:{MUTED}">Generated by the job-feed '
            f'sweep. Tune it in <code>config.json</code>; run <code>/job-feed</code> '
            f'in Claude for a conversational summary.</div></div>')


# ---------------------------------------------------------------- send

def build_subject(info):
    if info.get("degraded"):
        return f"{SUBJECT_PREFIX} ⚠️ LinkedIn blocked — partial results only"
    n = info.get("new_count", 0)
    if not n:
        return f"{SUBJECT_PREFIX} No new roles today"
    top = info.get("top") or {}
    word = "role" if n == 1 else "roles"
    if top.get("title"):
        return (f"{SUBJECT_PREFIX} {n} new {word} — top: "
                f"{top['title'][:60]} @ {top.get('company', '')}")
    return f"{SUBJECT_PREFIX} {n} new {word}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true",
                    help="render to data/preview.html and send nothing")
    args = ap.parse_args()

    if os.path.exists(DELIVERED_FLAG):
        os.remove(DELIVERED_FLAG)

    with open(CONFIG_PATH) as f:
        cfg = json.load(f)
    email_cfg = cfg.get("email", {})

    if not email_cfg.get("enabled"):
        log("email disabled in config.json - nothing sent")
        return

    if not os.path.exists(LAST_RUN):
        log("no data/last_run.json - did find_jobs.py run? nothing sent")
        return
    with open(LAST_RUN) as f:
        info = json.load(f)

    report_path = os.path.join(ROOT, info["report"])
    if not os.path.exists(report_path):
        log(f"report missing: {report_path} - nothing sent")
        return
    with open(report_path, encoding="utf-8") as f:
        md = f.read()

    body_html = md_to_html(md)
    subject = build_subject(info)

    if args.dry_run:
        out = os.path.join(ROOT, "data", "preview.html")
        with open(out, "w", encoding="utf-8") as f:
            f.write(body_html)
        print(f"subject: {subject}")
        print(f"preview: {out}")
        return

    password = os.environ.get("EMAIL_PASSWORD")
    if not password:
        log("EMAIL_PASSWORD not set - sweep results are saved, but no email sent.")
        log("  cloud: set the EMAIL_PASSWORD repository secret")
        log("  local: export EMAIL_PASSWORD=<gmail app password>")
        return

    # Env wins over config so the committed config carries no real address.
    to_addr = os.environ.get("EMAIL_TO", "").strip() or email_cfg.get("to_address", "")
    from_addr = (os.environ.get("EMAIL_FROM", "").strip()
                 or email_cfg.get("from_address", "") or to_addr)
    if not to_addr or "@" not in to_addr:
        log("no recipient - set the EMAIL_TO secret (or email.to_address locally)")
        return

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = from_addr
    msg["To"] = to_addr
    # Plain-text fallback is the raw markdown - already readable by design.
    msg.attach(MIMEText(md, "plain", "utf-8"))
    msg.attach(MIMEText(body_html, "html", "utf-8"))

    try:
        with smtplib.SMTP(email_cfg.get("smtp_server", "smtp.gmail.com"),
                          email_cfg.get("smtp_port", 587), timeout=30) as s:
            s.starttls(context=ssl.create_default_context())
            s.login(from_addr, password)
            s.send_message(msg)
        log(f"sent to {to_addr}: {subject}")
        mark_delivered()
    except smtplib.SMTPAuthenticationError as exc:
        log(f"SMTP auth rejected: {exc}")
        log("Gmail requires an APP PASSWORD, not the account password.")
        log("  myaccount.google.com -> Security -> 2-Step Verification -> App passwords")
    except Exception as exc:  # noqa: BLE001 - delivery must never fail the run
        log(f"SMTP failed ({type(exc).__name__}): {exc}")


if __name__ == "__main__":
    main()
