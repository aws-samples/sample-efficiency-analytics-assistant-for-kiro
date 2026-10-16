import type { EChartsOption } from "echarts";
import { Card } from "antd";
import { Chart, axisStyle, esc } from "./Chart";
import type { TopUser } from "../../api/types";
import { useI18n, tierLabel } from "../../i18n";

/** Top N users horizontal bars. */
export function TopUsersChart({ users }: { users: TopUser[] }) {
  const { t } = useI18n();
  const rows = [...users].sort((a, b) => a.credits_used - b.credits_used);
  const labels = rows.map((u) => u.display_name || u.email.split("@")[0]);
  const option: EChartsOption = {
    tooltip: {
      trigger: "axis",
      axisPointer: { type: "shadow" },
      formatter: (p: any) => {
        const i = p[0].dataIndex;
        const u = rows[i];
        return `${esc(u.display_name || u.email)}<br/>${esc(u.department)} · ${esc(tierLabel(u.subscription_tier))}<br/>Credits: <b>${esc(u.credits_used)}</b>`;
      },
    },
    grid: { left: 90, right: 40, top: 12, bottom: 24 },
    xAxis: { type: "value", ...axisStyle },
    yAxis: { type: "category", data: labels, ...axisStyle },
    series: [
      {
        type: "bar",
        data: rows.map((u) => u.credits_used),
        itemStyle: { color: "#a855f7", borderRadius: [0, 4, 4, 0] },
        barMaxWidth: 16,
        label: { show: true, position: "right", color: "#cbd5e1" },
      },
    ],
  };
  return (
    <Card size="small" title={t("chart.top.title", { n: users.length })}>
      <Chart option={option} height={360} />
    </Card>
  );
}
