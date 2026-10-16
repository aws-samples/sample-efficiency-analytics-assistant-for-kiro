import { useCallback, useEffect, useMemo, useState } from "react";
import { Alert, App as AntdApp, Button, Card, InputNumber, Space, Spin, Table, Tag, Tooltip } from "antd";
import { ReloadOutlined, SaveOutlined } from "@ant-design/icons";
import type { ColumnsType } from "antd/es/table";
import { api, ApiAuthError } from "../api/client";
import { login } from "../auth/auth";
import type { ModelMultiplier, ModelMultiplierSource } from "../api/types";
import { useI18n } from "../i18n";

const SOURCE_COLOR: Record<ModelMultiplierSource, string> = {
  official: "green",
  estimated: "gold",
  edited: "blue",
  unknown: "default",
};

/**
 * Model multiplier maintenance page: an Ant Table with model + multiplier columns (inline InputNumber editing) + a source Tag.
 * Has no AppShell of its own — it is wrapped by the admin-center layout. All copy goes through i18n.
 */
export function MultiplierMaintenancePage() {
  const { t } = useI18n();
  const { message } = AntdApp.useApp();
  const [rows, setRows] = useState<ModelMultiplier[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Inline-edit draft: model -> new multiplier (null means the user cleared it).
  const [drafts, setDrafts] = useState<Record<string, number | null>>({});

  const load = useCallback(() => {
    setLoading(true);
    setError(null);
    api
      .modelMultipliers()
      .then((r) => {
        setRows(r.items);
        setDrafts({});
      })
      .catch((e) => {
        if (e instanceof ApiAuthError) return login();
        setError(String(e));
      })
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const setDraft = (model: string, value: number | null) =>
    setDrafts((d) => ({ ...d, [model]: value }));

  // Current displayed value: prefer the draft, otherwise take the server value.
  const effective = (r: ModelMultiplier): number | null =>
    r.model in drafts ? drafts[r.model] : r.multiplier;

  // Pending items: draft entries filled with a non-negative value that differ from the server value.
  const dirtyItems = useMemo(
    () =>
      rows
        .filter((r) => r.model in drafts)
        .map((r) => ({ model: r.model, multiplier: drafts[r.model] }))
        .filter(
          (it): it is { model: string; multiplier: number } =>
            typeof it.multiplier === "number" && it.multiplier >= 0,
        )
        .filter((it) => {
          const orig = rows.find((r) => r.model === it.model)?.multiplier ?? null;
          return it.multiplier !== orig;
        }),
    [rows, drafts],
  );

  const save = async () => {
    if (dirtyItems.length === 0) return;
    setSaving(true);
    try {
      await api.updateModelMultipliers(dirtyItems);
      message.success(t("mult.saved"));
      load();
    } catch (e) {
      if (e instanceof ApiAuthError) return login();
      message.error(String(e));
    } finally {
      setSaving(false);
    }
  };

  const columns: ColumnsType<ModelMultiplier> = [
    { title: t("mult.col.model"), dataIndex: "model", width: 220, ellipsis: true },
    {
      title: t("mult.col.multiplier"),
      dataIndex: "multiplier",
      width: 160,
      render: (_v, r) => (
        <InputNumber
          min={0}
          step={0.1}
          style={{ width: 120 }}
          value={effective(r)}
          placeholder={t("mult.unset")}
          onChange={(v) => setDraft(r.model, v)}
        />
      ),
    },
    {
      title: t("mult.col.source"),
      dataIndex: "source",
      width: 140,
      render: (s: ModelMultiplierSource) => {
        const tag = <Tag color={SOURCE_COLOR[s] ?? "default"}>{t(`mult.source.${s}`)}</Tag>;
        if (s === "estimated") {
          return <Tooltip title={t("mult.estimatedHint")}>{tag}</Tooltip>;
        }
        if (s === "unknown") {
          return <Tooltip title={t("mult.unknownHint")}>{tag}</Tooltip>;
        }
        return tag;
      },
    },
  ];

  return (
    <Card
      size="small"
      title={t("mult.title")}
      extra={
        <Space>
          <Button icon={<ReloadOutlined />} onClick={load}>
            {t("common.refresh")}
          </Button>
          <Button
            type="primary"
            icon={<SaveOutlined />}
            loading={saving}
            disabled={dirtyItems.length === 0}
            onClick={save}
          >
            {t("mult.save")}
          </Button>
        </Space>
      }
    >
      <Alert type="info" showIcon style={{ marginBottom: 12 }} message={t("mult.intro")} />
      {error && (
        <Alert
          type="error"
          showIcon
          message={t("common.loadFailed")}
          description={error}
          style={{ marginBottom: 12 }}
        />
      )}
      <Spin spinning={loading}>
        <Table<ModelMultiplier>
          rowKey="model"
          size="small"
          columns={columns}
          dataSource={rows}
          pagination={false}
          scroll={{ x: "max-content" }}
        />
      </Spin>
    </Card>
  );
}
