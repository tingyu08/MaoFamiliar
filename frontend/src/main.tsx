import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";
import { readToken } from "./api/token";
import "./styles.css";

const root = createRoot(document.getElementById("root")!);
const token = readToken();

root.render(
  <StrictMode>
    {token ? (
      <App token={token} />
    ) : (
      <main className="no-token">
        <h1>Familiar</h1>
        <p>請用後端啟動時印出的網址開啟（網址裡帶有 <code>?token=</code>）。</p>
        <p className="muted">token 每次啟動都會重新產生，只在這台電腦上有效。</p>
      </main>
    )}
  </StrictMode>,
);
