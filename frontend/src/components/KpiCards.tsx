import { Card, Col, Progress, Row, Statistic, Tooltip } from "antd";
import type { SummaryResponse } from "../api/types";
import { useI18n, tierLabel } from "../i18n";

interface Props {
  summary: SummaryResponse;
}

export function KpiCards({ summary }: Props) {
  const { t, lang } = useI18n();
  const fmt = (n: number) =>
    n.toLocaleString(lang === "en" ? "en-US" : "zh-CN", { maximumFractionDigits: 1 });
  const k = summary.kpis;
  const cards: { title: string; value: string; suffix?: string; tip?: string }[] = [
    { title: t("kpi.registeredActive"), value: `${k.registered_users} / ${k.active_users}`, tip: t("kpi.registeredActive.tip") },
    { title: t("kpi.activationRate"), value: `${(k.activation_rate * 100).toFixed(1)}`, suffix: "%", tip: t("kpi.activationRate.tip") },
    { title: t("kpi.dailyActive"), value: `${fmt(k.daily_active_avg)} / ${fmt(k.daily_active_peak)}`, suffix: t("kpi.unit.person"), tip: t("kpi.dailyActive.tip") },
    { title: t("kpi.totalCredits"), value: fmt(k.total_credits) },
    { title: t("kpi.overageUsers"), value: fmt(k.overage_users), tip: t("kpi.overageUsers.tip") },
    { title: t("kpi.estCost"), value: `$${fmt(k.est_cost)}`, tip: summary.est_cost_note },
    { title: t("kpi.avgCredits"), value: fmt(k.avg_credits_per_active) },
  ];

  return (
    <Row gutter={[12, 12]} align="stretch">
      {cards.map((c) => (
        <Col key={c.title} xs={12} sm={8} md={6} xl={3} style={{ display: "flex" }}>
          <Card size="small" style={{ width: "100%" }} styles={{ body: { padding: 14 } }}>
            <Tooltip title={c.tip}>
              <Statistic
                title={c.title + (c.tip ? " *" : "")}
                value={c.value}
                suffix={c.suffix ? <span style={{ fontSize: 13, fontWeight: 400 }}>{c.suffix}</span> : undefined}
                valueStyle={{ fontSize: 20, fontWeight: 600, whiteSpace: "nowrap" }}
              />
            </Tooltip>
          </Card>
        </Col>
      ))}
    </Row>
  );
}

const TIER_COLORS: Record<string, string> = {
  Pro: "#64748b",
  ProPlus: "#3b82f6",
  ProMax: "#a855f7",
  Power: "#f59e0b",
};

export function TierDistribution({ tiers }: { tiers: SummaryResponse["tiers"] }) {
  const { t } = useI18n();
  const unit = t("tier.unit");
  const entries = Object.entries(tiers) as [keyof SummaryResponse["tiers"], number][];
  const total = entries.reduce((s, [, v]) => s + v, 0) || 1;
  return (
    <Card size="small" title={t("tier.title")}>
      <Progress
        percent={100}
        showInfo={false}
        strokeColor={{ from: "#3b82f6", to: "#3b82f6" }}
        style={{ display: "none" }}
      />
      <div style={{ display: "flex", height: 18, borderRadius: 6, overflow: "hidden" }}>
        {entries.map(([tier, count]) => (
          <Tooltip key={tier} title={`${tierLabel(tier, true)}: ${count}${unit ? " " + unit : ""}`}>
            <div
              style={{
                width: `${(count / total) * 100}%`,
                background: TIER_COLORS[tier],
              }}
            />
          </Tooltip>
        ))}
      </div>
      <div style={{ display: "flex", flexWrap: "wrap", gap: 14, marginTop: 12 }}>
        {entries.map(([tier, count]) => (
          <span key={tier} style={{ fontSize: 12 }}>
            <span
              style={{
                display: "inline-block",
                width: 10,
                height: 10,
                borderRadius: 2,
                background: TIER_COLORS[tier],
                marginRight: 6,
              }}
            />
            {tierLabel(tier, true)} <b>{count}</b>
          </span>
        ))}
      </div>
    </Card>
  );
}
