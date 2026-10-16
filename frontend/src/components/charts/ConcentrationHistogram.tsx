import { useRef } from "react";
import type { EChartsOption } from "echarts";
import { Chart, axisStyle, esc } from "./Chart";
import type { ConcentrationBucket } from "../../api/types";
import { useI18n } from "../../i18n";

/** Highlight/normal bar colors; per-point itemStyle controls the selected bucket. */
const NORMAL_COLOR = "#38bdf8";
const SELECTED_COLOR = "#f59e0b";
/** Full-column highlight background for the selected column (covers zero-count columns—a zero-count bar has height 0, so markArea provides the visual feedback). */
const SELECTED_AREA = "rgba(245, 158, 11, 0.18)";

/**
 * Model-concentration histogram: x = per-user share range (e.g. 0–10%), y = number of employees.
 * - selectedBucket: which bucket to highlight (null = none selected).
 * - onBucketClick: clicking a **column** (anywhere in the column, including the space above the bar and zero-count columns) returns its bucket index,
 *   leaving the select/deselect semantics to the parent. The hit resolves the category column by the click's x pixel, independent of click height
 *   (via coordinate conversion, no visible column background required).
 *
 * Hit implementation (avoids "must click the thin bar exactly"):
 *   does not rely on series click (which only fires on the bar/background and is unreliable for zero-count columns); uses a zrender-level click instead:
 *   containPixel(inside grid) → convertFromPixel(xAxisIndex) for the nearest category index → range check → callback.
 *   The zr handler is registered once in onChartReady but reads the latest buckets/callback via ref to avoid capturing a stale closure
 *   (echarts-for-react's onChartReady fires only once).
 */
export function ConcentrationHistogram({
  buckets,
  selectedBucket = null,
  onBucketClick,
}: {
  buckets: ConcentrationBucket[];
  selectedBucket?: number | null;
  onBucketClick?: (index: number) => void;
}) {
  const { t } = useI18n();

  // Store the latest values in refs: the zr click handler is registered only once (onChartReady), and at runtime reads the refs to get the current
  // buckets and callback, avoiding a stale closure (hits stay correct when buckets change after switching models/threshold).
  const bucketsRef = useRef(buckets);
  bucketsRef.current = buckets;
  const onClickRef = useRef(onBucketClick);
  onClickRef.current = onBucketClick;
  const boundRef = useRef(false);

  const labels = buckets.map(
    (b) => `${Math.round(b.from * 100)}–${Math.round(b.to * 100)}%`
  );

  const option: EChartsOption = {
    tooltip: {
      trigger: "axis",
      axisPointer: { type: "shadow" },
      formatter: (p: any) => {
        const i = p[0].dataIndex;
        return `${esc(labels[i])}<br/>${esc(t("conc.hist.users"))}: <b>${esc(buckets[i].count)}</b>`;
      },
    },
    grid: { left: 48, right: 24, top: 16, bottom: 56 },
    xAxis: {
      type: "category",
      data: labels,
      name: t("conc.hist.xAxis"),
      nameLocation: "middle",
      // Place the axis name below the tick labels, with enough gap to avoid overlapping the horizontal labels
      nameGap: 36,
      ...axisStyle,
      // Show tick labels horizontally (no tilt); hide alternating labels when dense to avoid overlap
      axisLabel: { color: "#94a3b8", rotate: 0, interval: 0, fontSize: 11 },
    },
    yAxis: {
      type: "value",
      name: t("conc.hist.yAxis"),
      minInterval: 1,
      ...axisStyle,
    },
    series: [
      {
        type: "bar",
        data: buckets.map((b, i) => ({
          value: b.count,
          itemStyle:
            i === selectedBucket
              ? {
                  color: SELECTED_COLOR,
                  borderColor: "#fde68a",
                  borderWidth: 2,
                  borderRadius: [4, 4, 0, 0],
                }
              : { color: NORMAL_COLOR, borderRadius: [4, 4, 0, 0] },
        })),
        // Full-column highlight for the selected column (bottom to top), covering zero-height columns; silent so it does not intercept clicks.
        markArea:
          selectedBucket != null && labels[selectedBucket]
            ? {
                silent: true,
                itemStyle: { color: SELECTED_AREA },
                data: [
                  [
                    { xAxis: labels[selectedBucket] },
                    { xAxis: labels[selectedBucket] },
                  ],
                ],
              }
            : undefined,
        ...(onBucketClick ? { cursor: "pointer" } : {}),
        barMaxWidth: 28,
      },
    ],
  };

  // zrender-level click: clicking anywhere in a column (any height, including shadow/zero-count columns) resolves the category index by x pixel.
  const handleReady = onBucketClick
    ? (chart: any) => {
        if (boundRef.current) return; // bind only once per instance
        boundRef.current = true;
        const zr = chart.getZr();
        zr.on("click", (e: any) => {
          const cb = onClickRef.current;
          if (!cb) return;
          const x = e.offsetX;
          const y = e.offsetY;
          // The click must land inside the plot area (grid), otherwise ignore it (axis labels / space outside the chart do not trigger).
          if (!chart.containPixel({ gridIndex: 0 }, [x, y])) return;
          // Resolve the nearest category-axis index by x pixel (independent of y).
          const idx = chart.convertFromPixel({ xAxisIndex: 0 }, x);
          if (!Number.isFinite(idx)) return;
          const i = Math.round(idx);
          if (i < 0 || i >= bucketsRef.current.length) return;
          cb(i);
        });
      }
    : undefined;

  return <Chart option={option} height={300} onChartReady={handleReady} />;
}
