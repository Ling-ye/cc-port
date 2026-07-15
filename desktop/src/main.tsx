import React from "react";
import ReactDOM from "react-dom/client";
import App from "@/app/App";
import { TaskCenterProvider } from "@/app/TaskCenterContext";
import "@/styles/global.css";

ReactDOM.createRoot(document.getElementById("root") as HTMLElement).render(
  <React.StrictMode>
    <TaskCenterProvider>
      <App />
    </TaskCenterProvider>
  </React.StrictMode>,
);
