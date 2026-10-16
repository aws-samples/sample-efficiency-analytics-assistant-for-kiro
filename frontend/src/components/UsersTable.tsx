import { useEffect, useMemo, useState } from "react";
import { Button, Card, Input, Progress, Table, Tabs, Tag, Tooltip, message } from "antd";
import { DownloadOutlined } from "@ant-design/icons";
import type { ColumnsType } from "antd/es/table";
import type { SorterResult } from "antd/es/table/interface";
import { api } from "../api/client";
import type { DateRange, UserRow } from "../api/types";
import { useI18n, tierLabel } from "../i18n";
import { UserModelsDrawer } from "./UserModelsDrawer";

// Status key -> color (the key comes from the backend, the label is localized in the frontend)
const STATUS_COLOR: Record<string, string> = {
  overage: "red",
  near_limit: "orange",
  normal: "green",
  unused: "default",
};

const TIER_COLOR: Record<string, string> = {
  Pro: "default",
  ProPlus: "blue",
  ProMax: "purple",
  Power: "gold",
};

interface Props {
  range: DateRange;
  departments: string[];
}

export function UsersTable({ range, departments }: Props) {
  const { t, lang } = useI18n();
  const numLocale = lang === "en" ? "en-US" : "zh-CN";
  const [dept, setDept] = useState<string | undefined>(undefined);
  const [rows, setRows] = useState<UserRow[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(10);
  const [sort, setSort] = useState("credits_used");
  const [direction, setDirection] = useState<"asc" | "desc">("desc");
  const [search, setSearch] = useState("");        // Submitted search term (sent to the backend)
  const [searchInput, setSearchInput] = useState(""); // Current input-box value
  const [selected, setSelected] = useState<UserRow | null>(null);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [downloading, setDownloading] = useState(false);

  async function handleDownload() {
    setDownloading(true);
    try {
      // Export scope = current department tab + search (unpaginated, including zero-usage users)
      await api.usersExportCsv(range, { department: dept, search, sort, direction });
    } catch (e) {
      message.error(`${t("users.downloadFail")}: ${(e as Error).message}`);
    } finally {
      setDownloading(false);
    }
  }

  useEffect(() => {
    let active = true;
    setLoading(true);
    api
      .users(range, { department: dept, search, sort, direction, page, page_size: pageSize })
      .then((r) => {
        if (!active) return;
        setRows(r.items);
        setTotal(r.total);
      })
      .finally(() => active && setLoading(false));
    return () => {
      active = false;
    };
  }, [range, dept, search, sort, direction, page, pageSize]);

  // Reset to the first page when the filter (date/department/search) changes
  useEffect(() => setPage(1), [range, dept, search]);

  const columns: ColumnsType<UserRow> = useMemo(
    () => [
      {
        title: t("users.col.rank"),
        key: "rank",
        width: 64,
        render: (_v, _r, i) => (page - 1) * pageSize + i + 1,
      },
      {
        title: t("users.col.name"),
        key: "name",
        render: (_v, r) => (
          <div>
            <div>{r.display_name || r.email.split("@")[0]}</div>
            <div style={{ color: "#64748b", fontSize: 12 }}>{r.email}</div>
          </div>
        ),
      },
      { title: t("users.col.department"), dataIndex: "department", width: 160 },
      {
        title: t("users.col.tier"),
        dataIndex: "subscription_tier",
        width: 130,
        render: (tier: string) =>
          tier ? (
            <Tag color={TIER_COLOR[tier] ?? "default"}>{tierLabel(tier, true)}</Tag>
          ) : (
            <span style={{ color: "#94a3b8" }}>-</span>
          ),
      },
      {
        title: t("users.col.credits"),
        dataIndex: "credits_used",
        width: 100,
        sorter: true,
        render: (v: number) => v.toLocaleString(numLocale, { maximumFractionDigits: 2 }),
      },
      {
        title: t("users.col.limit"),
        dataIndex: "usage_limit",
        width: 90,
        sorter: true,
        render: (v: number) => (v ? v.toLocaleString(numLocale, { maximumFractionDigits: 0 }) : "-"),
      },
      { title: t("users.col.balance"), dataIndex: "balance", width: 90, sorter: true },
      {
        title: t("users.col.usageRate"),
        dataIndex: "usage_rate",
        width: 150,
        sorter: true,
        render: (v: number) => {
          const pct = Math.min(v * 100, 100);
          const status = v >= 1 ? "exception" : v >= 0.8 ? "normal" : "active";
          return <Progress percent={Math.round(pct)} size="small" status={status as any} />;
        },
      },
      { title: t("users.col.messages"), dataIndex: "total_messages", width: 90, sorter: true },
      { title: t("users.col.activeDays"), dataIndex: "active_days", width: 90, sorter: true },
      {
        title: t("users.col.status"),
        dataIndex: "status",
        width: 90,
        render: (s: string) => <Tag color={STATUS_COLOR[s] ?? "default"}>{t(`status.${s}`)}</Tag>,
      },
    ],
    [page, pageSize, t, numLocale]
  );

  const tabItems = [{ key: "__all__", label: t("users.all") }].concat(
    departments.map((d) => ({ key: d, label: d }))
  );

  return (
    <Card size="small" title={t("users.title")}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12, flexWrap: "wrap" }}>
        <Tabs
          size="small"
          activeKey={dept ?? "__all__"}
          items={tabItems}
          onChange={(k) => setDept(k === "__all__" ? undefined : k)}
          style={{ flex: 1, minWidth: 0 }}
        />
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <Input.Search
            allowClear
            placeholder={t("users.search")}
            value={searchInput}
            onChange={(e) => setSearchInput(e.target.value)}
            onSearch={(v) => setSearch(v.trim())}
            style={{ width: 240, marginBottom: 8 }}
          />
          <Tooltip title={t("users.downloadHint")}>
            <Button
              icon={<DownloadOutlined />}
              loading={downloading}
              onClick={handleDownload}
              style={{ marginBottom: 8 }}
            >
              {t("users.download")}
            </Button>
          </Tooltip>
        </div>
      </div>
      <Table<UserRow>
        rowKey="email"
        size="small"
        loading={loading}
        columns={columns}
        dataSource={rows}
        onRow={(record) => ({
          onClick: () => {
            setSelected(record);
            setDrawerOpen(true);
          },
          style: { cursor: "pointer" },
        })}
        pagination={{
          current: page,
          pageSize,
          total,
          showSizeChanger: true,
          pageSizeOptions: [10, 20, 50],
          showTotal: (n) => t("users.total", { n }),
        }}
        onChange={(pg, _filters, sorter) => {
          setPage(pg.current ?? 1);
          setPageSize(pg.pageSize ?? 10);
          const s = sorter as SorterResult<UserRow>;
          if (s.field) {
            setSort(String(s.field));
            setDirection(s.order === "ascend" ? "asc" : "desc");
          }
        }}
        scroll={{ x: 900 }}
      />
      <UserModelsDrawer
        user={selected}
        range={range}
        open={drawerOpen}
        onClose={() => setDrawerOpen(false)}
      />
    </Card>
  );
}
