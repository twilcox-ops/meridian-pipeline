"""Digest email: built and sent only when there's something notable to
report — a quiet run should be a quiet run, not an inbox full of "nothing
happened" emails that train everyone to stop reading them.
"""
from __future__ import annotations

import html
from typing import Iterable

import requests

from . import timeutil


def select_notable(records: list[dict], min_magnitude: float, limit: int = 25) -> list[dict]:
    notable = [r for r in records if (r.get("magnitude") or 0) >= min_magnitude]
    notable.sort(key=lambda r: r.get("magnitude") or 0, reverse=True)
    return notable[:limit]


def build_digest_html(run_summary: dict, notable: list[dict]) -> str:
    rows = "\n".join(
        "<tr>"
        f"<td>{(r.get('magnitude') or 0):.1f}</td>"
        f"<td>{html.escape(r.get('place') or '')}</td>"
        f"<td>{timeutil.ms_to_iso(r['event_time_ms'])}</td>"
        f"<td><a href=\"{html.escape(r.get('url') or '#')}\">details</a></td>"
        "</tr>"
        for r in notable
    )
    return f"""<html><body style="font-family: sans-serif;">
  <h2>Earthquake digest</h2>
  <p>
    Run at {run_summary['run_at']} &mdash; fetched {run_summary['fetched']},
    inserted {run_summary['inserted']}, updated {run_summary['updated']},
    skipped {run_summary['skipped']} in {run_summary['duration_seconds']}s.
  </p>
  <table border="1" cellpadding="6" cellspacing="0" style="border-collapse: collapse;">
    <tr><th>Mag</th><th>Place</th><th>Time (UTC)</th><th>Link</th></tr>
    {rows}
  </table>
</body></html>"""


class NullMailer:
    """MAILER=none, or --dry-run: log instead of sending. The safe default —
    a fresh checkout with no mail credentials configured should never crash
    trying to send email, it should just tell you what it would have sent.
    """

    def __init__(self, logger):
        self._logger = logger

    def send(self, to_addresses: Iterable[str], subject: str, html_body: str) -> bool:
        self._logger.info("mailer=none — digest not sent (subject: %s)", subject)
        return False


class GraphMailer:
    """Sends via Microsoft Graph `sendMail`, using an app registration with
    application-level Mail.Send permission (admin-consented), authenticating
    through the OAuth2 client-credentials flow. `sender` must be a mailbox
    that app registration is allowed to send as. Chosen over SendGrid/SES
    because it's the most transferable skill to enterprise environments,
    which already have a tenant and mailboxes.
    """

    TOKEN_URL = "https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token"
    SEND_URL = "https://graph.microsoft.com/v1.0/users/{sender}/sendMail"

    def __init__(self, tenant_id: str, client_id: str, client_secret: str, sender: str):
        self.tenant_id = tenant_id
        self.client_id = client_id
        self.client_secret = client_secret
        self.sender = sender

    def _get_token(self) -> str:
        response = requests.post(
            self.TOKEN_URL.format(tenant=self.tenant_id),
            data={
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "scope": "https://graph.microsoft.com/.default",
                "grant_type": "client_credentials",
            },
            timeout=15,
        )
        response.raise_for_status()
        return response.json()["access_token"]

    def send(self, to_addresses: Iterable[str], subject: str, html_body: str) -> bool:
        token = self._get_token()
        message = {
            "message": {
                "subject": subject,
                "body": {"contentType": "HTML", "content": html_body},
                "toRecipients": [
                    {"emailAddress": {"address": addr.strip()}} for addr in to_addresses if addr.strip()
                ],
            },
            "saveToSentItems": "false",
        }
        response = requests.post(
            self.SEND_URL.format(sender=self.sender),
            json=message,
            headers={"Authorization": f"Bearer {token}"},
            timeout=15,
        )
        response.raise_for_status()
        return True


def get_mailer(cfg, logger):
    if cfg.mailer == "graph":
        return GraphMailer(cfg.graph_tenant_id, cfg.graph_client_id, cfg.graph_client_secret, cfg.digest_from)
    if cfg.mailer == "sendgrid":
        raise NotImplementedError(
            "SendGrid mailer not implemented — swap in sendgrid.SendGridAPIClient here if you prefer it "
            "over Graph."
        )
    return NullMailer(logger)
