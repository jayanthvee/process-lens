/**
 * The page's entry point: the self-hosted Public Sans faces, the shared styles, and the app.
 *
 * The four weights are the only ones the design uses, so exactly four faces are loaded. Nothing
 * here reads `prefers-color-scheme`: the app is light only (DESIGN.md §4).
 */

import "@fontsource/public-sans/300.css";
import "@fontsource/public-sans/400.css";
import "@fontsource/public-sans/500.css";
import "@fontsource/public-sans/600.css";
import "./styles/tokens.css";
import "./styles/global.css";
import "./shell/shell.css";

import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";

import { App } from "./App";

const container = document.getElementById("root");
if (container !== null) {
  createRoot(container).render(
    <StrictMode>
      <BrowserRouter>
        <App />
      </BrowserRouter>
    </StrictMode>,
  );
}
