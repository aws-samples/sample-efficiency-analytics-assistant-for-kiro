import { useCallback, useEffect, useMemo, useState } from "react";
import { Alert, Col, Row, Space, Spin } from "antd";
import type { Dayjs } from "dayjs";

import { api, ApiAuthError } from "../api/client";
import { login } from "../auth/auth";
import type {
  DepartmentRow,
  FiltersResponse,
  ModelSlice,
  SummaryResponse,
  TopUser,
  TrendPoint,
} from "../api/types";
import { KpiCards, TierDistribution } from "../components/KpiCards";
import { TrendChart } from "../components/charts/TrendChart";
import { ModelDonut } from "../components/charts/ModelDonut";
import { DepartmentChart } from "../components/charts/DepartmentChart";
import { TopUsersChart } from "../components/charts/TopUsersChart";
import { UsersTable } from "../components/UsersTable";
import { ChatBox } from "../components/ChatBox";
import { AppShell } from "../components/AppShell";
import { useI18n } from "../i18n";
import {
  FilterBar,
  defaultRange,
  toDateRange,
  type FilterMode,
} from "../components/FilterBar";

export function DashboardPage() {
  const { t } = useI18n();
  const [mode, setMode] = useState<FilterMode>("range");
  const [period, setPeriod] = useState<string | undefined>(undefined);
  const [range, setRange] = useState<[Dayjs, Dayjs] | null>(defaultRange());
  const [tick, setTick] = useState(0); // manual refresh

  const [filters, setFilters] = useState<FiltersResponse>({ periods: [], departments: [], models: [] });
  const [summary, setSummary] = useState<SummaryResponse | null>(null);
  const [trend, setTrend] = useState<TrendPoint[]>([]);
  const [models, setModels] = useState<ModelSlice[]>([]);
  const [departments, setDepartments] = useState<DepartmentRow[]>([]);
  const [topUsers, setTopUsers] = useState<TopUser[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const dateRange = useMemo(
    () => toDateRange(mode, period, range),
    [mode, period, range]
  );

  // First fetch the filter options (period list / departments / models)
  useEffect(() => {
    api
      .filters()
      .then((f) => {
        setFilters(f);
        if (!period && f.periods.length) setPeriod(f.periods[0]);
      })
      .catch(handleErr);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const handleErr = useCallback((e: unknown) => {
    if (e instanceof ApiAuthError) {
      // token expired / unauthorized -> sign in again
      login();
      return;
    }
    setError(String(e));
  }, []);

  // Fetch all panel data
  useEffect(() => {
    // In period mode a period must already be selected
    if (mode === "period" && !period) return;
    let active = true;
    setLoading(true);
    setError(null);
    Promise.all([
      api.summary(dateRange),
      api.trend(dateRange),
      api.models(dateRange),
      api.departments(dateRange),
      api.topUsers(dateRange, 15),
    ])
      .then(([s, t, m, d, tu]) => {
        if (!active) return;
        setSummary(s);
        setTrend(t.series);
        setModels(m.models);
        setDepartments(d.departments);
        setTopUsers(tu.users);
      })
      .catch(handleErr)
      .finally(() => active && setLoading(false));
    return () => {
      active = false;
    };
  }, [dateRange, tick, mode, period, handleErr]);

  return (
    <AppShell active="dashboard">
        <div style={{ marginBottom: 16 }}>
          <FilterBar
            mode={mode}
            onModeChange={setMode}
            periods={filters.periods}
            period={period}
            onPeriodChange={setPeriod}
            range={range}
            onRangeChange={setRange}
            onRefresh={() => setTick((x) => x + 1)}
          />
        </div>

        {error && (
          <Alert type="error" showIcon message={t("common.loadFailed")} description={error} style={{ marginBottom: 16 }} />
        )}

        <Spin spinning={loading}>
          {summary && (
            <Space direction="vertical" size={16} style={{ width: "100%" }}>
              <KpiCards summary={summary} />

              <TierDistribution tiers={summary.tiers} />

              <Row gutter={[16, 16]}>
                <Col xs={24}>
                  <TrendChart series={trend} />
                </Col>
              </Row>

              <Row gutter={[16, 16]}>
                <Col xs={24} lg={8}>
                  <ModelDonut models={models} />
                </Col>
                <Col xs={24} lg={16}>
                  <DepartmentChart departments={departments} />
                </Col>
              </Row>

              <Row gutter={[16, 16]}>
                <Col xs={24} lg={14}>
                  <TopUsersChart users={topUsers} />
                </Col>
                <Col xs={24} lg={10}>
                  <ChatBox />
                </Col>
              </Row>

              <Row gutter={[16, 16]}>
                <Col xs={24}>
                  <UsersTable range={dateRange} departments={filters.departments} />
                </Col>
              </Row>
            </Space>
          )}
        </Spin>
    </AppShell>
  );
}
