// First of all: style elements made by the page carry the one-time number
// the page's security policy asks for (see styleNonce.ts).
import "./styleNonce";
// Fonts live with the page, not at Google: otherwise every visit would tell
// Google the visitor's address.
import "@fontsource-variable/manrope";
import "@fontsource-variable/jetbrains-mono";
import "./styles/tokens.css";
import "./styles/app.css";

import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";

const root = document.getElementById("root");
if (root) {
  createRoot(root).render(
    <StrictMode>
      <App />
    </StrictMode>,
  );
}
