"""Email-safe HTML digest (inline styles, table layout: renders in Gmail and Outlook)."""
from __future__ import annotations

from datetime import date
from html import escape

from .models import Job, Post

INK, MUTED, LINE, ACCENT, SOFT = "#1d2433", "#5b6475", "#e3e6ec", "#0b6e4f", "#f4f7f5"


def _badge(score: float) -> str:
    color = "#0b6e4f" if score >= 80 else "#b7791f" if score >= 65 else "#6b7280"
    return (f'<span style="display:inline-block;min-width:34px;text-align:center;padding:2px 6px;'
            f'border-radius:4px;background:{color};color:#fff;font-weight:600;font-size:13px">'
            f'{int(score)}</span>')


def _job_row(j: Job) -> str:
    meta = " · ".join(x for x in [escape(j.company), escape(j.location),
                                   "remote" if j.remote and "remote" not in j.location.lower() else "",
                                   escape(j.track), escape(j.source)] if x)
    why = f'<div style="margin-top:4px">{escape(j.why)}</div>' if j.why else ""
    angle = (f'<div style="margin-top:4px;color:{ACCENT}"><b>Lead with:</b> {escape(j.apply_angle)}</div>'
             if j.apply_angle else "")
    flags = (f'<div style="margin-top:4px;color:{MUTED};font-size:12px">{escape(" · ".join(j.flags))}</div>'
             if j.flags else "")
    return (f'<tr><td style="padding:12px 0;border-bottom:1px solid {LINE};vertical-align:top;width:46px">'
            f'{_badge(j.score)}</td><td style="padding:12px 0 12px 10px;border-bottom:1px solid {LINE}">'
            f'<a href="{escape(j.url)}" style="color:{INK};font-weight:600;font-size:15px;text-decoration:none">'
            f'{escape(j.title)}</a><div style="color:{MUTED};font-size:13px;margin-top:2px">{meta}</div>'
            f'{why}{angle}{flags}</td></tr>')


def _post_row(p: Post) -> str:
    draft = (f'<div style="margin-top:8px;padding:10px 12px;background:{SOFT};border-left:3px solid {ACCENT};'
             f'font-size:14px;line-height:1.5;white-space:pre-wrap">{escape(p.comment_draft)}</div>'
             if p.comment_draft else "")
    why = f'<div style="margin-top:4px;color:{MUTED};font-size:13px">{escape(p.why)}</div>' if p.why else ""
    return (f'<tr><td style="padding:12px 0;border-bottom:1px solid {LINE}">'
            f'<a href="{escape(p.url)}" style="color:{INK};font-weight:600;text-decoration:none">{escape(p.title)}</a>'
            f'<div style="color:{MUTED};font-size:12px;margin-top:2px">{escape(p.source)} · {escape(p.kind)}</div>'
            f'{why}{draft}</td></tr>')


def _h(t: str) -> str:
    return (f'<h2 style="font-size:13px;letter-spacing:.06em;text-transform:uppercase;color:{MUTED};'
            f'margin:28px 0 4px">{t}</h2>')


def render(jobs: list[Job], posts: list[Post], links: list[dict], weekly: str,
           notes: list[str], stats: dict) -> str:
    today = date.today().strftime("%a %d %b %Y")
    parts = [f'<div style="font-family:-apple-system,Segoe UI,Roboto,Arial,sans-serif;color:{INK};'
             f'max-width:680px;margin:0 auto;padding:16px">',
             f'<div style="font-size:20px;font-weight:700">Signal Radar</div>',
             f'<div style="color:{MUTED};font-size:13px">{today} · {stats.get("jobs_scanned", 0)} listings scanned · '
             f'{len(jobs)} new matches · {len(posts)} conversations</div>']

    # Today's actions: keep it to a sustainable daily habit
    acts = []
    if jobs:
        acts.append(f"Apply to the top {min(2, len(jobs))} role{'s' if len(jobs) > 1 else ''} below")
    if posts:
        acts.append(f"Leave {min(2, len(posts))} thoughtful comment{'s' if len(posts) > 1 else ''} (edit the drafts into your own words)")
    acts.append("Open one LinkedIn topic link and comment on one post from a hiring leader")
    parts.append(f'<div style="margin-top:16px;padding:12px 14px;background:{SOFT};border-radius:6px;font-size:14px">'
                 f'<b>Today, 20 minutes:</b><ol style="margin:6px 0 0 18px;padding:0">'
                 + "".join(f"<li>{escape(a)}</li>" for a in acts) + "</ol></div>")

    parts.append(_h("Roles to apply for"))
    if jobs:
        parts.append('<table role="presentation" width="100%" cellspacing="0" cellpadding="0">'
                     + "".join(_job_row(j) for j in jobs) + "</table>")
    else:
        parts.append(f'<p style="color:{MUTED}">No new matches today.</p>')

    parts.append(_h("Conversations to join"))
    if posts:
        parts.append('<table role="presentation" width="100%" cellspacing="0" cellpadding="0">'
                     + "".join(_post_row(p) for p in posts) + "</table>")
    else:
        parts.append(f'<p style="color:{MUTED}">Nothing worth your time today.</p>')

    if links:
        parts.append(_h("LinkedIn: fresh posts on your topics (last 24h)"))
        parts.append('<p style="font-size:14px;margin:4px 0">'
                     + " · ".join(f'<a href="{escape(l["url"])}" style="color:{ACCENT}">{escape(l["topic"])}</a>'
                                  for l in links) + "</p>")

    if weekly:
        parts.append(_h("This week's post draft"))
        parts.append(f'<div style="padding:12px 14px;border:1px solid {LINE};border-radius:6px;white-space:pre-wrap;'
                     f'font-size:14px;line-height:1.55">{escape(weekly)}</div>')

    if notes:
        parts.append(_h("Run notes"))
        parts.append(f'<ul style="color:{MUTED};font-size:12px;padding-left:18px">'
                     + "".join(f"<li>{escape(n)}</li>" for n in notes) + "</ul>")
    parts.append("</div>")
    return "".join(parts)
