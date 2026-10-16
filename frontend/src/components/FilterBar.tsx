import { Button, DatePicker, Segmented, Select, Space } from "antd";
import dayjs, { type Dayjs } from "dayjs";
import type { DateRange } from "../api/types";
import { useI18n } from "../i18n";

const { RangePicker } = DatePicker;

export type FilterMode = "period" | "range";

interface Props {
  mode: FilterMode;
  onModeChange: (m: FilterMode) => void;
  periods: string[];
  period?: string;
  onPeriodChange: (p: string) => void;
  range: [Dayjs, Dayjs] | null;
  onRangeChange: (r: [Dayjs, Dayjs] | null) => void;
  onRefresh: () => void;
}

export function FilterBar(p: Props) {
  const { t } = useI18n();
  return (
    <Space wrap size="middle">
      <Segmented
        options={[
          { label: t("filter.byPeriod"), value: "period" },
          { label: t("filter.byRange"), value: "range" },
        ]}
        value={p.mode}
        onChange={(v) => p.onModeChange(v as FilterMode)}
      />
      {p.mode === "period" ? (
        <Select
          style={{ width: 160 }}
          placeholder={t("filter.selectPeriod")}
          value={p.period}
          options={p.periods.map((x) => ({ label: x, value: x }))}
          onChange={p.onPeriodChange}
        />
      ) : (
        <RangePicker
          value={p.range as any}
          onChange={(v) => p.onRangeChange(v as [Dayjs, Dayjs] | null)}
          allowClear={false}
        />
      )}
      <Button onClick={p.onRefresh}>{t("common.refresh")}</Button>
    </Space>
  );
}

/** Convert the filter state into API DateRange parameters. */
export function toDateRange(
  mode: FilterMode,
  period: string | undefined,
  range: [Dayjs, Dayjs] | null
): DateRange {
  if (mode === "period") return { period };
  if (range) return { from: range[0].format("YYYY-MM-DD"), to: range[1].format("YYYY-MM-DD") };
  return {};
}

/** Default date range: the 1st of the current month → today. */
export const defaultRange = (): [Dayjs, Dayjs] => [dayjs().startOf("month"), dayjs()];
