import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { OverlayProvider } from "./components/ui";
import App from "./App";
import "./styles.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <OverlayProvider>
      <App />
    </OverlayProvider>
  </StrictMode>,
);
