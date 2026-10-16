import { useMemo } from "react";
import { Button, Card, Space, Table } from "antd";
import { DownloadOutlined } from "@ant-design/icons";
import type { ColumnsType } from "antd/es/table";

import type { ConcentrationBucket, ConcentrationMember, DateRange } from "../api/types";
import { useI18n } from "../i18n";
import { buildCsv, buildFilename, filterMembersByBucket } from "./concentrationExport";

/**
 * Member details table + CSV export (driven only by clicking a bar).
 * - No bucket selected (selectedBucket==null): the row set is empty, showing a neutral title + the hint "click a bar above to view members in a range".
 * - A bucket selected: the row set = members filtered locally by that bucket (filterMembersByBucket), already descending.
 * - Export CSV: generated purely client-side (Blob + a[download]) with a UTF-8 BOM; the filename contains models/bucket/time range.
 * - The pure logic (filtering / CSV / filename) is extracted into concentrationExport.ts for unit testing and reuse.
 */
export function ConcentrationMembersTable({
  members,
  models,
  range,
  loading,
  selectedBucket = null,
  bins,
  buckets,
  onClearBucket,
}: {
  members: ConcentrationMember[];
  models: string[];
  range: DateRange;
  loading?: boolean;
  /** null = above-threshold view; a number = bucket drill-down view. */
  selectedBucket?: number | null;
  /** = data.histogram.length, used so the bucket-filter metric stays consistent with bar heights. */
  bins?: number;
  /** = data.histogram, used to display the bucket's from/to in the title/filename. */
  buckets?: ConcentrationBucket[];
  /** Clear the drill-down and return to the above-threshold view. */
  onClearBucket?: () => void;
}) {
  const { t } = useI18n();

  const inBucketView = selectedBucket != null;

  const rows = useMemo(
    () => (inBucketView && bins ? filterMembersByBucket(members, selectedBucket!, bins) : []),
    [members, selectedBucket, bins, inBucketView]
  );

  const title = inBucketView
    ? t("conc.table.bucketTitle", {
        from: Math.round((buckets?.[selectedBucket!]?.from ?? 0) * 100),
        to: Math.round((buckets?.[selectedBucket!]?.to ?? 0) * 100),
        n: rows.length,
      })
    : t("conc.table.title.hint");

  const columns: ColumnsType<ConcentrationMember> = [
    { title: t("conc.col.name"), dataIndex: "display_name" },
    { title: t("conc.col.department"), dataIndex: "department", width: 160 },
    { title: t("conc.col.email"), dataIndex: "email" },
    {
      title: t("conc.col.targetMsgs"),
      dataIndex: "target_messages",
      align: "right",
      width: 160,
    },
    {
      title: t("conc.col.totalMsgs"),
      dataIndex: "total_messages",
      align: "right",
      width: 130,
    },
    {
      title: t("conc.col.ratio"),
      dataIndex: "ratio",
      align: "right",
      width: 110,
      render: (v: number) => `${(v * 100).toFixed(1)}%`,
    },
  ];

  const exportCsv = () => {
    if (rows.length === 0) return;
    const header = [
      t("conc.col.name"),
      t("conc.col.department"),
      t("conc.col.email"),
      t("conc.col.targetMsgs"),
      t("conc.col.totalMsgs"),
      t("conc.col.ratio"),
    ];
    const text = buildCsv(header, rows);
    const blob = new Blob([text], { type: "text/csv;charset=utf-8;" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = buildFilename(
      models,
      0, // thresholdPct placeholder: the non-bucket view cannot export (rows is always empty), and the bucket view takes the bucket branch and does not read this value
      range,
      inBucketView ? (buckets?.[selectedBucket!] ?? null) : null
    );
    a.click();
    URL.revokeObjectURL(url);
  };

  return (
    <Card
      size="small"
      title={title}
      extra={
        <Space>
          {inBucketView && (
            <Button size="small" onClick={onClearBucket}>
              {t("conc.table.clearBucket")}
            </Button>
          )}
          <Button
            icon={<DownloadOutlined />}
            onClick={exportCsv}
            disabled={rows.length === 0}
            title={t("conc.exportHint")}
          >
            {t("conc.export")}
          </Button>
        </Space>
      }
    >
      <Table<ConcentrationMember>
        rowKey="email"
        size="small"
        loading={loading}
        columns={columns}
        dataSource={rows}
        pagination={{ defaultPageSize: 20, showSizeChanger: true, pageSizeOptions: [10, 20, 50] }}
        locale={{ emptyText: inBucketView ? t("conc.table.empty") : t("conc.table.empty.hint") }}
        scroll={{ x: 700 }}
      />
    </Card>
  );
}
