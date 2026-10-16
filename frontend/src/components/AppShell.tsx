import type { ReactNode } from "react";
import { Button, Dropdown, Layout, Space } from "antd";
import { LogoutOutlined, UserOutlined } from "@ant-design/icons";
import { useNavigate } from "react-router-dom";
import { getUserEmail, logout } from "../auth/auth";
import { LanguageSwitch } from "./LanguageSwitch";
import { useI18n } from "../i18n";

const { Header, Content } = Layout;

type NavKey = "dashboard" | "modelConcentration" | "report" | "admin";

/** Shared shell for all pages: Header (title + nav + language + sign out) + Content. */
export function AppShell({ active, children }: { active: NavKey; children: ReactNode }) {
  const { t } = useI18n();
  const navigate = useNavigate();

  const navItems: { key: NavKey; label: string; path: string }[] = [
    { key: "dashboard", label: t("nav.dashboard"), path: "/" },
    { key: "modelConcentration", label: t("nav.modelConcentration"), path: "/model-concentration" },
    { key: "report", label: t("nav.report"), path: "/report" },
    { key: "admin", label: t("nav.adminCenter"), path: "/admin/multipliers" },
  ];

  return (
    <Layout style={{ minHeight: "100vh" }}>
      <Header
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          background: "#0f1830",
          borderBottom: "1px solid #1e2a44",
          paddingInline: 20,
          gap: 16,
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 24, minWidth: 0 }}>
          <span style={{ color: "#e2e8f0", fontSize: 18, fontWeight: 600, whiteSpace: "nowrap" }}>
            {t("app.title")}
          </span>
          <Space size={4}>
            {navItems.map((n) => (
              <Button
                key={n.key}
                type="text"
                onClick={() => navigate(n.path)}
                style={{
                  color: active === n.key ? "#60a5fa" : "#cbd5e1",
                  fontWeight: active === n.key ? 600 : 400,
                }}
              >
                {n.label}
              </Button>
            ))}
          </Space>
        </div>
        <Space size={8} align="center">
          <LanguageSwitch />
          <Dropdown
            menu={{
              items: [
                { key: "logout", icon: <LogoutOutlined />, label: t("app.logout"), onClick: () => logout() },
              ],
            }}
          >
            <Button type="text" style={{ color: "#cbd5e1" }} icon={<UserOutlined />}>
              {getUserEmail() ?? t("app.admin")}
            </Button>
          </Dropdown>
        </Space>
      </Header>
      <Content style={{ padding: 20 }}>{children}</Content>
    </Layout>
  );
}
