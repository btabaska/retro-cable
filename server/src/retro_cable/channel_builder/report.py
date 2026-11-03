"""The proposal for Brandon to read: markdown (channel -> shows, day-parts, issues) and a flat
CSV with one row per (channel, show)."""

import csv
import io
from collections import defaultdict

from retro_cable.channel_builder.proposal import Issue, Proposal, ProposedChannel

CSV_FIELDS = [
    "number",
    "call_sign",
    "channel",
    "channel_id",
    "status",
    "type",
    "audience",
    "launch",
    "show",
    "title",
    "kind",
    "debut",
    "from_year",
    "pretend_premiere",
    "on_nas",
    "issues",
]


def _status(ch: ProposedChannel, issues: list[Issue]) -> str:
    if any(i.severity == "error" for i in issues):
        return "invalid (not applied)"
    return "existing channel" if ch.reuse else "new channel"


def _by_channel(proposal: Proposal) -> dict[str | None, list[Issue]]:
    out: dict[str | None, list[Issue]] = defaultdict(list)
    for i in proposal.issues:
        out[i.channel].append(i)
    return out


def _show_issues(ch: ProposedChannel, issues: list[Issue], title: str) -> list[str]:
    return [i.message for i in issues if title in i.message or (i.show and i.show == title)]


def to_csv(proposal: Proposal) -> str:
    issues = _by_channel(proposal)
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=CSV_FIELDS, lineterminator="\n")
    w.writeheader()
    for ch in sorted(proposal.channels, key=lambda c: c.number):
        mine = issues.get(ch.id, [])
        for s in ch.shows:
            w.writerow(
                {
                    "number": ch.number,
                    "call_sign": ch.call_sign,
                    "channel": ch.name,
                    "channel_id": ch.id,
                    "status": _status(ch, mine),
                    "type": ch.type,
                    "audience": ch.audience,
                    "launch": ch.launch_year or "always",
                    "show": s.show,
                    "title": s.title,
                    "kind": s.kind,
                    "debut": s.debut.isoformat() if s.debut else "",
                    "from_year": s.from_year,
                    "pretend_premiere": s.premiere.isoformat() if s.premiere else "",
                    "on_nas": "yes" if s.on_nas else "",
                    "issues": "; ".join(_show_issues(ch, mine, s.title)),
                }
            )
    for u in proposal.unplaced:
        w.writerow({"status": "unplaced", "show": u.show, "title": u.title, "issues": u.reason})
    return buf.getvalue()


def to_markdown(proposal: Proposal) -> str:
    issues = _by_channel(proposal)
    new = [c for c in proposal.channels if not c.reuse]
    reused = [c for c in proposal.channels if c.reuse]
    placed = {s.show for c in proposal.channels for s in c.shows}
    lines = [
        "# Channel builder proposal",
        "",
        f"Drafted {proposal.created_at} by the local model `{proposal.model}` from"
        f" `{proposal.shows_file}`, against catalog version `{proposal.base_version}`.",
        "Nothing is applied until you run `apply`; one undo in /admin reverts it whole.",
        "",
        f"- {len(new)} new channels, {len(reused)} existing channels get shows",
        f"- {len(placed)} shows placed, {len(proposal.already_carried)} already carried,"
        f" {len(proposal.unplaced)} unplaced",
        f"- {sum(1 for c in proposal.channels for s in c.shows if s.premiere)} pretend"
        " premieres (shows that debuted after 2010)",
        f"- {sum(1 for i in proposal.issues if i.severity == 'error')} errors,"
        f" {sum(1 for i in proposal.issues if i.severity == 'warning')} warnings",
        "",
    ]
    for ch in sorted(proposal.channels, key=lambda c: c.number):
        mine = issues.get(ch.id, [])
        launch = f"launches {ch.launch_year}" if ch.launch_year else "on the dial every year"
        lines += [
            f"## {ch.number} {ch.call_sign} · {ch.name}",
            "",
            f"*{_status(ch, mine)}, {ch.type}, {ch.audience}, {launch}.* {ch.identity}".rstrip(),
            "",
            "| Show | Kind | Debut | Carried from | Pretend premiere | On NAS |",
            "| --- | --- | --- | --- | --- | --- |",
        ]
        for s in sorted(ch.shows, key=lambda s: (s.from_year, s.title.lower())):
            lines.append(
                f"| {s.title} | {s.kind} | {s.debut.year if s.debut else '?'} | {s.from_year}"
                f" | {s.premiere.isoformat() if s.premiere else ''}"
                f" | {'yes' if s.on_nas else ''} |"
            )
        if ch.dayparts:
            titles = {s.show: s.title for s in ch.shows}
            lines += ["", "Day-parts:", ""]
            for p in ch.dayparts:
                names = ", ".join(titles.get(x, x) for x in p.shows)
                prime = " (prime time)" if p.primetime else ""
                lines.append(f"- {p.days} {p.start} **{p.name}**{prime}: {names}")
        elif not ch.reuse and ch.type == "network":
            lines += ["", "Day-parts: none, so it plays everything it carries all day."]
        errors = [i.message for i in mine if i.severity == "error"]
        warnings = [i.message for i in mine if i.severity == "warning"]
        if errors:
            lines += ["", "Errors (this channel is not applied):", ""]
            lines += [f"- {m}" for m in errors]
        if warnings:
            lines += ["", f"Warnings ({len(warnings)}):", ""]
            lines += [f"- {m}" for m in warnings[:20]]
            if len(warnings) > 20:
                lines.append(f"- … and {len(warnings) - 20} more (see the JSON)")
        lines.append("")
    if proposal.unplaced:
        lines += ["## Unplaced", "", "| Show | Reason |", "| --- | --- |"]
        lines += [f"| {u.title} | {u.reason} |" for u in proposal.unplaced]
        lines.append("")
    if proposal.already_carried:
        lines += ["## Already carried (left where they are)", ""]
        lines += [f"- {a.title}: {', '.join(a.channels)}" for a in proposal.already_carried]
        lines.append("")
    other = [i for i in proposal.issues if i.channel is None]
    if other or proposal.notes:
        lines += ["## Notes", ""]
        lines += [f"- {i.severity}: {i.show or ''} {i.message}".replace("  ", " ") for i in other]
        lines += [f"- {n}" for n in proposal.notes]
        lines.append("")
    return "\n".join(lines)
