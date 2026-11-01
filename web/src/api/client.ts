import createClient from "openapi-fetch";
import type { components, paths } from "./generated/schema";

export type ServerVersion = components["schemas"]["Version"];

// Caddy (and the Vite dev proxy) route /api/* to the API with the prefix stripped.
export const api = createClient<paths>({ baseUrl: `${window.location.origin}/api` });

export async function getServerVersion(): Promise<ServerVersion> {
  const { data, response } = await api.GET("/version");
  if (!response.ok || !data) throw new Error(`version request failed: ${response.status}`);
  return data;
}
