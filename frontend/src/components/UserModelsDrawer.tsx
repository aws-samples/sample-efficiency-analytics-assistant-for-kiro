import { useEffect, useState } from "react";
import { Drawer, Empty, Spin, Typography } from "antd";
import type { EChartsOption } from "echarts";
import { api } from "../api/client";
import type { DateRange, ModelSlice, UserRow } from "../api/types";
import { Chart, axisStyle } from "./charts/Chart";
import { useI18n } from "../i18n";

interface Props {
  user: UserRow | null;
  range: DateRange;
  open: boolean;
  onClose: () => void;
}

/** Click a user detail row → drawer shows that user's per-model usage in range (horizontal bars). */
export function UserModelsDrawer({ user, range, open, onClose }: Props) {
  const { t } = useI18n();
  const [models, setModels] = useState<ModelSlice[]>([]);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!open || !user) return;
    let active = true;
    setLoading(true);
    setModels([]);
    api
      .models(range, user.email)
      .then((r) => {
        if (active) setModels(r.models);
      })
      .finally(() => active && setLoading(false));
    return () => {
      active = false;
    };
  }, [open, user, range]);

  const rows = [...models].sort((a, b) => a.messages - b.messages);
  const option: EChartsOption = {
    tooltip: { trigger: "axis", axisPointer: { type: "shadow" } },
    grid: { left: 140, right: 40, top: 12, bottom: 24 },
    xAxis: { type: "value", ...axisStyle },
    yAxis: { type: "category", data: rows.map((m) => m.model), ...axisStyle },
    series: [
      {
        name: t("userModels.messages"),
        type: "bar",
        data: rows.map((m) => m.messages),
        itemStyle: { color: "#3b82f6", borderRadius: [0, 4, 4, 0] },
        barMaxWidth: 16,
        label: { show: true, position: "right", color: "#cbd5e1", fontSize: 11 },
      },
    ],
  };

  return (
    <Drawer
      title={t("userModels.title")}
      placement="right"
      width={560}
      open={open}
      onClose={onClose}
    >
      {user && (
        <>
          <Typography.Title level={5} style={{ marginTop: 0 }}>
            {user.display_name || user.email.split("@")[0]}
          </Typography.Title>
          <Typography.Paragraph type="secondary" style={{ marginBottom: 4 }}>
            {user.email}
          </Typography.Paragraph>
          <Typography.Paragraph type="secondary" style={{ marginBottom: 16 }}>
            {t("userModels.subtitle")}
            {range.from && range.to ? `：${range.from} ~ ${range.to}` : ""}
          </Typography.Paragraph>
        </>
      )}
      <Spin spinning={loading}>
        {!loading && models.length === 0 ? (
          <Empty description={t("userModels.empty")} />
        ) : (
          <Chart option={option} height={Math.max(240, rows.length * 30 + 60)} />
        )}
      </Spin>
    </Drawer>
  );
}
