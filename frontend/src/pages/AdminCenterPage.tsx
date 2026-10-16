import type { ReactNode } from "react";
import { Layout, Menu } from "antd";
import { PercentageOutlined } from "@ant-design/icons";
import { Navigate, useLocation, useNavigate } from "react-router-dom";
import { AppShell } from "../components/AppShell";
import { MultiplierMaintenancePage } from "./MultiplierMaintenancePage";
import { useI18n } from "../i18n";

const { Sider, Content } = Layout;

/**
 * Admin-center menu configuration (array-driven).
 * To extend: adding a management feature = add one config entry + one sub-panel component.
 */
interface AdminMenuItem {
  key: string;
  labelKey: string;
  path: string;
  icon: ReactNode;
  element: ReactNode;
}

const MENU_ITEMS: AdminMenuItem[] = [
  {
    key: "multipliers",
    labelKey: "adminCenter.multipliers",
    path: "/admin/multipliers",
    icon: <PercentageOutlined />,
    element: <MultiplierMaintenancePage />,
  },
];

/**
 * Admin center: AppShell (active='admin') wraps an Ant Layout.Sider + Menu.
 * Renders the matching panel for the current sub-route; clicking the menu navigates to the sub-route.
 */
export function AdminCenterPage() {
  const { t } = useI18n();
  const navigate = useNavigate();
  const location = useLocation();

  const current = MENU_ITEMS.find((m) => location.pathname === m.path);

  // /admin or an unknown sub-path → default to the first item (multipliers).
  if (!current) {
    return <Navigate to={MENU_ITEMS[0].path} replace />;
  }

  return (
    <AppShell active="admin">
      <Layout style={{ background: "transparent" }}>
        <Sider width={200} theme="light" style={{ background: "transparent" }}>
          <Menu
            mode="inline"
            selectedKeys={[current.key]}
            onClick={({ key }) => {
              const item = MENU_ITEMS.find((m) => m.key === key);
              if (item) navigate(item.path);
            }}
            items={MENU_ITEMS.map((m) => ({ key: m.key, icon: m.icon, label: t(m.labelKey) }))}
            style={{ borderInlineEnd: "none" }}
          />
        </Sider>
        <Content style={{ paddingInlineStart: 20, minWidth: 0 }}>{current.element}</Content>
      </Layout>
    </AppShell>
  );
}
