import type { EChartsOption } from "echarts";
import { Card } from "antd";
import { Chart, axisStyle } from "./Chart";
import type { TrendPoint } from "../../api/types";
import { useI18n } from "../../i18n";

/** Daily credits line + active-users line (dual Y axis). */
export function TrendChart({ series }: { series: TrendPoint[] }) {
  const { t } = useI18n();
  const credits = t("chart.trend.credits");
  const activeUsers = t("chart.trend.activeUsers");
  const dates = series.map((s) => s.date.slice(5));
  const option: EChartsOption = {
    tooltip: { trigger: "axis", axisPointer: { type: "cross" } },
    legend: { data: [credits, activeUsers], textStyle: { color: "#94a3b8" }, top: 0 },
    grid: { left: 50, right: 50, top: 36, bottom: 30 },
    xAxis: { type: "category", data: dates, ...axisStyle },
    yAxis: [
      { type: "value", ...axisStyle },
      { type: "value", ...axisStyle, splitLine: { show: false } },
    ],
    series: [
      {
        name: credits,
        type: "line",
        smooth: true,
        data: series.map((s) => s.credits),
        itemStyle: { color: "#3b82f6" },
        lineStyle: { width: 2, color: "#3b82f6" },
        areaStyle: { color: "rgba(59,130,246,0.12)" },
      },
      {
        name: activeUsers,
        type: "line",
        yAxisIndex: 1,
        smooth: true,
        data: series.map((s) => s.active_users),
        itemStyle: { color: "#f59e0b" },
        lineStyle: { width: 2, color: "#f59e0b" },
      },
    ],
  };
  return (
    <Card size="small" title={t("chart.trend.title")}>
      <Chart option={option} />
    </Card>
  );
}
