import ReactECharts from "echarts-for-react";
import type { EChartsOption } from "echarts";

/** Generic dark-canvas ECharts wrapper. */
export function Chart({
  option,
  height = 300,
  onEvents,
  onChartReady,
}: {
  option: EChartsOption;
  height?: number;
  onEvents?: Record<string, (params: any) => void>;
  /** Callback to get the ECharts instance (optional, backward-compatible). Used by charts that need zrender-level events / coordinate conversion. */
  onChartReady?: (chart: any) => void;
}) {
  return (
    <ReactECharts
      option={{ backgroundColor: "transparent", textStyle: { color: "#cbd5e1" }, ...option }}
      style={{ height }}
      opts={{ renderer: "canvas" }}
      notMerge
      onEvents={onEvents}
      onChartReady={onChartReady}
    />
  );
}

/**
 * HTML escaping: the ECharts callback-style tooltip formatter returns an HTML string,
 * so values must be escaped before interpolation (user/department names come from Identity Center and are untrusted).
 */
export const esc = (s: unknown) => String(s ?? "").replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]!));
export const axisStyle = {
  axisLine: { lineStyle: { color: "#334155" } },
  splitLine: { lineStyle: { color: "#1e2a44" } },
  axisLabel: { color: "#94a3b8" },
};
