"""Drafting a proposal: the code sorts, batches, dates, numbers and validates; the local model
only does the judgement calls, each as a small JSON reply checked against a schema:

1. channel concepts per audience group (name, call sign, type, identity, era),
2. which channel each show goes on, in batches that fit a local model's context, and a
   pretend premiere year for shows that debuted after 2010,
3. the day-parts of each new network channel.

Everything else is plain Python: which shows are kept, holiday specials and shows already on
a channel are set aside, groups, carriage years from first air dates, pretend premiere dates,
launch years, numbers, call signs and ids. A show the model leaves out, or places on a channel
that doesn't exist, ends up unplaced with the reason.
"""

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from retro_cable.admin.drafts import FIRST_YEAR, LAST_YEAR, LibraryItem, new_channel_id
from retro_cable.catalog import Catalog
from retro_cable.catalog.models import CUSTOM_PREFIX, Channel
from retro_cable.channel_builder.llm import LLM, InvalidReply, Transcript, ask_json
from retro_cable.channel_builder.proposal import (
    HHMM,
    AlreadyCarried,
    ChannelType,
    DayPartPlan,
    Proposal,
    ProposedChannel,
    ProposedShow,
    Unplaced,
    call_sign,
    clamp_year,
    launch_year,
    next_number,
    premiere_year,
    pretend_premiere,
    start_year,
    taken_call_signs,
    taken_numbers,
)
from retro_cable.channel_builder.shows import CATEGORIES, ShowRow

GROUP_ORDER = (*CATEGORIES, "movies")
SHOWS_PER_CHANNEL = 30  # target size when asking for concepts
MAX_CHANNELS_PER_GROUP = 8
CONCEPT_SAMPLE = 60  # titles shown to the model when it names a group's channels
SYSTEM = (
    "You are the station manager of a 1990-2010 Bay Area cable TV system. You design cable"
    " channels the way real networks did then: a clear audience, a recognizable identity and"
    " a believable schedule. Channels may be real networks or made-up ones. Reply with one"
    " JSON object only, no prose."
)


class _Lenient(BaseModel):
    model_config = ConfigDict(extra="ignore")


class Concept(_Lenient):
    name: str = Field(min_length=2, max_length=40)
    call_sign: str = Field(default="", max_length=12)
    type: ChannelType = "network"
    identity: str = Field(default="", max_length=300)
    launch_year: int | None = None


class ConceptReply(_Lenient):
    channels: list[Concept] = Field(min_length=1)


class Assignment(_Lenient):
    show: str
    channels: list[str] = Field(min_length=1, max_length=2)
    premiere_year: int | None = None


class Skip(_Lenient):
    show: str
    reason: str = "the model left it out"


class AssignReply(_Lenient):
    assignments: list[Assignment] = Field(default_factory=list)
    unplaced: list[Skip] = Field(default_factory=list)


class Part(_Lenient):
    name: str = Field(min_length=1, max_length=40)
    days: str = "all"
    start: str = Field(pattern=HHMM)
    shows: list[str] = Field(min_length=1)
    primetime: bool = False


class PartsReply(_Lenient):
    dayparts: list[Part] = Field(min_length=1)


@dataclass
class _Stub:
    key: str  # what the model calls it in prompts
    group: str
    concept: Concept
    reuse: Channel | None = None
    shows: list[tuple[ShowRow, int | None]] = field(default_factory=list)  # (row, premiere year)


def group_of(row: ShowRow) -> str:
    if row.kind == "movie":
        return "movies"
    return row.category if row.category in CATEGORIES else "family"


def batches[T](items: Sequence[T], size: int) -> list[list[T]]:
    return [list(items[i : i + size]) for i in range(0, len(items), size)]


def _year(row: ShowRow) -> str:
    return str(row.debut_year) if row.debut_year else "year unknown"


def _line(key: str, row: ShowRow) -> str:
    extra = f", {row.network}" if row.network else ""
    post = " [after 2010: needs premiere_year 1990-2010]" if row.after_2010 else ""
    return f"{key}: {row.title} ({_year(row)}, {row.category}{extra}){post}"


def _channel_line(stub: _Stub) -> str:
    c = stub.concept
    kind = "existing" if stub.reuse else "new"
    return f"{stub.key}: {c.name} ({kind} {c.type}): {c.identity}"


def concepts_for(
    llm: LLM, group: str, rows: Sequence[ShowRow], existing: Sequence[Channel], log: Transcript
) -> list[Concept]:
    want = max(1, min(MAX_CHANNELS_PER_GROUP, math.ceil(len(rows) / SHOWS_PER_CHANNEL)))
    sample = rows[:: max(1, len(rows) // CONCEPT_SAMPLE)][:CONCEPT_SAMPLE]
    movie = group == "movies"
    kinds = '"movie"' if movie else '"network" (day-parts) or "marathon" (one to a few shows)'
    taken = ", ".join(c.name for c in existing)
    user = (
        f"Design {want} cable channel{'s' if want > 1 else ''} for {len(rows)}"
        f" {'movies' if movie else group + ' shows'} on this system. Already on the dial: "
        f"{taken}. Don't copy those; shows may still go on them later.\n"
        f"Some of the titles:\n" + "\n".join(_line("-", r) for r in sample) + "\n\n"
        'Reply as {"channels": [{"name": "channel name", "call_sign": "up to 6'
        f' letters", "type": {kinds}, "identity": "one sentence: audience, genre, feel",'
        ' "launch_year": 1990-2010}]}'
    )

    def check(reply: ConceptReply) -> None:
        problems = []
        if len(reply.channels) > want + 2:
            problems.append(f"at most {want + 2} channels")
        names = [c.name.strip().lower() for c in reply.channels]
        if len(set(names)) != len(names):
            problems.append("channel names must differ")
        for c in reply.channels:
            if movie != (c.type == "movie"):
                problems.append(f"{c.name}: type must be {kinds}")
        if problems:
            raise InvalidReply("\n".join(problems))

    reply = ask_json(llm, SYSTEM, user, ConceptReply, check, transcript=log)
    if reply is None:
        # The group still gets a channel, named plainly, so its shows can be placed.
        name = "Movie Channel" if movie else f"{group.title()} Channel"
        return [Concept(name=name, type="movie" if movie else "network", identity=f"{group}")]
    return reply.channels


def assign(
    llm: LLM,
    rows: Sequence[ShowRow],
    stubs: Sequence[_Stub],
    log: Transcript,
) -> tuple[dict[str, list[tuple[str, int | None]]], dict[str, str]]:
    """{show id: [(stub key, premiere year)]} and {show id: reason} for one batch."""
    keys = {f"s{i + 1}": r for i, r in enumerate(rows)}
    by_key = {s.key: s for s in stubs}
    user = (
        "Put each show on the channel where it fits best: one channel, or two only when it"
        " clearly belongs on both. Movies go on movie channels; shows never do. For shows marked"
        " after 2010, give premiere_year: the year it would have premiered on that channel.\n"
        "Channels:\n" + "\n".join(_channel_line(s) for s in stubs) + "\n\n"
        "Shows:\n" + "\n".join(_line(k, r) for k, r in keys.items()) + "\n\n"
        'Reply as {"assignments": [{"show": "s1", "channels": ["<channel key>"],'
        ' "premiere_year": null}], "unplaced": [{"show": "s2", "reason": "why"}]}.'
        " Every show key appears exactly once."
    )

    def check(reply: AssignReply) -> None:
        problems = []
        seen: list[str] = [a.show for a in reply.assignments] + [u.show for u in reply.unplaced]
        for k in sorted(set(seen) - set(keys)):
            problems.append(f"{k} is not a show key")
        for k in sorted({k for k in seen if seen.count(k) > 1}):
            problems.append(f"{k} appears more than once")
        missing = [k for k in keys if k not in seen]
        if missing:
            problems.append(f"missing shows: {', '.join(missing)}")
        for a in reply.assignments:
            row = keys.get(a.show)
            for ch in a.channels:
                stub = by_key.get(ch)
                if stub is None:
                    problems.append(f"{a.show}: {ch} is not a channel key")
                elif row is not None and (row.kind == "movie") != (stub.concept.type == "movie"):
                    problems.append(f"{a.show}: {row.kind}s can't go on {stub.concept.name}")
        if problems:
            raise InvalidReply("\n".join(problems))

    reply = ask_json(llm, SYSTEM, user, AssignReply, check, transcript=log)
    if reply is None:
        return {}, {r.id: "the model gave no valid channel for it" for r in rows}
    placed = {
        keys[a.show].id: [(ch, a.premiere_year) for ch in dict.fromkeys(a.channels)]
        for a in reply.assignments
    }
    skipped = {keys[u.show].id: u.reason.strip() or "the model left it out" for u in reply.unplaced}
    return placed, skipped


def dayparts_for(llm: LLM, channel: ProposedChannel, log: Transcript) -> list[DayPartPlan]:
    shows = [s for s in channel.shows if s.kind == "show"]
    if channel.type != "network" or len(shows) < 2:
        return []
    keys = {f"s{i + 1}": s for i, s in enumerate(shows)}
    user = (
        f"Plan the week of {channel.name} ({channel.identity}) as day-parts, the way a"
        " cable channel did: mornings, after school, prime time from 20:00, late night and so on."
        " Each part runs until the next one starts. Give weekdays and weekends a part starting"
        " at 00:00. Every show should be in at least one part.\n"
        "Shows:\n"
        + "\n".join(
            f"{k}: {s.title} ({s.debut.year if s.debut else '?'}, {s.category})"
            for k, s in keys.items()
        )
        + '\n\nReply as {"dayparts": [{"name": "Prime Time", "days": "weekdays|weekends|all",'
        ' "start": "HH:MM", "shows": ["s1"], "primetime": true}]}'
    )

    def check(reply: PartsReply) -> None:
        problems = []
        for p in reply.dayparts:
            if p.days not in ("weekdays", "weekends", "all"):
                problems.append(f"{p.name}: days is weekdays, weekends or all")
            bad = [k for k in p.shows if k not in keys]
            if bad:
                problems.append(f"{p.name}: unknown show keys {', '.join(bad)}")
        if problems:
            raise InvalidReply("\n".join(problems))

    reply = ask_json(llm, SYSTEM, user, PartsReply, check, transcript=log)
    if reply is None:
        return []
    return [
        DayPartPlan(
            name=p.name.strip(),
            days=p.days,  # type: ignore[arg-type]  # checked above
            start=p.start,
            shows=[keys[k].show for k in dict.fromkeys(p.shows)],
            primetime=p.primetime,
        )
        for p in reply.dayparts
    ]


def build_proposal(
    rows: Sequence[ShowRow],
    catalog: Catalog,
    index: Mapping[str, LibraryItem],
    llm: LLM,
    now: datetime,
    shows_file: str,
    batch_size: int = 40,
) -> Proposal:
    """A proposal for the kept rows. `catalog` is the effective catalog (YAML plus the admin
    overlay), `index` the server's library. Nothing is saved."""
    log = Transcript()
    carried: dict[str, list[str]] = {}
    for c in catalog.carriage.carriage:
        carried.setdefault(c.show, [])
        if c.channel not in carried[c.show]:
            carried[c.show].append(c.channel)
    already = [
        AlreadyCarried(show=r.id, title=r.title, channels=carried[r.id])
        for r in rows
        if r.id in carried
    ]
    unplaced: list[Unplaced] = []
    todo: dict[str, list[ShowRow]] = {}
    for r in rows:
        if r.id in carried:
            continue
        if r.kind == "special":
            unplaced.append(
                Unplaced(
                    show=r.id,
                    title=r.title,
                    reason="holiday specials are placed through the holiday calendar, not the"
                    " channel builder (yet)",
                )
            )
            continue
        todo.setdefault(group_of(r), []).append(r)

    existing = list(catalog.channels.channels)
    stubs: list[_Stub] = []
    reused: dict[str, _Stub] = {}  # one stub per existing channel, across groups
    for group in GROUP_ORDER:
        group_rows = todo.get(group, [])
        if not group_rows:
            continue
        concepts = concepts_for(llm, group, group_rows, existing, log)
        mine = [
            _Stub(key=f"{group}-{i + 1}", group=group, concept=c) for i, c in enumerate(concepts)
        ]
        stubs += mine
        reusable = [
            reused.setdefault(
                ch.id,
                _Stub(
                    key=ch.id,
                    group=group,
                    concept=Concept(name=ch.name, type=ch.type, identity="existing channel"),
                    reuse=ch,
                ),
            )
            for ch in existing
            if (ch.type == "movie") == (group == "movies")
        ]
        options = mine + reusable
        by_key = {s.key: s for s in options}
        for chunk in batches(group_rows, batch_size):
            placed, skipped = assign(llm, chunk, options, log)
            for r in chunk:
                if r.id in placed:
                    for key, year in placed[r.id]:
                        stub = by_key[key]
                        if stub.reuse is not None and stub not in stubs:
                            stubs.append(stub)
                        stub.shows.append((r, year))
                else:
                    reason = skipped.get(r.id, "the model left it out")
                    unplaced.append(Unplaced(show=r.id, title=r.title, reason=reason))

    channels = _channels(stubs, catalog, index)
    for ch in channels:
        if not ch.reuse:
            ch.dayparts = dayparts_for(llm, ch, log)
    notes = [f"model calls: {log.calls}, retries: {log.retries}"]
    notes += [f"gave up on a reply: {f}" for f in log.failures]
    return Proposal(
        created_at=now.isoformat(timespec="seconds"),
        model=llm.model,
        shows_file=shows_file,
        base_version=catalog.version,
        channels=channels,
        already_carried=already,
        unplaced=unplaced,
        notes=notes,
    )


def _channels(
    stubs: Sequence[_Stub], catalog: Catalog, index: Mapping[str, LibraryItem]
) -> list[ProposedChannel]:
    """Dates, numbers, call signs and ids: all deterministic."""
    numbers = taken_numbers(catalog)
    signs = taken_call_signs(catalog)
    ids = {c.id for c in catalog.channels.channels}
    out: list[ProposedChannel] = []
    for stub in stubs:
        if not stub.shows:
            continue
        reuse = stub.reuse
        premieres = {
            r.id: pretend_premiere(r, premiere_year(r, y)) if r.after_2010 else None
            for r, y in stub.shows
        }
        firsts = [start_year(r, premieres[r.id]) for r, _ in stub.shows]
        if reuse is not None:
            launch = reuse.launch.year if reuse.launch else None
        else:
            launch = launch_year(stub.concept.launch_year, firsts)
        shows = []
        for (r, _), first in zip(stub.shows, firsts, strict=True):
            item = index.get(r.id)
            debut = item.first_air if item and item.first_air else r.first_aired
            premiere = premieres[r.id]
            if premiere is not None and launch is not None and premiere.year < launch:
                premiere = pretend_premiere(r, launch)
                first = launch
            shows.append(
                ProposedShow(
                    show=r.id,
                    title=r.title,
                    kind="movie" if r.kind == "movie" else "show",
                    category=r.category,
                    debut=debut,
                    from_year=clamp_year(max(first, launch or FIRST_YEAR)),
                    premiere=premiere,
                    on_nas=r.on_nas or bool(item and item.episodes),
                    in_library=item is not None,
                )
            )
        if reuse is not None:
            ident = sorted(reuse.identity, key=lambda i: i.from_)[-1]
            out.append(
                ProposedChannel(
                    id=reuse.id,
                    reuse=True,
                    name=reuse.name,
                    call_sign=ident.call_sign,
                    number=ident.number,
                    type=reuse.type,
                    launch_year=launch,
                    audience=stub.group,
                    identity="existing channel",
                    shows=shows,
                )
            )
            continue
        channel_id = new_channel_id(stub.concept.name, ids)
        ids.add(channel_id)
        assert channel_id.startswith(CUSTOM_PREFIX)
        out.append(
            ProposedChannel(
                id=channel_id,
                reuse=False,
                name=stub.concept.name.strip(),
                call_sign=call_sign(stub.concept.call_sign, stub.concept.name, signs),
                number=next_number(stub.group, numbers),
                type=stub.concept.type,
                launch_year=min(launch or LAST_YEAR, LAST_YEAR),
                audience=stub.group,
                identity=stub.concept.identity.strip(),
                shows=shows,
            )
        )
    return out
