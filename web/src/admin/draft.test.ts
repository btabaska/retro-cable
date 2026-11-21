import { describe, expect, it } from "vitest";
import type { AdminChannelRow, LibraryEntry } from "./api";
import {
  carriageLine,
  carriedOn,
  channelMeta,
  dayChip,
  emptyDraft,
  groupLibrary,
  nextFreeNumber,
  readyToSave,
  yearsLine,
} from "./draft";

const row = (over: Partial<AdminChannelRow>): AdminChannelRow => ({
  id: "ktvu",
  name: "KTVU (Fox)",
  number: 2,
  call_sign: "KTVU",
  type: "network",
  library: false,
  launch: null,
  custom: false,
  shows: 12,
  ...over,
});

const entry = (over: Partial<LibraryEntry>): LibraryEntry => ({
  id: "johnny-bravo",
  title: "Johnny Bravo",
  kind: "show",
  first_year: 1997,
  last_year: 2004,
  episodes: 67,
  channels: [],
  shifted: false,
  ...over,
});

describe("admin drafts", () => {
  it("offers the lowest free number", () => {
    expect(nextFreeNumber([row({ number: 2 }), row({ number: 3 }), row({ number: 5 })])).toBe(4);
    expect(nextFreeNumber([])).toBe(2);
  });

  it("enables save once number, name and a show are set", () => {
    const draft = emptyDraft();
    expect(readyToSave(draft)).toBe(false);
    expect(readyToSave({ ...draft, number: 50, name: "Toons" })).toBe(false);
    expect(readyToSave({ ...draft, number: 50, name: "Toons", shows: [{ show: "johnny-bravo", kind: "show" }] })).toBe(true);
  });

  it("describes channels in the list", () => {
    expect(channelMeta(row({}))).toBe("Network · 12 shows");
    expect(channelMeta(row({ library: true, shows: 1 }))).toBe("Library · 1 show");
    expect(channelMeta(row({ launch: "2001-09-02" }))).toBe("from 2001 · 12 shows");
  });

  it("describes carriage windows", () => {
    expect(carriageLine({ show: "a", kind: "show" })).toBe("every year");
    expect(carriageLine({ show: "a", kind: "show", from_year: 2002 })).toBe("from 2002");
    expect(carriageLine({ show: "a", kind: "show", from_year: 2001, until_year: 2006 })).toBe("2001–2006");
    expect(carriageLine({ show: "a", kind: "show", from_year: 1995, until_year: 1995 })).toBe("1995 only");
  });

  it("groups the library: shows on no channel first", () => {
    const free = entry({});
    const carried = entry({ id: "the-simpsons", channels: [{ id: "ktvu", name: "KTVU (Fox)", number: 2 }] });
    expect(groupLibrary([carried, free])).toEqual({ free: [free], carried: [carried] });
    expect(carriedOn(carried)).toBe("on 2 KTVU (Fox)");
    expect(yearsLine(free)).toBe("1997–2004 · 67 ep");
    expect(yearsLine(entry({ kind: "movie", first_year: 1998, last_year: 1998 }))).toBe("1998 · movie");
  });

  it("labels day chips", () => {
    expect(dayChip("2000-10-29")).toBe("Sun 29");
  });
});
