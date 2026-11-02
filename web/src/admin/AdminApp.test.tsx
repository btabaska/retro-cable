import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { AdminApp } from "./AdminApp";
import type { AdminApi, AdminChannelList, AdminChannelRow, CheckResult, DraftRequest, LibraryEntry, Preview } from "./api";

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
  { id: "courage", title: "Courage the Cowardly Dog", kind: "show", first_year: 1999, last_year: 2002, episodes: 52, channels: [] },
  { id: "johnny-bravo", title: "Johnny Bravo", kind: "show", first_year: 1997, last_year: 2004, episodes: 67, channels: [] },
  {
    id: "the-simpsons",
    title: "The Simpsons",
    kind: "show",
    first_year: 1989,
    last_year: 2010,
    episodes: 400,
    channels: [{ id: "ktvu", name: "KTVU (Fox)", number: 2 }],
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
