import "@fontsource/arimo/400.css";
import "@fontsource/arimo/700.css";
import "@fontsource/archivo-narrow/400.css";
import "@fontsource/archivo-narrow/700.css";
import "./styles.css";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import tokens from "../../design/tokens.json";
import { App } from "./App";
import { applyTokens } from "./lib/tokens";

applyTokens(tokens);

const root = document.getElementById("root");
if (!root) throw new Error("missing #root");
createRoot(root).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
