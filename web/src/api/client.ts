import createClient from "openapi-fetch";
import { postWatch, type SendWatch } from "../lib/watch";
import type { components, paths } from "./generated/schema";

export type ServerVersion = components["schemas"]["Version"];
export type Lineup = components["schemas"]["Lineup"];
export type ChannelInfo = components["schemas"]["ChannelInfo"];
export type Guide = components["schemas"]["Guide"];
export type GuideSlot = components["schemas"]["GuideSlot"];
export type Tune = components["schemas"]["Tune"];
export type TunedSlot = components["schemas"]["TunedSlot"];

// Caddy (and the Vite dev proxy) route /api/* to the API with the prefix stripped.
export const api = createClient<paths>({ baseUrl: `${window.location.origin}/api` });

export async function getServerVersion(): Promise<ServerVersion> {
  const { data, response } = await api.GET("/version");
  if (!response.ok || !data) throw new Error(`version request failed: ${response.status}`);
  return data;
}

export async function getLineup(year: number): Promise<Lineup> {
  const { data, response } = await api.GET("/channels", { params: { query: { year } } });
  if (!response.ok || !data) throw new Error(`channels request failed: ${response.status}`);
  return data;
}

export async function getGuide(year: number, date: string, start: string, hours: number): Promise<Guide> {
  const { data, response } = await api.GET("/guide", {
    params: { query: { year, date, start, hours } },
  });
  if (!response.ok || !data) throw new Error(`guide request failed: ${response.status}`);
  return data;
}

export async function tune(channelId: string, year: number): Promise<Tune> {
  const { data, response } = await api.GET("/tune/{channel_id}", {
    params: { path: { channel_id: channelId }, query: { year } },
  });
  if (!response.ok || !data) throw new Error(`tune request failed: ${response.status}`);
  return data;
}

/** Everything the TV screens need from the server, injectable for tests. */
export interface TvApi {
  getServerVersion: typeof getServerVersion;
  getLineup: typeof getLineup;
  getGuide: typeof getGuide;
  tune: typeof tune;
  /** Viewing reports for the station manager (M5); fire and forget. Absent: none sent. */
  reportWatch?: SendWatch;
}

export const liveApi: TvApi = { getServerVersion, getLineup, getGuide, tune, reportWatch: postWatch };
