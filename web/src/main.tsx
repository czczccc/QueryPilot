import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { TooltipProvider } from "@cz/design-system";
import "./styles.css";
import { App } from "./App";
import { AppStateProvider } from "./lib/app-state";
import { MeProvider } from "./lib/me";
import { ToastProvider } from "./lib/toast";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <TooltipProvider>
      <ToastProvider>
        <MeProvider>
          <AppStateProvider>
            <App />
          </AppStateProvider>
        </MeProvider>
      </ToastProvider>
    </TooltipProvider>
  </StrictMode>,
);
