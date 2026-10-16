import type { EChartsOption } from "echarts";
import { Card } from "antd";
import { Chart, axisStyle } from "./Chart";
import type { DepartmentRow } from "../../api/types";
import { useI18n } from "../../i18n";

/** Thousands-separator formatting (used for bar-end labels) */
const fmt = (n: number) => Math.round(n).toLocaleString("en-US");

/**
 * Credits by department + per capita (horizontal bars).
 * Department names are resolved in the data layer (identity-sync extracts them from the Identity Center group Description);
 * the frontend displays them directly, with no hardcoded mapping.
 */
export function DepartmentChart({ departments }: { departments: DepartmentRow[] }) {
  const { t } = useI18n();
  const totalLabel = t("chart.dept.total");
  const avgLabel = t("chart.dept.avg");
  // Sort ascending by total credits; horizontal bars read more naturally with the smallest at the bottom
  const rows = [...departments].sort((a, b) => a.total_credits - b.total_credits);
  const names = rows.map((r) => r.department);
  const option: EChartsOption = {
    tooltip: { trigger: "axis", axisPointer: { type: "shadow" } },
    legend: { data: [totalLabel, avgLabel], textStyle: { color: "#94a3b8" }, top: 0 },
    // Leave more room on the right so the bar-end numbers are not clipped
    grid: { left: 150, right: 90, top: 36, bottom: 30 },
    xAxis: { type: "value", ...axisStyle },
    yAxis: { type: "category", data: names, ...axisStyle },
    series: [
      {
        name: totalLabel,
        type: "bar",
        data: rows.map((r) => r.total_credits),
        itemStyle: { color: "#3b82f6", borderRadius: [0, 4, 4, 0] },
        barMaxWidth: 16,
        label: {
          show: true,
          position: "right",
          color: "#e2e8f0",
          fontSize: 11,
          formatter: (p: any) => fmt(p.value as number),
        },
      },
      {
        name: avgLabel,
        type: "bar",
        data: rows.map((r) => r.avg_credits),
        itemStyle: { color: "#22c55e", borderRadius: [0, 4, 4, 0] },
        barMaxWidth: 16,
        label: {
          show: true,
          position: "right",
          color: "#e2e8f0",
          fontSize: 11,
          formatter: (p: any) => fmt(p.value as number),
        },
      },
    ],
  };
  return (
    <Card size="small" title={t("chart.dept.title")}>
      <Chart option={option} height={340} />
    </Card>
  );
}
