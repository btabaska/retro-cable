import { api } from "../api/client";
import type { components } from "../api/generated/schema";

type S = components["schemas"];
export type AdminState = S["AdminState"];
export type AdminChannelRow = S["AdminChannelRow"];
export type AdminChannelList = S["AdminChannelList"];
export type AdminChannel = S["AdminChannel"];
export type ChannelDraft = S["ChannelDraft"];
export type DraftShow = S["DraftShow"];
export type CheckIssue = S["CheckIssue"];
export type CheckResult = S["CheckResult"];
export type Preview = S["Preview"];
export type LibraryEntry = S["LibraryEntry"];
export type SaveResult = S["SaveResult"];
export type ChangeResult = S["ChangeResult"];

export type DraftRequest = S["DraftRequest"];

/** A save either lands or comes back with the checks that failed. */
export type SaveOutcome = { saved: true; result: SaveResult } | { saved: false; check: CheckResult };

const fail = (what: string, status: number) => new Error(`${what} failed: ${status}`);

export async function listChannels(): Promise<AdminChannelList> {
  const { data, response } = await api.GET("/admin/channels");
  if (!data) throw fail("channels", response.status);
  return data;
}

export async function getChannel(id: string): Promise<AdminChannel> {
  const { data, response } = await api.GET("/admin/channels/{channel_id}", {
    params: { path: { channel_id: id } },
  });
  if (!data) throw fail("channel", response.status);
  return data;
}

export async function searchLibrary(q: string): Promise<LibraryEntry[]> {
  const { data, response } = await api.GET("/admin/library", { params: { query: { q } } });
  if (!data) throw fail("library", response.status);
  return data.items;
}

export async function check(req: DraftRequest): Promise<CheckResult> {
  const { data, response } = await api.POST("/admin/check", { body: req });
  if (!data) throw fail("check", response.status);
  return data;
}

export async function preview(req: DraftRequest): Promise<Preview> {
  const { data, response } = await api.POST("/admin/preview", { body: req });
  if (!data) throw fail("preview", response.status);
  return data;
}

export async function saveChannel(req: DraftRequest, id: string | null): Promise<SaveOutcome> {
  const { data, error, response } = id
    ? await api.PUT("/admin/channels/{channel_id}", { params: { path: { channel_id: id } }, body: req })
    : await api.POST("/admin/channels", { body: req });
  if (data) return { saved: true, result: data };
  if (response.status === 409 && error && "ok" in error) return { saved: false, check: error };
  throw fail("save", response.status);
}

export async function deleteChannel(id: string): Promise<ChangeResult> {
  const { data, response } = await api.DELETE("/admin/channels/{channel_id}", {
    params: { path: { channel_id: id } },
  });
  if (!data) throw fail("delete", response.status);
  return data;
}

export async function undo(): Promise<ChangeResult> {
  const { data, response } = await api.POST("/admin/undo");
  if (!data) throw fail("undo", response.status);
  return data;
}

/** Everything the admin screens need from the server, injectable for tests. */
export interface AdminApi {
  listChannels: typeof listChannels;
  getChannel: typeof getChannel;
  searchLibrary: typeof searchLibrary;
  check: typeof check;
  preview: typeof preview;
  saveChannel: typeof saveChannel;
  deleteChannel: typeof deleteChannel;
  undo: typeof undo;
}

export const liveAdminApi: AdminApi = {
  listChannels,
  getChannel,
  searchLibrary,
  check,
  preview,
  saveChannel,
  deleteChannel,
  undo,
};
