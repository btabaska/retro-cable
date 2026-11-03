"""A fake local model for the channel builder: it reads the builder's prompts and answers like
a cooperative model would, with hooks to misbehave. No network anywhere in these tests."""

import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from retro_cable.channel_builder.llm import Message

# A handful of titles only; the real list stays on Brandon's Desktop.
SHOWS_CSV = """﻿action,review,category,title,kind,first_aired,after_2010,original_network,on_nas,already_carried,tmdb_id,aka,notes,source
keep,ok,kids,Courage the Cowardly Dog,show,1999-11-12,,Cartoon Network,yes,,tv:1,,,
add,new,kids,Johnny Bravo,show,1997-07-07,,Cartoon Network,yes,,tv:2,,,
keep,ok,kids,The Simpsons,show,1989-12-17,,FOX,yes,yes,tv:3,,,
add,new,teen,Gravity Falls,show,2012-06-15,yes,Disney Channel,,,tv:4,,,
remove,double-check,preschool,Barney & Friends,show,1992-04-06,,PBS,,,tv:5,,,
keep,ok,family,A Charlie Brown Christmas,special,1965-12-09,,CBS,,,tv:6,,,
keep,ok,family,Halloweentown,movie,1998-10-17,,Disney Channel,,,mv:7,,,
add,new,adult,Psych,show,2006-07-07,,USA Network,,,tv:8,,,
add,new,kids,Pixel Patrol,show,2014-03-02,yes,Made Up,,,,,a made-up title for tests,
add,new,family,Snow Day Rescue,movie,1999,,Made Up,,,,,a made-up title for tests,
"""


def lines_after(text: str, header: str) -> list[str]:
    block = text.split(header, 1)[1].split("\n\n", 1)[0]
    return [line for line in block.strip().splitlines() if line.strip()]


@dataclass
class FakeLLM:
    """Answers concept, placement and day-part prompts. `hooks` can override a kind of
    prompt ("concepts", "assign", "dayparts") with a function of the prompt text."""

    model: str = "fake-local"
    hooks: dict[str, Callable[[str, int], str]] = field(default_factory=dict)
    calls: list[str] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)

    def complete(self, messages: list[Message]) -> str:
        user = messages[1]["content"]
        kind = (
            "concepts"
            if user.startswith("Design")
            else "assign"
            if user.startswith("Put each show")
            else "dayparts"
        )
        n = self.counts.get(kind, 0)
        self.counts[kind] = n + 1
        self.calls.append(kind)
        if kind in self.hooks:
            return self.hooks[kind](user, n)
        reply: str = getattr(self, kind)(user)
        return reply

    def concepts(self, user: str) -> str:
        want = int(re.match(r"Design (\d+)", user).group(1))  # type: ignore[union-attr]
        movie = "movies on this system" in user
        group = re.search(r"for \d+ (\w+)", user).group(1)  # type: ignore[union-attr]
        return json.dumps(
            {
                "channels": [
                    {
                        "name": f"{group.title()} Movies {i}"
                        if movie
                        else f"{group.title()} TV {i}",
                        "call_sign": f"{group[:3]}{i}",
                        "type": "movie" if movie else "network",
                        "identity": f"{group} channel {i}",
                        "launch_year": 1995,
                    }
                    for i in range(1, want + 1)
                ]
            }
        )

    def assign(self, user: str) -> str:
        channels = [line.split(":")[0] for line in lines_after(user, "Channels:\n")]
        shows = [line.split(":")[0] for line in lines_after(user, "Shows:\n")]
        return json.dumps(
            {
                "assignments": [
                    {"show": s, "channels": [channels[0]], "premiere_year": None} for s in shows
                ],
                "unplaced": [],
            }
        )

    def dayparts(self, user: str) -> str:
        shows = [line.split(":")[0] for line in lines_after(user, "Shows:\n")]
        return json.dumps(
            {
                "dayparts": [
                    {"name": "Overnight", "days": "all", "start": "00:00", "shows": shows},
                    {"name": "After School", "days": "weekdays", "start": "15:00",
                     "shows": shows[:1]},
                    {"name": "Prime Time", "days": "all", "start": "20:00", "shows": shows[-1:],
                     "primetime": True},
                ]
            }
        )  # fmt: skip


@pytest.fixture
def shows_csv(tmp_path: Path) -> Path:
    path = tmp_path / "shows.csv"
    path.write_text(SHOWS_CSV, encoding="utf-8")
    return path
