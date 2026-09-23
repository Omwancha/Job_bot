"""
notifier.py — Sends a Gmail SMTP daily digest of new jobs.

Reads from env vars:
    GMAIL_SENDER      — sender address (must own the App Password)
    GMAIL_APP_PASSWORD — Gmail App Password (not your real password)
    NOTIFY_EMAIL      — recipient address

Uses profile.json only for the sender name in the email signature.
"""

from __future__ import annotations

import json
import logging
import os
import smtplib
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

GMAIL_SENDER = os.getenv("GMAIL_SENDER", "")
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD", "")
NOTIFY_EMAIL = os.getenv("NOTIFY_EMAIL", "")
SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587


def _load_sender_name(profile_path: str = "./profile.json") -> str:
    try:
        with open(profile_path, encoding="utf-8") as f:
            profile = json.load(f)
        return profile.get("personal", {}).get("full_name", "JobBot")
    except Exception:
        return "JobBot"


def _build_html(jobs: list[dict], sender_name: str) -> str:
    today = datetime.utcnow().strftime("%A, %d %B %Y")
    count = len(jobs)

    rows = ""
    for i, job in enumerate(jobs, start=1):
        source_badge = {
            "fuzu": "#4CAF50",
            "myjobmag": "#2196F3",
            "brightermonday": "#FF9800",
        }.get(job.get("source", "").lower(), "#9E9E9E")

        desc = job.get("description", "")
        desc_html = f"<p style='color:#555;font-size:13px;margin:6px 0 0;'>{desc[:280]}{'…' if len(desc) > 280 else ''}</p>" if desc else ""

        rows += f"""
        <tr style="background:{'#fff' if i % 2 == 0 else '#f9f9f9'};">
          <td style="padding:16px;border-bottom:1px solid #e0e0e0;">
            <div style="display:flex;align-items:flex-start;gap:12px;">
              <div>
                <a href="{job['url']}" style="font-size:15px;font-weight:600;color:#1a73e8;text-decoration:none;">
                  {job.get('title', 'Unknown Role')}
                </a>
                <span style="margin-left:8px;padding:2px 8px;border-radius:12px;
                             background:{source_badge};color:#fff;font-size:11px;font-weight:500;">
                  {job.get('source', '').upper()}
                </span>
                <div style="margin-top:4px;color:#555;font-size:13px;">
                  {'🏢 ' + job['company'] if job.get('company') else ''}
                  {'&nbsp;&nbsp;📍 ' + job['location'] if job.get('location') else ''}
                  {'&nbsp;&nbsp;📅 ' + job['date_posted'] if job.get('date_posted') else ''}
                </div>
                {desc_html}
                <a href="{job['url']}" style="display:inline-block;margin-top:10px;padding:6px 14px;
                          background:#1a73e8;color:#fff;border-radius:4px;font-size:12px;
                          text-decoration:none;font-weight:500;">
                  View Job →
                </a>
              </div>
            </div>
          </td>
        </tr>"""

    html = f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>JobBot Daily Digest</title></head>
<body style="margin:0;padding:0;background:#f0f2f5;font-family:Arial,Helvetica,sans-serif;">
  <table width="100%" cellpadding="0" cellspacing="0" bgcolor="#f0f2f5">
    <tr><td align="center" style="padding:32px 16px;">
      <table width="640" cellpadding="0" cellspacing="0" style="max-width:640px;width:100%;">

        <!-- Header -->
        <tr>
          <td style="background:linear-gradient(135deg,#1a73e8,#0d47a1);padding:32px 32px 24px;border-radius:8px 8px 0 0;">
            <h1 style="margin:0;color:#fff;font-size:24px;font-weight:700;">
              🤖 JobBot Daily Digest
            </h1>
            <p style="margin:6px 0 0;color:#bbdefb;font-size:14px;">{today}</p>
          </td>
        </tr>

        <!-- Summary bar -->
        <tr>
          <td style="background:#e3f2fd;padding:12px 32px;border-left:1px solid #90caf9;
                     border-right:1px solid #90caf9;">
            <p style="margin:0;color:#1565c0;font-size:14px;font-weight:500;">
              {'✅ ' + str(count) + ' new job' + ('s' if count != 1 else '') + ' found matching your profile.' if count else '😴 No new jobs found today. Check back tomorrow!'}
            </p>
          </td>
        </tr>

        <!-- Job rows -->
        <tr>
          <td style="background:#fff;border:1px solid #e0e0e0;border-top:none;border-radius:0 0 8px 8px;">
            <table width="100%" cellpadding="0" cellspacing="0">
              {rows if rows else '<tr><td style="padding:32px;text-align:center;color:#9e9e9e;">No new jobs to display.</td></tr>'}
            </table>
          </td>
        </tr>

        <!-- Footer -->
        <tr>
          <td style="padding:20px 0;text-align:center;color:#9e9e9e;font-size:12px;">
            Sent by JobBot for {sender_name} &middot;
            <a href="https://github.com/Omwancha" style="color:#9e9e9e;">GitHub</a>
            <br>To stop receiving these emails, disable JobBot.
          </td>
        </tr>

      </table>
    </td></tr>
  </table>
</body>
</html>"""
    return html


def _build_plain(jobs: list[dict]) -> str:
    today = datetime.utcnow().strftime("%A, %d %B %Y")
    lines = [f"JobBot Daily Digest — {today}", "=" * 50, ""]
    if not jobs:
        lines.append("No new jobs found today.")
    for i, job in enumerate(jobs, start=1):
        lines.append(f"{i}. {job.get('title', 'Unknown')} @ {job.get('company', 'N/A')}")
        lines.append(f"   Location : {job.get('location', '')}")
        lines.append(f"   Posted   : {job.get('date_posted', '')}")
        lines.append(f"   Source   : {job.get('source', '').upper()}")
        lines.append(f"   URL      : {job['url']}")
        if job.get("description"):
            lines.append(f"   {job['description'][:200]}")
        lines.append("")
    return "\n".join(lines)


def send_digest(jobs: list[dict], profile_path: str = "./profile.json") -> bool:
    """
    Send the daily digest email via Gmail SMTP.

    Args:
        jobs: Filtered job list from filter.filter_jobs()
        profile_path: Path to profile.json

    Returns:
        True if sent successfully, False otherwise.
    """
    if not GMAIL_SENDER or not GMAIL_APP_PASSWORD or not NOTIFY_EMAIL:
        logger.error(
            "Gmail SMTP env vars not set. "
            "Set GMAIL_SENDER, GMAIL_APP_PASSWORD, and NOTIFY_EMAIL in .env"
        )
        return False

    sender_name = _load_sender_name(profile_path)
    today = datetime.utcnow().strftime("%d %b %Y")
    subject = f"[JobBot] {len(jobs)} new job{'s' if len(jobs) != 1 else ''} — {today}"

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = f"{sender_name} via JobBot <{GMAIL_SENDER}>"
    msg["To"] = NOTIFY_EMAIL

    msg.attach(MIMEText(_build_plain(jobs), "plain"))
    msg.attach(MIMEText(_build_html(jobs, sender_name), "html"))

    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.ehlo()
            server.starttls()
            server.login(GMAIL_SENDER, GMAIL_APP_PASSWORD)
            server.sendmail(GMAIL_SENDER, NOTIFY_EMAIL, msg.as_string())
        logger.info("Digest sent to %s (%d jobs)", NOTIFY_EMAIL, len(jobs))
        return True
    except smtplib.SMTPAuthenticationError:
        logger.error(
            "Gmail SMTP authentication failed. "
            "Make sure you're using an App Password, not your real password. "
            "Enable 2FA and get one at https://myaccount.google.com/apppasswords"
        )
    except Exception as exc:
        logger.exception("Failed to send digest: %s", exc)
    return False
