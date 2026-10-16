import { theme, type ThemeConfig } from "antd";

/** Dark theme (close to the reference UI). Primary is teal-blue; accent is used for charts and progress bars. */
export const darkTheme: ThemeConfig = {
  algorithm: theme.darkAlgorithm,
  token: {
    colorPrimary: "#3b82f6",
    colorBgLayout: "#0b1220",
    colorBgContainer: "#111a2e",
    colorBorderSecondary: "#1e2a44",
    borderRadius: 8,
    fontSize: 13,
  },
  components: {
    Card: { colorBgContainer: "#111a2e" },
    Table: { colorBgContainer: "#111a2e", headerBg: "#16223c" },
    Tabs: { cardBg: "#16223c" },
  },
};

/** Chart palette (reused by the model donut, department bars, etc.). */
export const chartPalette = [
  "#3b82f6", "#22c55e", "#f59e0b", "#a855f7",
  "#06b6d4", "#ef4444", "#eab308", "#ec4899",
];
