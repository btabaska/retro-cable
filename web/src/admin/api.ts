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
export type AdminChannelShow = S["AdminChannelShow"];
export type PremiereResult = S["PremiereResult"];
export type StationRuns = S["StationRuns"];
export type StationRun = S["StationRunRow"];
export type Reports = S["Reports"];
export type WeeklyReport = S["WeeklyReport"];

/** A pretend premiere either lands or comes back with the reason it was refused. */
export type PremiereOutcome = { saved: true; result: PremiereResult } | { saved: false; check: CheckResult };

/**
 * Conversion progress on the rig (GET /api/admin/media/progress). eng-media owns the endpoint;
 * until it lands this shape is the proposal in docs/specs/api.md, so it is typed here rather
 * than in the generated client. A 404 means the server doesn't have it yet.
 */
export interface MediaProgress {
  done: number;
  total: number;
  failed: number;
  /** "gpu", "cpu", or "paused"; gpu_busy says CPU was chosen because the GPU was in use. */
  encoder: "gpu" | "cpu" | "paused";
  gpu_busy: boolean;
  /** Wall-clock seconds of the most recent finished files, newest last (up to 20). */
  recent_seconds: number[];
  shows: MediaShowProgress[];
}

export interface MediaShowProgress {
  title: string;
  done: number;
  total: number;
  failed: number;
  state: "converting" | "queued" | "done";
}

export class NotAvailable extends Error {}

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

export async function setPremiere(channelId: string, show: string, premiere: string | null): Promise<PremiereOutcome> {
  const { data, error, response } = await api.PUT("/admin/channels/{channel_id}/premieres/{show}", {
    params: { path: { channel_id: channelId, show } },
    body: { premiere },
  });
  if (data) return { saved: true, result: data };
  if (response.status === 409 && error && "ok" in error) return { saved: false, check: error };
  throw fail("premiere", response.status);
}

export async function getMediaProgress(): Promise<MediaProgress> {
  const response = await fetch(`${window.location.origin}/api/admin/media/progress`);
  if (response.status === 404) throw new NotAvailable("no conversion status");
  if (!response.ok) throw fail("media progress", response.status);
  return (await response.json()) as MediaProgress;
}

export async function getStationRuns(): Promise<StationRuns> {
  const { data, response } = await api.GET("/admin/station/runs", { params: { query: { limit: 12 } } });
  if (!data) throw fail("station runs", response.status);
  return data;
}

export async function getReports(): Promise<Reports> {
  const { data, response } = await api.GET("/admin/reports", { params: { query: { limit: 8 } } });
  if (!data) throw fail("reports", response.status);
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
  setPremiere: typeof setPremiere;
  getMediaProgress: typeof getMediaProgress;
  getStationRuns: typeof getStationRuns;
  getReports: typeof getReports;
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
  setPremiere,
  getMediaProgress,
  getStationRuns,
  getReports,
  undo,
};
