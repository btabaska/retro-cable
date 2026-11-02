import { expect, test } from "@playwright/test";

// Signed media URLs. Against the compose stack (local-test) the web container's Caddy asks
// the API (forward_auth /media-auth) before file_server answers; against `vite preview` the
// API serves /media itself. Both must behave the same.
test("signed media: valid ranges play, tampered or expired URLs are refused", async ({ request }) => {
  const tuned = (await (await request.get("/api/tune/ktvu?year=1998")).json()) as {
    current: { media_url: string | null };
    next: { media_url: string | null }[];
  };
  const url = [tuned.current, ...tuned.next].map((s) => s.media_url).find((u) => u !== null) ?? "";
  expect(url, "a tuned slot with media").not.toBe("");
  const signed = new URL(url, "http://x");
  const exp = signed.searchParams.get("exp") ?? "";
  const sig = signed.searchParams.get("sig") ?? "";
  expect(exp).not.toBe("");
  expect(sig).not.toBe("");
  const range = { headers: { Range: "bytes=0-99" } };

  const ok = await request.get(url, range);
  expect(ok.status()).toBe(206);
  expect((await ok.body()).length).toBe(100);

  const badSig = url.replace(sig, sig.slice(0, -1) + (sig.endsWith("0") ? "1" : "0"));
  expect((await request.get(badSig, range)).status()).toBe(403);

  const other = signed.pathname.endsWith("/movie.mp4") ? "bumper.mp4" : "movie.mp4";
  const otherPath = url.replace(/\/media\/[^?]+/, `/media/_demo/${other}`);
  expect((await request.get(otherPath, range)).status()).toBe(403);

  const expired = url.replace(`exp=${exp}`, "exp=1");
  expect((await request.get(expired, range)).status()).toBe(403);

  expect((await request.get(signed.pathname, range)).status()).not.toBe(206);
});
