import { Button, Card, Typography } from "antd";
import { login } from "../auth/auth";
import { useI18n } from "../i18n";
import { LanguageSwitch } from "../components/LanguageSwitch";

export function LoginPage() {
  const { t } = useI18n();
  return (
    <div style={{ position: "relative", display: "grid", placeItems: "center", height: "100vh" }}>
      <div style={{ position: "absolute", top: 16, right: 16 }}>
        <LanguageSwitch />
      </div>
      <Card style={{ width: 380, textAlign: "center" }}>
        <Typography.Title level={3} style={{ marginBottom: 24 }}>
          {t("login.title")}
        </Typography.Title>
        <Button type="primary" size="large" block onClick={() => login()}>
          {t("login.button")}
        </Button>
      </Card>
    </div>
  );
}
