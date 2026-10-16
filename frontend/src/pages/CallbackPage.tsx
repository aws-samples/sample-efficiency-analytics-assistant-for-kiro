import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Result, Spin, Button } from "antd";
import { handleCallback } from "../auth/auth";
import { useI18n } from "../i18n";

export function CallbackPage() {
  const { t } = useI18n();
  const navigate = useNavigate();
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    handleCallback()
      .then(() => navigate("/", { replace: true }))
      .catch((e) => setError(String(e)));
  }, [navigate]);

  if (error) {
    return (
      <Result
        status="error"
        title={t("callback.failTitle")}
        subTitle={error}
        extra={<Button type="primary" onClick={() => navigate("/login", { replace: true })}>{t("callback.back")}</Button>}
      />
    );
  }
  return (
    <div style={{ display: "grid", placeItems: "center", height: "100vh" }}>
      <Spin size="large" tip={t("callback.finishing")} />
    </div>
  );
}
