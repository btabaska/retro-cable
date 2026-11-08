"""The deterministic parts of the channel builder: the show list, dates, numbers, call signs,
day-part templates, and the model wrapper's parsing and retries (with a fake model)."""

import io
import json
import urllib.error
import urllib.request
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel

from retro_cable.channel_builder.llm import (
    InvalidReply,
    LLMError,
    Message,
    OpenAIChat,
    ReplyLog,
    Transcript,
    ask_json,
    extract_json,
)
from retro_cable.channel_builder.proposal import (
    ChannelType,
    DayPartPlan,
    ProposedChannel,
    ProposedShow,
    call_sign,
    launch_year,
    next_number,
    premiere_year,
    pretend_premiere,
    start_year,
    templates_for,
)
from retro_cable.channel_builder.shows import ShowRow, known_ids, parse_first_aired, read_shows

# --- the show list -------------------------------------------------------------------------


def test_reads_only_kept_rows_and_resolves_ids(shows_csv: Path) -> None:
    known = known_ids({"the-simpsons": "The Simpsons"}, {"courage": "Courage the Cowardly Dog"})
    rows = {r.title: r for r in read_shows(shows_csv, known)}
    assert "Barney & Friends" not in rows  # action remove
    assert rows["The Simpsons"].id == "the-simpsons"  # catalog title
    assert rows["Courage the Cowardly Dog"].id == "courage"  # library title
    assert rows["Johnny Bravo"].id == "johnny-bravo"  # the slug `ingest scan` would give
    assert rows["Gravity Falls"].after_2010 and rows["Gravity Falls"].category == "teen"
    assert rows["Halloweentown"].kind == "movie"
    assert rows["A Charlie Brown Christmas"].kind == "special"
    assert rows["Courage the Cowardly Dog"].on_nas
    assert rows["Psych"].first_aired == date(2006, 7, 7)


def test_duplicate_titles_keep_the_first_row(tmp_path: Path) -> None:
    path = tmp_path / "s.csv"
    path.write_text(
        "action,category,title,kind,first_aired\n"
        "keep,kids,Recess,show,1997\nadd,teen,Recess,show,1997\n"
    )
    rows = read_shows(path)
    assert [(r.id, r.category) for r in rows] == [("recess", "kids")]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("1997-07-07", date(1997, 7, 7)),
        ("1997", date(1997, 1, 1)),
        ("1997-09", date(1997, 9, 1)),
        ("", None),
        ("circa 1990", None),
    ],
)
def test_first_aired_formats(text: str, expected: date | None) -> None:
    assert parse_first_aired(text) == expected


def test_a_carried_shows_tmdb_id_wins_over_its_title(tmp_path: Path) -> None:
    """The list says `The Adventures of Jimmy Neutron, Boy Genius`; the catalog carries `The
    Adventures of Jimmy Neutron` with the same TMDB id (overrides.yaml): one show, one id."""
    path = tmp_path / "s.csv"
    path.write_text(
        "action,category,title,kind,first_aired,tmdb_id\n"
        'keep,kids,"The Adventures of Jimmy Neutron, Boy Genius",show,2002-07-20,tv:2129\n'
        "keep,kids,Recess,show,1997,tv:1\n"
    )
    rows = read_shows(path, known_ids({}, {}), {"tv:2129": "the-adventures-of-jimmy-neutron"})
    assert [(r.id, r.tmdb_id, r.debut_year) for r in rows] == [
        ("the-adventures-of-jimmy-neutron", "tv:2129", 2002),
        ("recess", "tv:1", 1997),
    ]


# --- dates ---------------------------------------------------------------------------------


def row(first: date | None, after: bool = False, kind: str = "show") -> ShowRow:
    return ShowRow("x", "X", kind, "kids", first, after, "", False, "")  # type: ignore[arg-type]


def test_pretend_premieres_land_inside_1990_2010() -> None:
    gravity = row(date(2012, 6, 15), after=True)
    assert premiere_year(gravity) == 1992  # twenty years earlier
    assert premiere_year(gravity, 2004) == 2004  # the model's year, when it is in range
    assert premiere_year(gravity, 2015) == 1992
    assert premiere_year(row(date(2035, 1, 1), after=True)) == 2010
    assert pretend_premiere(gravity, 2004) == date(2004, 6, 15)  # same month and day
    assert pretend_premiere(row(date(2014, 1, 1), after=True), 1994) == date(1994, 9, 15)
    leap = row(date(2016, 2, 29), after=True)
    assert pretend_premiere(leap, 1995) == date(1995, 2, 28)  # Feb 29 falls back to Feb 28


def test_carriage_starts_when_the_show_can_air() -> None:
    assert start_year(row(date(1985, 5, 1)), None) == 1990  # older shows: from 1990
    assert start_year(row(date(1997, 7, 7)), None) == 1997
    assert start_year(row(None), None) == 1990
    assert start_year(row(date(2012, 6, 15), True), date(2004, 6, 15)) == 2004


def test_a_channel_never_launches_after_its_first_show() -> None:
    assert launch_year(None, [1997, 1999]) == 1997
    assert launch_year(1995, [1997, 1999]) == 1995  # the model's earlier era stands
    assert launch_year(2003, [1997, 1999]) == 1997  # but never later than a show
    assert launch_year(1980, [1997]) == 1997  # out of range: ignored


# --- numbers and call signs ----------------------------------------------------------------


def test_numbers_come_from_the_audience_band_and_skip_taken_ones() -> None:
    taken = {20, 21, 24, 27}
    assert next_number("kids", taken) == 22
    assert next_number("kids", taken) == 23
    assert next_number("kids", taken) == 25
    assert next_number("preschool", taken) == 12
    assert next_number("whatever", taken) == 100


def test_call_signs_are_cleaned_and_unique() -> None:
    taken = {"KTVU", "TOON"}
    assert call_sign("toon", "Toon Town", taken) == "TOON2"
    assert call_sign("pixel-kids!", "Pixel Kids", taken) == "PIXELK"
    assert call_sign("", "Saturday Morning Cartoons", taken) == "SMC"  # initials
    assert call_sign("", "Retro Action Network", taken) == "RAN"
    assert call_sign("", "Toon Disney", taken) == "TOON3"  # short initials: the first word
    assert len(call_sign("ABCDEF", "x", {"ABCDEF", *{f"ABCDE{i}" for i in range(2, 10)}})) <= 6


# --- day-part templates --------------------------------------------------------------------


def channel(dayparts: list[DayPartPlan], kind: ChannelType = "network") -> ProposedChannel:
    shows = [
        ProposedShow(show=s, title=s.title(), kind="show", category="kids", debut=None, from_year=1995)
        for s in ("a", "b", "c")
    ]  # fmt: skip
    return ProposedChannel(
        id="custom-toons", reuse=False, name="Toons", call_sign="TOONS", number=20,
        type=kind, launch_year=1995, audience="kids", identity="", shows=shows,
        dayparts=dayparts,
    )  # fmt: skip


def test_templates_cover_every_weekday_from_midnight() -> None:
    plan = [
        DayPartPlan(name="After School", days="weekdays", start="15:00", shows=["a", "zzz"]),
        DayPartPlan(name="Prime", days="all", start="20:00", shows=["b"], primetime=True),
    ]
    [t] = templates_for(channel(plan))
    assert t.id == "custom-toons-week" and t.from_ == date(1995, 1, 1)
    by_day: dict[str, list[tuple[str, list[str]]]] = {}
    for p in t.parts:
        by_day.setdefault(p.weekday, []).append((p.start, [e.show or "" for e in p.pool]))
    assert len(by_day) == 7
    for day, parts in by_day.items():
        assert parts[0] == ("00:00", ["a", "b", "c"]), day  # added: every show can air
        assert ("20:00", ["b"]) in parts
    assert ("15:00", ["a"]) in by_day["monday"]  # unknown show ids dropped
    assert all(start != "15:00" for start, _ in by_day["saturday"])
    assert all(p.primetime == (p.start == "20:00") for p in t.parts)


def test_shows_left_out_of_the_plan_join_midnight() -> None:
    plan = [DayPartPlan(name="Late", days="all", start="00:00", shows=["a"])]
    [t] = templates_for(channel(plan))
    assert {e.show for e in t.parts[0].pool} == {"a", "b", "c"}


def test_templates_start_when_every_part_can_air() -> None:
    # Before then no template covers the channel, so it plays everything all day rather than
    # going Off Air in a part whose shows haven't started yet.
    ch = channel([DayPartPlan(name="Prime", days="all", start="20:00", shows=["c"])])
    later = [s.model_copy(update={"from_year": 1999}) if s.show == "c" else s for s in ch.shows]
    [t] = templates_for(ch.model_copy(update={"shows": later}))
    assert t.from_ == date(1999, 1, 1)


def test_no_templates_without_a_plan_or_on_marathon_channels() -> None:
    assert templates_for(channel([])) == []
    plan = [DayPartPlan(name="All", days="all", start="00:00", shows=["a"])]
    assert templates_for(channel(plan, kind="marathon")) == []


# --- the model wrapper ---------------------------------------------------------------------


class Reply(BaseModel):
    value: int


class Scripted:
    model = "scripted"

    def __init__(self, *replies: str) -> None:
        self.replies = list(replies)
        self.seen: list[list[Message]] = []

    def complete(self, messages: list[Message]) -> str:
        self.seen.append(list(messages))
        return self.replies.pop(0)


def test_extract_json_allows_fences_and_preambles() -> None:
    assert extract_json('```json\n{"value": 1}\n```') == {"value": 1}
    assert extract_json('<think>hmm {"no": 1}</think>Sure: {"value": 2} done') == {"value": 2}


def test_ask_json_retries_with_the_problems_then_succeeds() -> None:
    llm = Scripted("not json", '{"value": "x"}', '{"value": 3}')
    log = Transcript()
    got = ask_json(llm, "sys", "user", Reply, retries=2, transcript=log)
    assert got == Reply(value=3)
    assert log.calls == 3 and log.retries == 2
    # The model sees its own reply and what was wrong with it.
    assert llm.seen[2][-1]["role"] == "user" and "value" in llm.seen[2][-1]["content"]


def test_ask_json_gives_up_after_the_retries() -> None:
    def odd_only(r: Reply) -> None:
        if r.value % 2 == 0:
            raise InvalidReply("value must be odd")

    llm = Scripted('{"value": 2}', '{"value": 4}')
    log = Transcript()
    assert ask_json(llm, "s", "u", Reply, odd_only, retries=1, transcript=log) is None
    assert log.failures and "odd" in log.failures[0]
    assert "odd" in llm.seen[1][-1]["content"]


def test_openai_chat_posts_to_the_local_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    sent: dict[str, Any] = {}

    def fake_urlopen(req: urllib.request.Request, timeout: float) -> io.BytesIO:
        sent["url"], sent["headers"] = req.full_url, dict(req.header_items())
        sent["body"] = json.loads(req.data)  # type: ignore[arg-type]
        return io.BytesIO(json.dumps({"choices": [{"message": {"content": "{}"}}]}).encode())

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setenv("RC_LLM_URL", "http://rig:4000/")
    monkeypatch.setenv("RC_LLM_KEY", "sk-test-not-real")
    monkeypatch.setenv("RC_LLM_MODEL", "q38")
    llm = OpenAIChat.from_env()
    assert "sk-test" not in repr(llm)  # the key never shows up in logs
    assert llm.complete([{"role": "user", "content": "hi"}]) == "{}"
    assert sent["url"] == "http://rig:4000/v1/chat/completions"
    assert sent["headers"]["Authorization"] == "Bearer sk-test-not-real"
    assert sent["body"]["model"] == "q38"
    assert sent["body"]["response_format"] == {"type": "json_object"}


def test_openai_chat_waits_out_a_busy_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    """The rig is shared: a model swap or a long job ahead of ours shows up as a 502 or a
    timeout, and one of those must not throw away an hour of replies."""
    tries: list[str] = []

    def flaky_urlopen(req: urllib.request.Request, timeout: float) -> io.BytesIO:
        tries.append(req.full_url)
        if len(tries) == 1:
            raise urllib.error.HTTPError(req.full_url, 502, "Bad Gateway", {}, None)  # type: ignore[arg-type]
        if len(tries) == 2:
            raise TimeoutError("timed out")
        return io.BytesIO(json.dumps({"choices": [{"message": {"content": "{}"}}]}).encode())

    monkeypatch.setattr(urllib.request, "urlopen", flaky_urlopen)
    llm = OpenAIChat(url="http://rig:4000", attempts=3, backoff=0)
    assert llm.complete([{"role": "user", "content": "hi"}]) == "{}"
    assert len(tries) == 3


def test_openai_chat_gives_up_and_never_retries_a_refusal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    codes = iter([503, 503, 401])
    tries: list[int] = []

    def failing_urlopen(req: urllib.request.Request, timeout: float) -> io.BytesIO:
        code = next(codes)
        tries.append(code)
        raise urllib.error.HTTPError(req.full_url, code, "no", {}, None)  # type: ignore[arg-type]

    monkeypatch.setattr(urllib.request, "urlopen", failing_urlopen)
    with pytest.raises(LLMError, match="503"):
        OpenAIChat(attempts=2, backoff=0).complete([{"role": "user", "content": "hi"}])
    with pytest.raises(LLMError, match="401"):
        OpenAIChat(attempts=3, backoff=0).complete([{"role": "user", "content": "hi"}])
    assert tries == [503, 503, 401]  # a bad key is not worth asking again


class Counting:
    model = "fake"

    def __init__(self, fail_after: int | None = None) -> None:
        self.calls = 0
        self.fail_after = fail_after

    def complete(self, messages: list[Message]) -> str:
        if self.fail_after is not None and self.calls >= self.fail_after:
            raise LLMError("model endpoint unreachable", transient=True)
        self.calls += 1
        return json.dumps({"echo": messages[-1]["content"]})


def test_reply_log_replays_a_run_cut_short(tmp_path: Path) -> None:
    path = tmp_path / "replies.jsonl"
    first = ReplyLog(Counting(fail_after=2), path)
    assert first.complete([{"role": "user", "content": "a"}]) == '{"echo": "a"}'
    assert first.complete([{"role": "user", "content": "b"}]) == '{"echo": "b"}'
    with pytest.raises(LLMError):
        first.complete([{"role": "user", "content": "c"}])
    with path.open("a") as f:
        f.write('{"key": "half a li')  # the process died mid-write

    inner = Counting()
    resumed = ReplyLog(inner, path, replay=True)
    for text in "abc":
        assert resumed.complete([{"role": "user", "content": text}]) == f'{{"echo": "{text}"}}'
    assert inner.calls == 1 and resumed.replayed == 2  # only "c" went to the model
    assert "sk-" not in path.read_text()

    fresh = ReplyLog(Counting(), path)  # without replay the old replies are dropped
    assert fresh.replayed == 0 and path.read_text() == ""
