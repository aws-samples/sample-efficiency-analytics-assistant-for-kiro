import React from "react";
import ReactDOM from "react-dom/client";
import { ConfigProvider, App as AntdApp, Spin } from "antd";
import enUS from "antd/locale/en_US";
import { BrowserRouter } from "react-router-dom";

import { darkTheme } from "./theme";
import { loadConfig } from "./config";
import { AppRoutes } from "./App";
import { LanguageProvider } from "./i18n";
import "./index.css";

/** English-only build: AntD components use the en_US locale. */
function LocalizedApp() {
  return (
    <ConfigProvider theme={darkTheme} locale={enUS}>
      <AntdApp>
        <BrowserRouter>
          <AppRoutes />
        </BrowserRouter>
      </AntdApp>
    </ConfigProvider>
  );
}

function Root() {
  const [ready, setReady] = React.useState(false);
  const [err, setErr] = React.useState<string | null>(null);

  React.useEffect(() => {
    loadConfig()
      .then(() => setReady(true))
      .catch((e) => setErr(String(e)));
  }, []);

  if (err) return <div style={{ padding: 40, color: "#f87171" }}>Config load failed: {err}</div>;
  if (!ready)
    return (
      <div style={{ display: "grid", placeItems: "center", height: "100vh" }}>
        <Spin size="large" tip="Loading…" />
      </div>
    );

  return (
    <LanguageProvider>
      <LocalizedApp />
    </LanguageProvider>
  );
}

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <Root />
  </React.StrictMode>
);
