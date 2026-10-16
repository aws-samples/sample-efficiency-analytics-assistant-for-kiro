import { useEffect, useMemo, useState } from "react";
import { Space } from "antd";
import type { Dayjs } from "dayjs";

import { api } from "../api/client";
import type { FiltersResponse, ModelConcentrationResponse } from "../api/types";
import { FilterBar, defaultRange, toDateRange, type FilterMode } from "../components/FilterBar";
import { AppShell } from "../components/AppShell";
import { ModelConcentrationCard } from "../components/ModelConcentrationCard";
import { ConcentrationMembersTable } from "../components/ConcentrationMembersTable";

/**
 * Standalone model-concentration page.
 * - Has its own FilterBar (period / range modes) to get a page-level dateRange.
 * - The card manages its own request and reports the latest response, current threshold, and loading state to the page via onData/onThresholdPct/onLoading.
 * - When a new request starts or fails, the card calls onData(null) to invalidate the parent response, so the table clears, export is disabled, and the old range's result is not kept.
 * - The page hands members + the current threshold to the member table (local threshold filtering + CSV export).
 */
export function ModelConcentrationPage() {
  const [mode, setMode] = useState<FilterMode>("range");
  const [period, setPeriod] = useState<string | undefined>(undefined);
  const [periods, setPeriods] = useState<string[]>([]);
  const [range, setRange] = useState<[Dayjs, Dayjs] | null>(defaultRange());
  const [tick, setTick] = useState(0);

  const [res, setRes] = useState<ModelConcentrationResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [selectedBucket, setSelectedBucket] = useState<number | null>(null); // null = no drill-down

  const dateRange = useMemo(() => toDateRange(mode, period, range), [mode, period, range, tick]);

  // Clear a stale selection when the distribution changes: models/date-range take effect via the new res object identity (the card calls onData(null) first
  // on each request, then onData(newRes)). The "click the same bar to deselect" path does not touch res, so this effect will not reset it by mistake.
  useEffect(() => {
    setSelectedBucket(null);
  }, [res]);

  // Clicking the same bar again = deselect; the histogram only returns the raw index, and the page decides the select/deselect semantics here.
  const handleBucketClick = (index: number) =>
    setSelectedBucket((cur) => (cur === index ? null : index));

  useEffect(() => {
    let active = true;
    api
      .filters()
      .then((f: FiltersResponse) => {
        if (!active) return;
        setPeriods(f.periods);
        if (!period && f.periods.length) setPeriod(f.periods[0]);
      })
      .catch(() => {
        /* A failed period-list fetch does not block the date-range mode */
      });
    return () => {
      active = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <AppShell active="modelConcentration">
      <div style={{ marginBottom: 16 }}>
        <FilterBar
          mode={mode}
          onModeChange={setMode}
          periods={periods}
          period={period}
          onPeriodChange={setPeriod}
          range={range}
          onRangeChange={setRange}
          onRefresh={() => setTick((x) => x + 1)}
        />
      </div>

      <Space direction="vertical" size={16} style={{ width: "100%" }}>
        <ModelConcentrationCard
          range={dateRange}
          onData={setRes}
          onLoading={setLoading}
          selectedBucket={selectedBucket}
          onBucketClick={handleBucketClick}
        />
        <ConcentrationMembersTable
          members={res?.members ?? []}
          models={res?.models ?? []}
          range={dateRange}
          loading={loading}
          selectedBucket={selectedBucket}
          // bins must be the backend bucket count res.histogram.length passed through as-is; the table must not recompute it.
          // This is the precondition for filterMembersByBucket(members,i,bins).length === histogram[i].count to hold.
          bins={res?.histogram.length ?? 0}
          buckets={res?.histogram ?? []}
          onClearBucket={() => setSelectedBucket(null)}
        />
      </Space>
    </AppShell>
  );
}
