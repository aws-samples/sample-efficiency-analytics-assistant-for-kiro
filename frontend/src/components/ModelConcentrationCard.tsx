import { useEffect, useState } from "react";
import { Alert, Card, Col, Empty, Row, Select, Spin } from "antd";

import { api, ApiAuthError } from "../api/client";
import { login } from "../auth/auth";
import type { DateRange, ModelConcentrationResponse } from "../api/types";
import { ConcentrationHistogram } from "./charts/ConcentrationHistogram";
import { useI18n } from "../i18n";

/** Default pre-selection multiplier threshold: models with multiplier>=2.0. */
const PRESELECT_MULTIPLIER = 2.0;

/**
 * Model concentration analysis card (module A).
 * - Multi-select models + a Slider threshold (0–100%).
 * - Model options come from all models in /api/model-multipliers (currently 21), pre-selecting multiplier>=2.0 by default.
 * - Only a change to models/range triggers api.modelConcentration; a slider change recomputes locally from the returned ratios.
 * - Selecting a model with no usage gives a 0 share, which is not a bug; when none are in the allowlist the backend returns 400 and a neutral hint is shown.
 * - Metric = message-count share (not cost), with zero-usage users excluded.
 * - Exposes data and the current threshold to the page via onData/onThresholdPct callbacks (shared with the member table).
 */
export function ModelConcentrationCard({
  range,
  onData,
  onLoading,
  selectedBucket = null,
  onBucketClick,
}: {
  range: DateRange;
  onData?: (res: ModelConcentrationResponse | null) => void;
  onLoading?: (loading: boolean) => void;
  /** Which bucket to highlight, null = none (injected by the page). */
  selectedBucket?: number | null;
  /** Clicking a bucket returns its raw index (the page decides select/deselect). */
  onBucketClick?: (index: number) => void;
}) {
  const { t } = useI18n();
  const [selected, setSelected] = useState<string[]>([]);
  const [modelOptions, setModelOptions] = useState<{ label: string; value: string }[]>([]);
  const [preselectDone, setPreselectDone] = useState(false);
  const [data, setData] = useState<ModelConcentrationResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  // Default pre-selection: load all model multipliers, options = all models, pre-select multiplier>=2.0.
  useEffect(() => {
    if (preselectDone) return;
    let active = true;
    api
      .modelMultipliers()
      .then((res) => {
        if (!active) return;
        // The endpoint already returns models sorted ascending, so map them straight to options.
        setModelOptions(res.items.map((it) => ({ label: it.model, value: it.model })));
        const pre = res.items
          .filter((it) => it.multiplier != null && it.multiplier >= PRESELECT_MULTIPLIER)
          .map((it) => it.model);
        setSelected(pre);
        setPreselectDone(true);
      })
      .catch((e) => {
        // A failed multiplier fetch does not block the card: mark done and leave the selection empty for the user to choose.
        if (!active) return;
        setPreselectDone(true);
        if (e instanceof ApiAuthError) {
          login();
          return;
        }
        setError(String(e));
      });
    return () => {
      active = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [preselectDone]);

  // Only a change to models/range triggers a request (the slider does not).
  useEffect(() => {
    if (!preselectDone) return;
    if (selected.length === 0) {
      // Clearing the selection: synchronously reset local loading (otherwise clearing mid-request leaves a permanent spinner,
      // because the old effect's cleanup stops its finally from resetting), and clear the previous notice/error.
      setLoading(false);
      setError(null);
      setNotice(null);
      setData(null);
      onData?.(null);
      onLoading?.(false);
      return;
    }
    let active = true;
    setLoading(true);
    onLoading?.(true);
    setError(null);
    // Issuing a new request invalidates the parent's old data, so the table/export does not reuse the previous range's result.
    setData(null);
    onData?.(null);
    api
      // The frontend no longer uses the returned concentration stats (concentration_rate/concentrated_users/threshold);
      // the third argument only satisfies the backend's required threshold with a fixed 0.5, and the backend signature/return is unchanged.
      .modelConcentration(range, selected, 0.5)
      .then((res) => {
        if (!active) return;
        setData(res);
        setError(null);
        setNotice(null);
        onData?.(res);
      })
      .catch((e) => {
        if (!active) return;
        if (e instanceof ApiAuthError) {
          login();
          return;
        }
        const msg = String(e);
        if (msg.includes("no valid model selected")) {
          // All selected models are outside the Athena allowlist: a neutral hint, not an error.
          setData(null);
          setError(null);
          setNotice(t("conc.noData"));
          onData?.(null);
        } else {
          // Other failures: clear the data and the parent response, hide the table, disable export, do not keep the old range's result.
          setNotice(null);
          setError(msg);
          setData(null);
          onData?.(null);
        }
      })
      .finally(() => {
        if (!active) return;
        setLoading(false);
        onLoading?.(false);
      });
    return () => {
      active = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [range, selected, preselectDone]);

  return (
    <Card size="small" title={t("conc.title")}>
      <Alert
        type="info"
        showIcon
        style={{ marginBottom: 12 }}
        message={t("conc.caption")}
        description={t("conc.excludeZero")}
      />
      <Row gutter={[16, 16]} align="middle">
        <Col xs={24}>
          <div style={{ marginBottom: 4, color: "#94a3b8" }}>{t("conc.selectModels")}</div>
          <Select
            mode="multiple"
            allowClear
            style={{ width: "100%" }}
            placeholder={t("conc.selectModels")}
            value={selected}
            onChange={setSelected}
            options={modelOptions}
          />
        </Col>
      </Row>

      {notice && (
        <Alert type="info" showIcon style={{ marginBottom: 12 }} message={notice} />
      )}

      {error && (
        <Alert
          type="error"
          showIcon
          style={{ marginBottom: 12 }}
          message={t("common.loadFailed")}
          description={error}
        />
      )}

      <Spin spinning={loading}>
        {selected.length === 0 ? (
          <Empty description={t("conc.empty")} style={{ marginTop: 24 }} />
        ) : data ? (
          <>
            <ConcentrationHistogram
              buckets={data.histogram}
              selectedBucket={selectedBucket}
              onBucketClick={onBucketClick}
            />
            {onBucketClick && (
              <div style={{ color: "#94a3b8", marginTop: 8 }}>{t("conc.hist.clickHint")}</div>
            )}
          </>
        ) : (
          <Empty description={t("conc.empty")} style={{ marginTop: 24 }} />
        )}
      </Spin>
    </Card>
  );
}
