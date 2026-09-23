"""
applier.py — AI-tailored cover emails + CV summaries, sent via Gmail OAuth.

Only runs when profile.json → application.auto_apply_enabled == true.

Flow per job:
    1. Call Claude to generate a tailored cover email body + CV summary paragraph.
    2. Compose a Gmail message (with optional CV PDF attachment).
    3. Send via Gmail API using OAuth2 credentials.

Gmail OAuth setup:
    1. Go to https://console.cloud.google.com
    2. Enable the Gmail API for your project.
    3. Create OAuth2 Desktop credentials → download JSON.
    4. Set GMAIL_OAUTH_CREDENTIALS_PATH in .env to point to that JSON.
    5. First run will open a browser for consent — token saved to GMAIL_OAUTH_TOKEN_PATH.
"""

from __future__ import annotations

import base64
import json
import logging
import os
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

import anthropic
from dotenv import load_dotenv
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

load_dotenv()
logger = logging.getLogger(__name__)

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
GMAIL_OAUTH_CREDENTIALS_PATH = os.getenv(
    "GMAIL_OAUTH_CREDENTIALS_PATH", "./credentials/gmail_oauth.json"
)
GMAIL_OAUTH_TOKEN_PATH = os.getenv(
    "GMAIL_OAUTH_TOKEN_PATH", "./credentials/gmail_token.json"
)

GMAIL_SCOPES = ["https://www.googleapis.com/auth/gmail.send"]
CLAUDE_MODEL = "claude-sonnet-4-6"


# ── Profile helpers ───────────────────────────────────────────────────────────

def _load_profile(profile_path: str = "./profile.json") -> dict:
    with open(profile_path, encoding="utf-8") as f:
        return json.load(f)


def _auto_apply_enabled(profile: dict) -> bool:
    return bool(profile.get("application", {}).get("auto_apply_enabled", False))


# ── Gmail OAuth ───────────────────────────────────────────────────────────────

def _get_gmail_service():
    """Authenticate and return a Gmail API service object."""
    creds = None
    token_path = Path(GMAIL_OAUTH_TOKEN_PATH)
    creds_path = Path(GMAIL_OAUTH_CREDENTIALS_PATH)

    if not creds_path.exists():
        raise FileNotFoundError(
            f"Gmail OAuth credentials not found at {creds_path}. "
            "Download them from https://console.cloud.google.com and set "
            "GMAIL_OAUTH_CREDENTIALS_PATH in .env"
        )

    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), GMAIL_SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(
                str(creds_path), GMAIL_SCOPES
            )
            creds = flow.run_local_server(port=0)
        # Save token for next run
        token_path.parent.mkdir(parents=True, exist_ok=True)
        token_path.write_text(creds.to_json())
        logger.info("OAuth token saved to %s", token_path)

    return build("gmail", "v1", credentials=creds)


# ── Claude AI generation ──────────────────────────────────────────────────────

_COVER_EMAIL_PROMPT = """\
You are writing a job application cover email on behalf of {full_name}.

## Candidate Profile
{cv_text}

## Job Details
Title: {job_title}
Company: {company}
Description: {description}
Job URL: {job_url}

## Instructions
Write a concise, {tone} cover email (3–4 short paragraphs, max 300 words) that:
1. Opens with a strong hook specific to this role and company.
2. Highlights 2–3 of the candidate's most relevant skills/achievements for THIS specific job.
3. Closes with a clear call to action.

Also write a 2-sentence tailored CV summary for this specific role (to be placed at the top of the CV).

Format your response as JSON with exactly these keys:
{{
  "subject": "<email subject line>",
  "cover_email_body": "<plain-text email body>",
  "cv_summary": "<2-sentence tailored summary>"
}}
Return only the JSON object, no other text.
"""


def _generate_cover_content(job: dict, profile: dict) -> dict:
    """Call Claude to generate subject, cover email body, and tailored CV summary."""
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    personal = profile.get("personal", {})
    app_cfg = profile.get("application", {})

    prompt = _COVER_EMAIL_PROMPT.format(
        full_name=personal.get("full_name", ""),
        cv_text=profile.get("cv_plaintext", ""),
        job_title=job.get("title", ""),
        company=job.get("company", "Unknown Company"),
        description=job.get("description", "No description provided."),
        job_url=job.get("url", ""),
        tone=app_cfg.get("cover_letter_tone", "professional and concise"),
    )

    message = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=1024,
        messages=[{"role": "user", "content": prompt}],
    )
    raw = message.content[0].text.strip()

    # Strip markdown code fences if Claude wraps in ```json
    if raw.startswith("```"):
        raw = raw.split("```", 2)[1]
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.rsplit("```", 1)[0].strip()

    return json.loads(raw)


# ── Email composition ─────────────────────────────────────────────────────────

def _build_mime_message(
    sender: str,
    recipient: str,
    subject: str,
    body: str,
    cv_pdf_path: str | None,
    sender_name: str,
) -> MIMEMultipart:
    msg = MIMEMultipart("mixed")
    msg["From"] = f"{sender_name} <{sender}>"
    msg["To"] = recipient
    msg["Subject"] = subject

    alt = MIMEMultipart("alternative")
    alt.attach(MIMEText(body, "plain"))
    msg.attach(alt)

    if cv_pdf_path:
        pdf = Path(cv_pdf_path)
        if pdf.exists():
            with open(pdf, "rb") as f:
                attachment = MIMEApplication(f.read(), _subtype="pdf")
                attachment.add_header(
                    "Content-Disposition", "attachment", filename=pdf.name
                )
                msg.attach(attachment)
            logger.debug("Attached CV: %s", pdf.name)
        else:
            logger.warning("CV PDF not found at %s — sending without attachment", pdf)

    return msg


def _encode_message(msg: MIMEMultipart) -> dict:
    """Encode a MIME message for the Gmail API."""
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    return {"raw": raw}


# ── Public entry point ────────────────────────────────────────────────────────

def apply_to_jobs(jobs: list[dict], profile_path: str = "./profile.json") -> None:
    """
    For each new job, generate a tailored cover email with Claude and send via Gmail OAuth.
    Only runs if auto_apply_enabled is true in profile.json.

    Args:
        jobs:         Filtered new jobs from filter.filter_jobs()
        profile_path: Path to profile.json
    """
    profile = _load_profile(profile_path)

    if not _auto_apply_enabled(profile):
        logger.info(
            "auto_apply_enabled is false in profile.json — skipping auto-apply. "
            "Set it to true to enable."
        )
        return

    if not ANTHROPIC_API_KEY:
        logger.error("ANTHROPIC_API_KEY not set in .env — cannot generate cover emails.")
        return

    if not jobs:
        logger.info("No new jobs to apply to.")
        return

    app_cfg = profile.get("application", {})
    personal = profile.get("personal", {})
    sender_email = app_cfg.get("sender_email") or personal.get("email", "")
    sender_name = app_cfg.get("sender_name") or personal.get("full_name", "Applicant")
    cv_pdf_path = app_cfg.get("cv_pdf_path") if app_cfg.get("attach_cv_pdf") else None

    try:
        gmail_service = _get_gmail_service()
    except FileNotFoundError as exc:
        logger.error("Gmail OAuth setup required: %s", exc)
        return
    except Exception as exc:
        logger.exception("Failed to initialise Gmail service: %s", exc)
        return

    applied = 0
    for job in jobs:
        company = job.get("company") or "the hiring team"
        recipient = job.get("contact_email", "")

        if not recipient:
            logger.info(
                "No contact email for '%s' @ %s — visit the listing to apply manually: %s",
                job.get("title"), company, job.get("url"),
            )
            continue

        try:
            logger.info("Generating cover email for: %s @ %s", job.get("title"), company)
            content = _generate_cover_content(job, profile)
        except (json.JSONDecodeError, anthropic.APIError) as exc:
            logger.error("Claude generation failed for %s: %s", job.get("url"), exc)
            continue

        subject = content.get("subject", f"Application for {job.get('title')} — {sender_name}")
        body = content.get("cover_email_body", "")

        if not body:
            logger.warning("Empty cover email body returned for %s — skipping.", job.get("url"))
            continue

        mime_msg = _build_mime_message(
            sender=sender_email,
            recipient=recipient,
            subject=subject,
            body=body,
            cv_pdf_path=cv_pdf_path,
            sender_name=sender_name,
        )

        try:
            gmail_service.users().messages().send(
                userId="me", body=_encode_message(mime_msg)
            ).execute()
            logger.info("Application sent → %s (%s)", company, recipient)
            applied += 1
        except HttpError as exc:
            logger.error("Gmail API error sending to %s: %s", recipient, exc)
        except Exception as exc:
            logger.exception("Unexpected error sending application to %s: %s", recipient, exc)

    logger.info("Auto-apply complete: %d/%d applications sent.", applied, len(jobs))
