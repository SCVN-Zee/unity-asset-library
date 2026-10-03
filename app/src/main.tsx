import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { OverlayProvider } from "./components/ui";
import App from "./App";
import { FocusVisibility } from "./components/focus-visibility";
import "./styles.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <OverlayProvider>
      <FocusVisibility />
      <App />
    </OverlayProvider>
  </StrictMode>,
);
