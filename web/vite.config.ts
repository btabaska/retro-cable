/// <reference types="vitest/config" />
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

const apiTarget = process.env.RC_API_URL ?? "http://127.0.0.1:8000";
const proxy = {
  "/api": { target: apiTarget, rewrite: (path: string) => path.replace(/^\/api/, "") },
};

export default defineConfig({
  plugins: [react()],
  // design/tokens.json lives outside web/.
  server: { fs: { allow: [".."] }, proxy },
  preview: { proxy },
  test: {
    environment: "jsdom",
    include: ["src/**/*.test.{ts,tsx}"],
    setupFiles: ["./src/test-setup.ts"],
  },
});
