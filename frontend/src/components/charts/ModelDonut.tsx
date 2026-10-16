import type { EChartsOption } from "echarts";
import { Card } from "antd";
import { Chart } from "./Chart";
import { chartPalette } from "../../theme";
import type { ModelSlice } from "../../api/types";
import { useI18n } from "../../i18n";

/** Model usage distribution donut chart. */
export function ModelDonut({ models }: { models: ModelSlice[] }) {
  const { t } = useI18n();
  const option: EChartsOption = {
    tooltip: { trigger: "item", formatter: "{b}: {c} ({d}%)" },
    legend: { type: "scroll", bottom: 0, textStyle: { color: "#94a3b8" } },
    color: chartPalette,
    series: [
      {
        type: "pie",
        radius: ["45%", "70%"],
        center: ["50%", "45%"],
        avoidLabelOverlap: true,
        itemStyle: { borderColor: "#111a2e", borderWidth: 2 },
        label: { color: "#cbd5e1" },
        data: models.map((m) => ({ name: m.model, value: m.messages })),
      },
    ],
  };
  return (
    <Card size="small" title={t("chart.models.title")}>
      <Chart option={option} height={340} />
    </Card>
  );
}
