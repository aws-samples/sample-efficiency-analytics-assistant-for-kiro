/**
 * Pure functions for the model-concentration member details: threshold filtering, CSV assembly, filename encoding.
 * Extracted into a standalone module for unit testing and reuse; no React / DOM dependencies.
 * Metric note: the row set = members filtered locally by ratio>=threshold, already sorted by ratio descending, order preserved.
 */
import type { ConcentrationMember, DateRange } from "../api/types";

/**
 * Neutralize CSV formula injection: a cell starting with = + - @ or a leading tab/carriage-return
 * may be interpreted as a formula by spreadsheets (Excel, etc.). Prepend a single quote to break
 * formula parsing, then hand off to quote escaping. Tab (\t) and carriage-return (\r) are also known
 * formula-trigger prefixes and are covered too.
 *
 * Pure-function examples (the repo has no frontend test framework, so examples are pinned here as a spec for review):
 *   neutralizeFormula("=HYPERLINK(1,2)") === "'=HYPERLINK(1,2)"
 *   neutralizeFormula("+1+1")            === "'+1+1"
 *   neutralizeFormula("-2")              === "'-2"
 *   neutralizeFormula("@SUM(A1)")        === "'@SUM(A1)"
 *   neutralizeFormula("\t=1")            === "'\t=1"   // leading tab
 *   neutralizeFormula("\r=1")            === "'\r=1"   // leading carriage-return
 *   neutralizeFormula("Alice")           === "Alice"    // an ordinary name is unchanged
 *   neutralizeFormula("a@example.com")         === "a@example.com"  // @ not in the first position does not trigger
 *   neutralizeFormula("")                === ""         // an empty string gets no prefix
 * After csvCell, a neutralized result with commas/quotes/newlines/carriage-returns is still wrapped in quotes, e.g.:
 *   csvCell("=1,2")   === "\"'=1,2\""   (formula-neutralized + comma quote-wrapped)
 *   csvCell("\r=1+1") === "\"'\r=1+1\"" (formula-neutralized + carriage-return quote-wrapped, \r does not split the record)
 *   csvCell("Alice\r=HYPERLINK(1)") === "\"Alice\r=HYPERLINK(1)\"" (an internal carriage-return is wrapped in quotes as a whole,
 *                                        it does not start a new record, and the = after it does not land at the start of a new cell)
 */
export function neutralizeFormula(s: string): string {
  if (s.length > 0 && /^[=+\-@\t\r]/.test(s)) {
    return `'${s}`;
  }
  return s;
}

/**
 * CSV cell encoding: first neutralize the formula prefix (see neutralizeFormula),
 * then do standard quote escaping—when the value contains a comma/double-quote/newline(\n)/carriage-return(\r)
 * wrap it in double quotes and turn inner " into "".
 * The carriage-return must be part of the quoting test: otherwise a \r inside a field would split one cell into two
 * CSV records, letting the = + - @ formula prefix after the carriage-return land at the start of a new record's cell
 * and bypass formula neutralization that only inspects the first character of a field.
 */
export function csvCell(v: string | number): string {
  const s = neutralizeFormula(String(v));
  if (/[",\n\r]/.test(s)) {
    return `"${s.replace(/"/g, '""')}"`;
  }
  return s;
}

/** Filter members by threshold (percent); members are already descending, filtering preserves order. */
export function filterMembers(
  members: ConcentrationMember[],
  thresholdPct: number
): ConcentrationMember[] {
  const th = thresholdPct / 100;
  return members.filter((m) => m.ratio >= th);
}

/**
 * Map a per-user ratio ∈ [0,1] to the histogram bucket index, mirroring the backend:
 * idx = min(bins-1, int(ratio*bins)): ratio==1.0 falls into the last bucket ([0.9,1.0] closed interval),
 * the other buckets are left-closed right-open [from, to). bins = number of histogram buckets (data.histogram.length).
 * An out-of-range ratio (<0 or >1, which should not happen) is also clamped to the first/last bucket to stay consistent with bar heights.
 *
 * Math.floor is equivalent to Python int() (truncate toward zero) for a non-negative ratio ∈ [0,1],
 * and the backend bucketing uses the un-rounded member.ratio (the histogram's round is only for from/to display,
 * not for drill-down), so the frontend and backend bucket assignments match one-for-one.
 *
 * Pure-function examples (the repo has no frontend test framework, so examples are pinned here as a spec for review):
 *   bucketIndexOf(0.0, 10)  === 0
 *   bucketIndexOf(0.05, 10) === 0
 *   bucketIndexOf(0.1, 10)  === 1
 *   bucketIndexOf(0.3, 10)  === 3
 *   bucketIndexOf(0.95, 10) === 9
 *   bucketIndexOf(1.0, 10)  === 9   // last-bucket clamp (min(bins-1, …)), consistent with the backend
 */
export function bucketIndexOf(ratio: number, bins: number): number {
  if (bins <= 0) return 0;
  const idx = Math.floor(ratio * bins);
  if (idx < 0) return 0;
  return Math.min(bins - 1, idx);
}

/**
 * Get the members of a bucket; bins must be passed as data.histogram.length to keep the metric consistent with bar heights.
 * Array.filter preserves order, so the bucket view keeps the backend's ratio-descending order.
 *
 * Core invariant: filterMembersByBucket(members, i, bins).length === data.histogram[i].count,
 * holds if and only if bins === data.histogram.length is passed through as-is.
 * Anchored to the backend test_api.py::test_model_concentration_ok fixture (ratios [0.9,0.6,0.2,0.0], bins 10
 * -> fall into buckets 9/6/2/0, each bucket count 1):
 *   filterMembersByBucket(fixture, 9, 10).length === 1  (ratio 0.9)
 *   filterMembersByBucket(fixture, 6, 10).length === 1  (ratio 0.6)
 *   filterMembersByBucket(fixture, 2, 10).length === 1  (ratio 0.2)
 *   filterMembersByBucket(fixture, 0, 10).length === 1  (ratio 0.0)
 */
export function filterMembersByBucket(
  members: ConcentrationMember[],
  bucketIndex: number,
  bins: number
): ConcentrationMember[] {
  return members.filter((m) => bucketIndexOf(m.ratio, bins) === bucketIndex);
}

/** Sanitize illegal filename characters (/ \ : * ? " < > | and whitespace). */
export function sanitizeFilename(s: string): string {
  return s.replace(/[/\\:*?"<>|\s]+/g, "_");
}

/**
 * Build the CSV filename: contains models / threshold / time range, using language-independent encoding (no natural-language literals).
 * - Models: `allmodels` when the selection is empty; `m1_m2_m3_plusN` when there are more than 3 (N = remaining count).
 * - View: default (above threshold) is `th<integer percent>`; the bucket drill-down view is `bucket<from%>-<to%>`.
 * - Time range: `<from>_<to>` (hyphens removed) / `to<to>` (end only) / `<period>` / `allrange`.
 * All segments are stable ASCII encodings and do not change with the UI language, so they do not go through DICT.
 *
 * Pure-function examples (for review):
 *   buildFilename(models, 50, range) contains `th50` (byte-for-byte identical to the old behavior when there is no bucket argument)
 *   buildFilename(models, 50, range, {from:0.9, to:1.0}) contains `bucket90-100`
 */
export function buildFilename(
  models: string[],
  thresholdPct: number,
  range: DateRange,
  bucket?: { from: number; to: number } | null
): string {
  let modelPart = "allmodels";
  if (models.length > 0) {
    modelPart =
      models.length > 3
        ? `${models.slice(0, 3).join("_")}_plus${models.length - 3}`
        : models.join("_");
  }

  let rangePart = "allrange";
  if (range.from && range.to) {
    rangePart = `${range.from}_${range.to}`.replace(/-/g, "");
  } else if (range.to) {
    rangePart = `to${range.to.replace(/-/g, "")}`;
  } else if (range.period) {
    rangePart = range.period.replace(/-/g, "");
  }

  const viewPart = bucket
    ? `bucket${Math.round(bucket.from * 100)}-${Math.round(bucket.to * 100)}`
    : `th${Math.round(thresholdPct)}`;

  const raw = `kiro-model-concentration-${modelPart}-${viewPart}-${rangePart}.csv`;
  return sanitizeFilename(raw);
}

/**
 * Build the CSV text (with UTF-8 BOM).
 * @param headers column headers (passed in for the current UI language, translated by the caller via DICT)
 * @param rows filtered, sorted member rows
 */
export function buildCsv(headers: string[], rows: ConcentrationMember[]): string {
  const lines = [headers.map(csvCell).join(",")];
  rows.forEach((m) => {
    lines.push(
      [
        csvCell(m.display_name),
        csvCell(m.department),
        csvCell(m.email),
        csvCell(m.target_messages),
        csvCell(m.total_messages),
        csvCell((m.ratio * 100).toFixed(1)),
      ].join(",")
    );
  });
  // \ufeff = UTF-8 BOM, ensures Excel detects the encoding correctly (consistent with users_export).
  return "\ufeff" + lines.join("\n");
}
