import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { AdminApp } from "./AdminApp";
import {
  type AdminApi,
  type AdminChannel,
  type AdminChannelList,
  type AdminChannelRow,
  type CheckResult,
  type DraftRequest,
  type LibraryEntry,
  type MediaProgress,
  NotAvailable,
  type Preview,
} from "./api";

const row = (id: string, number: number, name: string, custom = false): AdminChannelRow => ({
  id,
  name,
  number,
  call_sign: name.slice(0, 4).toUpperCase(),
  type: "network",
  library: custom,
  launch: null,
  custom,
  shows: 2,
});

const LIBRARY: LibraryEntry[] = [
  { id: "courage", title: "Courage the Cowardly Dog", kind: "show", first_year: 1999, last_year: 2002, debut: "1999-11-12", episodes: 52, channels: [], shifted: false },
  { id: "johnny-bravo", title: "Johnny Bravo", kind: "show", first_year: 1997, last_year: 2004, episodes: 67, channels: [], shifted: false },
  {
    id: "the-simpsons",
    title: "The Simpsons",
    kind: "show",
    first_year: 1989,
    last_year: 2010,
    episodes: 400,
    channels: [{ id: "ktvu", name: "KTVU (Fox)", number: 2 }],
    shifted: false,
  },
];

const state = (revision: number, can_undo: boolean) => ({ revision, catalog_version: `v${revision}`, can_undo });
const ok: CheckResult = { ok: true, errors: [], warnings: [], catalog_version: "v1" };

function fakeApi(): AdminApi & { channels: AdminChannelRow[] } {
  const channels = [row("ktvu", 2, "KTVU (Fox)"), row("disney", 30, "Disney Channel")];
  const list = (): AdminChannelList => ({ state: state(channels.length, false), channels: [...channels] });
  const preview: Preview = {
    check: ok,
    channel: null,
    days: [
      {
        date: "2000-10-29",
        weekday: "Sunday",
        on_air: true,
        slots: [
          {
            kind: "episode",
            start: "2000-10-29T05:00:00-05:00",
            end: "2000-10-29T05:22:00-05:00",
            seconds: 1320,
            title: "Johnny Bravo",
            show: "johnny-bravo",
            episode_title: "Episode 1",
            season: 1,
            episode: 1,
            air_date: "1997-07-07",
            new: false,
            transplant: false,
            holiday: null,
            holiday_name: null,
            description: "",
          },
          {
            kind: "episode",
            start: "2000-10-29T05:22:00-05:00",
            end: "2000-10-29T05:44:00-05:00",
            seconds: 1320,
            title: "Courage the Cowardly Dog",
            show: "courage",
            episode_title: "Episode 2",
            season: 1,
            episode: 2,
            air_date: "1999-11-12",
            new: false,
            transplant: false,
            holiday: "halloween",
            holiday_name: "Halloween",
            description: "",
          },
        ],
      },
    ],
  };
  return {
    channels,
    listChannels: vi.fn(() => Promise.resolve(list())),
    getChannel: vi.fn(() => Promise.reject(new Error("unused"))),
    searchLibrary: vi.fn((q: string) =>
      Promise.resolve(LIBRARY.filter((e) => e.title.toLowerCase().includes(q.toLowerCase()))),
    ),
    check: vi.fn(() => Promise.resolve(ok)),
    preview: vi.fn(() => Promise.resolve(preview)),
    saveChannel: vi.fn((req: DraftRequest) => {
      if (req.draft.number === 30) {
        return Promise.resolve({
          saved: false as const,
          check: {
            ok: false,
            errors: [{ field: "number", message: "Number 30 is already Disney Channel." }],
            warnings: [],
            catalog_version: null,
          },
        });
      }
      channels.push(row("custom-toons", req.draft.number, req.draft.name, true));
      return Promise.resolve({
        saved: true as const,
        result: { channel_id: "custom-toons", message: `Created ${req.draft.name}`, check: ok, state: state(9, true) },
      });
    }),
    deleteChannel: vi.fn(() => Promise.reject(new Error("unused"))),
    setPremiere: vi.fn(() => Promise.reject(new Error("unused"))),
    getMediaProgress: vi.fn(() => Promise.reject(new NotAvailable("no"))),
    getStationRuns: vi.fn(() => Promise.resolve({ auto_apply: true, runs: [] })),
    getReports: vi.fn(() => Promise.resolve({ schedule: "Sundays at 8 AM", reports: [] })),
    undo: vi.fn(() => {
      channels.pop();
      return Promise.resolve({ message: "Restored", state: state(10, false) });
    }),
  };
}

async function fillNewChannel(user: ReturnType<typeof userEvent.setup>, number: string) {
  await user.click(await screen.findByRole("button", { name: "+ New channel" }));
  expect(screen.getByRole("heading", { name: "New channel" })).toBeInTheDocument();
  await user.type(screen.getByLabelText("Number"), number);
  await user.type(screen.getByLabelText("Call sign"), "toons");
  expect(screen.getByLabelText("Call sign")).toHaveValue("TOONS");
  await user.type(screen.getByLabelText("Name"), "Brandon's Toons");
  await user.click(screen.getByRole("switch"));
  expect(screen.getByText("Available in every year")).toBeInTheDocument();

  await user.click(screen.getByRole("button", { name: "+ Add show" }));
  const picker = await screen.findByTestId("show-picker");
  expect(within(picker).getByText("Not on any channel")).toBeInTheDocument();
  expect(within(picker).getByText(/on 2 KTVU \(Fox\)/)).toBeInTheDocument();
  await user.click(within(picker).getByRole("button", { name: "Add Courage the Cowardly Dog" }));
  await user.click(within(picker).getByRole("button", { name: "Add Johnny Bravo" }));
  expect(within(picker).getByRole("button", { name: "Johnny Bravo is on this channel" })).toBeDisabled();
  await user.keyboard("{Escape}");
  await waitFor(() => {
    expect(screen.queryByTestId("show-picker")).toBeNull();
  });
}

describe("AdminApp", () => {
  it("lists channels by number with a New channel button", async () => {
    render(<AdminApp api={fakeApi()} />);
    const list = await screen.findByTestId("admin-channels");
    expect(within(list).getAllByRole("button").map((b) => b.textContent)).toEqual([
      expect.stringContaining("KTVU (Fox)"),
      expect.stringContaining("Disney Channel"),
    ]);
    expect(screen.getByRole("navigation", { name: "Admin sections" })).toHaveTextContent("Channels");
  });

  it("creates a channel from library shows, previews it, saves and undoes", async () => {
    const user = userEvent.setup();
    const api = fakeApi();
    render(<AdminApp api={api} />);
    await fillNewChannel(user, "50");
    const shows = screen.getByTestId("editor-shows");
    expect(within(shows).getByText("Courage the Cowardly Dog")).toBeInTheDocument();
    expect(within(shows).getByText("Johnny Bravo")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Preview week" }));
    const day = await screen.findByTestId("preview-day");
    expect(within(day).getByText("5:00")).toBeInTheDocument();
    expect(within(day).getByText("Johnny Bravo")).toBeInTheDocument();
    expect(within(day).getByText("HALLOWEEN")).toBeInTheDocument();
    expect(api.saveChannel).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "Close" }));

    await user.click(screen.getByRole("button", { name: "Save" }));
    const toast = await screen.findByRole("status");
    expect(toast).toHaveTextContent("Created Brandon's Toons");
    expect(await screen.findByTestId("admin-channel-custom-toons")).toHaveTextContent("50");
    const saved = vi.mocked(api.saveChannel).mock.calls[0]?.[0];
    expect(saved?.draft).toMatchObject({
      number: 50,
      call_sign: "TOONS",
      library: true,
      shows: [{ show: "courage" }, { show: "johnny-bravo" }],
    });

    await user.click(within(toast).getByRole("button", { name: "Undo" }));
    expect(await screen.findByRole("status")).toHaveTextContent("Restored");
    await waitFor(() => {
      expect(screen.queryByTestId("admin-channel-custom-toons")).toBeNull();
    });
  });

  it("shows failed checks in a summary and next to the field", async () => {
    const user = userEvent.setup();
    render(<AdminApp api={fakeApi()} />);
    await fillNewChannel(user, "30");
    await user.click(screen.getByRole("button", { name: "Save" }));
    const summary = await screen.findByTestId("check-errors");
    expect(summary).toHaveTextContent("Can't save: 1 problem");
    expect(summary).toHaveFocus();
    const number = screen.getByLabelText("Number");
    expect(number).toHaveAttribute("aria-invalid", "true");
    expect(document.getElementById(number.getAttribute("aria-describedby") ?? "")).toHaveTextContent(
      "Number 30 is already Disney Channel.",
    );
  });

  it("asks before discarding unsaved changes", async () => {
    const user = userEvent.setup();
    render(<AdminApp api={fakeApi()} />);
    await user.click(await screen.findByRole("button", { name: "+ New channel" }));
    await user.type(screen.getByLabelText("Name"), "Draft");
    await user.click(screen.getByRole("button", { name: "‹ Channels" }));
    expect(screen.getByRole("dialog", { name: "Discard changes" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Discard" }));
    expect(await screen.findByTestId("admin-channels")).toBeInTheDocument();
  });

  it("shows a loading state, not an empty channel, while a channel loads", async () => {
    const user = userEvent.setup();
    const api = fakeApi();
    let finish: (c: Awaited<ReturnType<AdminApi["getChannel"]>>) => void = () => undefined;
    vi.mocked(api.getChannel).mockReturnValueOnce(
      new Promise((resolve) => {
        finish = resolve;
      }),
    );
    render(<AdminApp api={api} />);
    await user.click(await screen.findByTestId("admin-channel-disney"));

    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("Disney Channel");
    expect(screen.getByLabelText("Loading channel")).toHaveAttribute("aria-busy", "true");
    expect(screen.queryByLabelText("Number")).toBeNull();
    expect(screen.queryByText(/No shows yet/)).toBeNull();
    expect(screen.queryByText(/read-only/)).toBeNull();
    expect(screen.queryByRole("button", { name: "Save" })).toBeNull();

    finish({
      id: "disney",
      custom: false,
      draft: {
        number: 30,
        call_sign: "DISN",
        name: "Disney Channel",
        type: "network",
        library: false,
        launch_year: 1990,
        shows: [{ show: "johnny-bravo", kind: "show" }],
      },
    });
    expect(await screen.findByLabelText("Number")).toHaveValue("30");
    expect(screen.queryByLabelText("Loading channel")).toBeNull();
    expect(screen.getByText(/read-only/)).toBeInTheDocument();
    expect(within(screen.getByTestId("editor-shows")).getByText("Johnny Bravo")).toBeInTheDocument();
  });

  it("offers a retry when a channel can't load", async () => {
    const user = userEvent.setup();
    const api = fakeApi();
    render(<AdminApp api={api} />);
    await user.click(await screen.findByTestId("admin-channel-disney"));
    expect(await screen.findByText("Couldn't load this channel.")).toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("Disney Channel");
    expect(screen.queryByLabelText("Number")).toBeNull();
    vi.mocked(api.getChannel).mockResolvedValueOnce({
      id: "disney",
      custom: false,
      draft: { number: 30, call_sign: "DISN", name: "Disney Channel", type: "network", library: false, launch_year: 1990, shows: [] },
    });
    await user.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByLabelText("Number")).toHaveValue("30");
  });

  it("offers a retry when the channels can't load", async () => {
    const user = userEvent.setup();
    const api = fakeApi();
    vi.mocked(api.listChannels).mockRejectedValueOnce(new Error("down"));
    render(<AdminApp api={api} />);
    expect(await screen.findByText("Couldn't load channels.")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByTestId("admin-channels")).toBeInTheDocument();
  });
});

const disney = (over: Partial<AdminChannel> = {}): AdminChannel => ({
  id: "disney",
  custom: false,
  draft: {
    number: 30,
    call_sign: "DISN",
    name: "Disney Channel",
    type: "network",
    library: false,
    launch_year: 1990,
    shows: [
      { show: "johnny-bravo", kind: "show", premiere: null },
      { show: "courage", kind: "show", until_year: 1997, premiere: null },
    ],
  },
  shows: [
    {
      show: "johnny-bravo",
      kind: "show",
      debut: "1997-07-07",
      premiere: null,
      premiere_source: null,
      first_air: "1997-07-07",
      last_air: "2004-08-27",
      on_air_until: null,
      airs: true,
    },
    {
      show: "courage",
      kind: "show",
      debut: "1999-11-12",
      premiere: null,
      premiere_source: null,
      first_air: "1999-11-12",
      last_air: "2002-11-22",
      on_air_until: "1997-12-31",
      airs: false,
    },
  ],
  ...over,
});

const progress = (over: Partial<MediaProgress> = {}): MediaProgress => ({
  done: 1240,
  total: 2000,
  failed: 2,
  encoder: "cpu",
  gpu_busy: true,
  recent_seconds: Array.from({ length: 20 }, () => 30),
  shows: [
    { title: "Johnny Bravo", done: 67, total: 67, failed: 0, state: "done" },
    { title: "Courage the Cowardly Dog", done: 0, total: 52, failed: 0, state: "queued" },
    { title: "Dexter's Laboratory", done: 38, total: 52, failed: 2, state: "converting" },
  ],
  ...over,
});

describe("AdminApp M2", () => {
  it("shows read-only rows without chevrons and saves a pretend premiere on a YAML channel", async () => {
    const user = userEvent.setup();
    const api = fakeApi();
    vi.mocked(api.getChannel).mockResolvedValue(disney());
    vi.mocked(api.setPremiere).mockResolvedValueOnce({
      saved: true,
      result: {
        message: "Set the pretend premiere of Johnny Bravo",
        warnings: [],
        state: state(11, true),
        show: {
          show: "johnny-bravo",
          kind: "show",
          debut: "1997-07-07",
          premiere: "1995-09-08",
          premiere_source: "admin",
          first_air: "1995-09-08",
          last_air: "2002-10-28",
          on_air_until: null,
          airs: true,
        },
      },
    });
    render(<AdminApp api={api} />);
    await user.click(await screen.findByTestId("admin-channel-disney"));
    const shows = await screen.findByTestId("editor-shows");
    // D26: plain rows, no chevrons, no expand buttons.
    expect(within(shows).queryByText("⌄")).toBeNull();
    expect(within(shows).queryAllByRole("button", { expanded: false })).toHaveLength(0);

    const date = within(shows).getAllByLabelText("Pretend premiere")[0];
    if (!date) throw new Error("no premiere field");
    await user.type(date, "1995-09-08");
    await user.click(within(shows).getAllByRole("button", { name: "Set" })[0] as HTMLElement);
    expect(api.setPremiere).toHaveBeenCalledWith("disney", "johnny-bravo", "1995-09-08");
    expect(await screen.findByRole("status")).toHaveTextContent("Set the pretend premiere of Johnny Bravo");
    expect(screen.getByTestId("show-meta-0")).toHaveTextContent("Pretend premiere Sep 1995 · really Jul 1997");
    expect(screen.getByTestId("premiere-run-0")).toHaveTextContent("S1E1 airs Sep 8, 1995 · last episode Oct 2002");
  });

  it("flags a show that debuts after the channel stops carrying it", async () => {
    const user = userEvent.setup();
    const api = fakeApi();
    vi.mocked(api.getChannel).mockResolvedValue(disney());
    render(<AdminApp api={api} />);
    await user.click(await screen.findByTestId("admin-channel-disney"));
    const meta = await screen.findByTestId("show-meta-1");
    expect(meta).toHaveTextContent("Debuts Nov 1999 · won't air. Set a pretend premiere.");
    expect(meta.closest("li")).toHaveClass("adm-show-flag");
  });

  it("shows a refused premiere inline", async () => {
    const user = userEvent.setup();
    const api = fakeApi();
    vi.mocked(api.getChannel).mockResolvedValue(disney());
    vi.mocked(api.setPremiere).mockResolvedValueOnce({
      saved: false,
      check: {
        ok: false,
        errors: [{ field: "premiere", message: "Pick a date from 1990 to 2010." }],
        warnings: [],
        catalog_version: null,
      },
    });
    render(<AdminApp api={api} />);
    await user.click(await screen.findByTestId("admin-channel-disney"));
    const date = (await screen.findAllByLabelText("Pretend premiere"))[0] as HTMLElement;
    await user.type(date, "2012-06-15");
    await user.click(screen.getAllByRole("button", { name: "Set" })[0] as HTMLElement);
    expect(await screen.findByRole("alert")).toHaveTextContent("Pick a date from 1990 to 2010.");
    expect(date).toHaveAttribute("aria-invalid", "true");
  });

  it("sets a pretend premiere on a custom channel's show and saves it with the channel", async () => {
    const user = userEvent.setup();
    const api = fakeApi();
    render(<AdminApp api={api} />);
    await fillNewChannel(user, "50");
    await user.click(screen.getByRole("button", { name: /Courage the Cowardly Dog/ }));
    const date = screen.getByLabelText("Pretend premiere");
    expect(date).toHaveAttribute("min", "1990-01-01");
    expect(date).toHaveAttribute("max", "2010-12-31");
    await user.type(date, "1996-09-06");
    expect(screen.getByTestId("show-meta-0")).toHaveTextContent("Pretend premiere Sep 1996 · really Nov 1999");
    await user.click(screen.getByRole("button", { name: "Save" }));
    await screen.findByRole("status");
    const saved = vi.mocked(api.saveChannel).mock.calls[0]?.[0];
    expect(saved?.draft.shows?.[0]).toMatchObject({ show: "courage", premiere: "1996-09-06" });
  });

  it("clears a field's error as soon as it is edited, and the summary with it", async () => {
    const user = userEvent.setup();
    render(<AdminApp api={fakeApi()} />);
    await fillNewChannel(user, "30");
    await user.click(screen.getByRole("button", { name: "Save" }));
    expect(await screen.findByTestId("check-errors")).toBeInTheDocument();
    const number = screen.getByLabelText("Number");
    await user.clear(number);
    await user.type(number, "51");
    expect(number).not.toHaveAttribute("aria-invalid");
    expect(screen.queryByTestId("check-errors")).toBeNull();
  });

  it("opens the show row an error links to", async () => {
    const user = userEvent.setup();
    const api = fakeApi();
    vi.mocked(api.saveChannel).mockResolvedValueOnce({
      saved: false,
      check: {
        ok: false,
        errors: [{ field: "shows.1.premiere", message: "Pick a date from 1990 to 2010." }],
        warnings: [],
        catalog_version: null,
      },
    });
    render(<AdminApp api={api} />);
    await fillNewChannel(user, "50");
    await user.click(screen.getByRole("button", { name: "Save" }));
    await user.click(await screen.findByRole("link", { name: "Pick a date from 1990 to 2010." }));
    await waitFor(() => {
      expect(screen.getByLabelText("Pretend premiere")).toHaveFocus();
    });
  });

  it("gives focus back to the opener when a sheet closes, and to the title after a save", async () => {
    const user = userEvent.setup();
    render(<AdminApp api={fakeApi()} />);
    await fillNewChannel(user, "50");
    expect(screen.getByRole("button", { name: "+ Add show" })).toHaveFocus();
    const previewButton = screen.getByRole("button", { name: "Preview week" });
    await user.click(previewButton);
    await screen.findByTestId("preview-day");
    await user.click(screen.getByRole("button", { name: "Close" }));
    expect(previewButton).toHaveFocus();
    await user.click(screen.getByRole("button", { name: "Save" }));
    await screen.findByRole("status");
    await waitFor(() => {
      expect(screen.getByRole("heading", { level: 1, name: "Retro Cable" })).toHaveFocus();
    });
  });

  it("draws saved custom channels with a solid border, not the draft dashes", async () => {
    const api = fakeApi();
    api.channels.push(row("custom-toons", 50, "Toons", true));
    render(<AdminApp api={api} />);
    const custom = await screen.findByTestId("admin-channel-custom-toons");
    expect(custom).toHaveClass("adm-row-custom");
    expect(custom).not.toHaveClass("adm-row-draft");
    expect(custom).toHaveTextContent("Custom ·");
  });

  it("splits week-preview day chips over two lines", async () => {
    const user = userEvent.setup();
    render(<AdminApp api={fakeApi()} />);
    await fillNewChannel(user, "50");
    await user.click(screen.getByRole("button", { name: "Preview week" }));
    const chip = await screen.findByRole("tab", { name: "Sun 29" });
    expect(within(chip).getByText("Sun")).toHaveClass("adm-chip-day");
    expect(within(chip).getByText("29")).toHaveClass("adm-chip-date");
  });

  it("shows conversion progress on the Media tab", async () => {
    const user = userEvent.setup();
    const api = fakeApi();
    vi.mocked(api.getMediaProgress).mockResolvedValue(progress());
    render(<AdminApp api={api} />);
    await user.click(await screen.findByRole("button", { name: "Media" }));
    const summary = await screen.findByTestId("media-summary");
    expect(summary).toHaveTextContent("62%");
    // 760 files left at 30 s each: 6 h 20 m, rounded to ten minutes.
    expect(summary).toHaveTextContent("1,240 of 2,000 files · about 6 h 20 m left");
    expect(summary).toHaveTextContent("Encoding on CPU (GPU busy)");
    const rows = within(screen.getByTestId("media-shows")).getAllByRole("listitem");
    expect(rows.map((r) => r.textContent)).toEqual([
      expect.stringContaining("Dexter's Laboratory"),
      expect.stringContaining("Courage the Cowardly Dog"),
      expect.stringContaining("✓ 67 / 67"),
    ]);
    expect(rows[0]).toHaveTextContent("2 failed");
    expect(screen.getByRole("heading", { level: 1, name: "Media" })).toHaveFocus();
  });

  it("says when the server has no conversion status yet, or nothing to convert", async () => {
    const user = userEvent.setup();
    const api = fakeApi();
    render(<AdminApp api={api} />);
    await user.click(await screen.findByRole("button", { name: "Media" }));
    expect(await screen.findByText(/isn't available on this server yet/)).toBeInTheDocument();
    vi.mocked(api.getMediaProgress).mockResolvedValue(progress({ done: 0, total: 0, shows: [] }));
    await user.click(screen.getByRole("button", { name: "Channels" }));
    await user.click(await screen.findByRole("button", { name: "Media" }));
    expect(await screen.findByText("Nothing to convert.")).toBeInTheDocument();
  });

  it("lists the station manager's runs and undoes the latest", async () => {
    const user = userEvent.setup();
    const api = fakeApi();
    const signals = {
      total_seconds: 16080, sessions: 24, year: 1995, shows: [], channels: [],
      heatmap: [], busiest_hours: [20],
    };
    const report = {
      weeks: 4, since: "2026-10-11T07:00:00Z", until: "2026-11-08T07:00:00Z", signals,
      power_window: { start: "20:00", hours: 1 },
      changes: [{ knob: "pool:ktvu-base:20:00:the-simpsons", channel: "ktvu", show: "the-simpsons",
                  title: "The Simpsons", template: "ktvu-base", start: "20:00", before: 1, after: 1.62,
                  why: "finished every time" }],
      power_hours: [{ channel: "ktvu", start: "20:00", hours: 1, shows: ["the-simpsons"],
                      titles: ["The Simpsons"], weights: [2] }],
      dropped: [], model_summary: "More Simpsons", notes: [],
      before: { carriage: [], pools: [], power_hours: [] }, after: null,
    };
    vi.mocked(api.getStationRuns).mockResolvedValue({
      auto_apply: true,
      runs: [
        { id: 2, created_at: "2026-11-08T07:04:00Z", week_of: "2026-11-08", status: "applied",
          model: "chat", revision_id: 9, summary: "Applied 1 weight on 1 channel.", report,
          can_undo: true },
        { id: 1, created_at: "2026-11-01T07:00:00Z", week_of: "2026-11-01", status: "skipped",
          model: "chat", revision_id: null, summary: "Not enough viewing yet.",
          report: { ...report, changes: [], power_hours: [] }, can_undo: false },
      ],
    });
    render(<AdminApp api={api} />);
    await user.click(await screen.findByRole("button", { name: "Station" }));
    const runs = await screen.findByTestId("station-runs");
    const [latest, older] = Array.from(runs.querySelectorAll(":scope > li"));
    expect(latest).toHaveTextContent("Week of Nov 8, 2026");
    expect(latest).toHaveTextContent("Applied");
    expect(latest).toHaveTextContent("4 h 28 min watched in 4 weeks");
    expect(latest).toHaveTextContent("Power hour 8 PM to 9 PM: The Simpsons");
    expect(latest).toHaveTextContent("The Simpsons, 8 PM part: ×1 → ×1.62");
    expect(older).toHaveTextContent("No change");
    expect(screen.getByRole("heading", { level: 1, name: "Station manager" })).toHaveFocus();
    await user.click(screen.getByRole("button", { name: "Undo this week's changes" }));
    expect(api.undo).toHaveBeenCalledOnce();
    expect(await screen.findByText("Restored")).toBeInTheDocument();
  });

  it("says when the station manager hasn't run yet", async () => {
    const user = userEvent.setup();
    render(<AdminApp api={fakeApi()} />);
    await user.click(await screen.findByRole("button", { name: "Station" }));
    expect(await screen.findByText(/hasn't run yet/)).toBeInTheDocument();
    expect(screen.getByTestId("station-mode")).toHaveTextContent("Changes apply on their own");
  });
  it("shows the latest Sunday report and the headlines of older ones", async () => {
    const user = userEvent.setup();
    const api = fakeApi();
    const report = {
      week_of: "2026-11-08", since: "2026-11-08T13:00:00Z", until: "2026-11-15T13:00:00Z",
      viewing: { seconds: 3240, sessions: 3, last_week_seconds: 900, busiest_hour: "20:00",
                 top_shows: [{ show: "the-simpsons", title: "The Simpsons", channel: "KTVU (Fox)",
                               seconds: 2640, completion: 1 }],
                 top_channels: [{ id: "ktvu", name: "KTVU (Fox)", seconds: 2640 }] },
      station: { id: 3, status: "proposed", summary: "3 changes proposed", week_of: "2026-11-15" },
      pending: ["The station manager's proposal waits for you in /admin, Station."],
      library: { episodes: 6301, shows: 140, movies: 79, episodes_added: 412, empty_count: 2,
                 empty: ["Doug", "Rugrats"] },
      conversion: { done: 5746, total: 8774, left: 3028, failed: 3, hours_left: 50.5 },
      backups: { ok: true, last_backup_at: "2026-11-15T06:00:00Z", last_backup_bytes: 1438872,
                 restore_ok: true, restore_at: "2026-11-15T06:00:10Z", restore_problems: [],
                 schedule_warnings: ["kqed: Sat Nov 11 2000: Some Movie would air before its first air date."] },
      headline: ["Watched 54 min (last week 15 min), most The Simpsons on KTVU (Fox).",
                 "Backups: last 7 h ago, restore test passed."],
    };
    vi.mocked(api.getReports).mockResolvedValue({
      schedule: "Sundays at 8 AM",
      reports: [
        { id: 2, created_at: "2026-11-15T13:00:00Z", week_of: "2026-11-08", report },
        { id: 1, created_at: "2026-11-08T13:00:00Z", week_of: "2026-11-01",
          report: { ...report, week_of: "2026-11-01", headline: ["Nothing watched this week."] } },
      ],
    });
    render(<AdminApp api={api} />);
    await user.click(await screen.findByRole("button", { name: "Report" }));
    expect(screen.getByRole("heading", { level: 1, name: "Sunday report" })).toHaveFocus();
    const latest = await screen.findByTestId("report-latest");
    expect(latest).toHaveTextContent("Week of Nov 8, 2026");
    expect(latest).toHaveTextContent("54 min watched (last week 15 min)");
    expect(latest).toHaveTextContent("The Simpsons on KTVU (Fox)");
    expect(latest).toHaveTextContent("Waits for you: The station manager's proposal");
    expect(latest).toHaveTextContent("6,301 episodes (+412) of 140 shows, 79 movies");
    expect(latest).toHaveTextContent("Nothing yet for 2 carried titles: Doug, Rugrats");
    expect(latest).toHaveTextContent("3,028 files left, about 51 h; 3 failed");
    expect(latest).toHaveTextContent("Restore test passed");
    expect(latest).toHaveTextContent("Some Movie would air before its first air date");
    expect(screen.getByTestId("report-older")).toHaveTextContent("Week of Nov 1, 2026: Nothing watched this week.");
  });

  it("says when the first Sunday report is due", async () => {
    const user = userEvent.setup();
    render(<AdminApp api={fakeApi()} />);
    await user.click(await screen.findByRole("button", { name: "Report" }));
    expect(await screen.findByText("The first report comes Sundays at 8 AM.")).toBeInTheDocument();
  });
});
