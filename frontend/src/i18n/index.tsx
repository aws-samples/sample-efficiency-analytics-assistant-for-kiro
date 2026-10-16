import { createContext, useCallback, useContext, useEffect, useMemo } from "react";
import type { ReactNode } from "react";

// The published build is English-only. The i18n layer is retained so the
// existing t()/tierLabel() call sites keep working, but it resolves a single
// English dictionary.
export type Lang = "en";

/** Supported languages and display names (for the dropdown). */
export const LANGUAGES: { value: Lang; label: string }[] = [
  { value: "en", label: "English" },
];

/**
 * Unified subscription-tier display (Kiro's official names + monthly price).
 * The backend returns the raw keys Pro / ProPlus / ProMax / Power;
 * the whole app displays through tierLabel() to avoid inconsistent names/casing.
 */
export const TIER_ORDER = ["Pro", "ProPlus", "ProMax", "Power"] as const;
export const TIER_LABELS: Record<string, string> = {
  Pro: "Pro",
  ProPlus: "Pro+",
  ProMax: "Pro Max",
  Power: "Power",
};
export const TIER_PRICE_USD: Record<string, number> = {
  Pro: 20,
  ProPlus: 40,
  ProMax: 100,
  Power: 200,
};

/** Tier display name; with price appends the monthly fee, e.g. "Power ($200)". Unknown keys returned as-is. */
export function tierLabel(key: string, withPrice = false): string {
  const name = TIER_LABELS[key] ?? key;
  const price = TIER_PRICE_USD[key];
  return withPrice && price != null ? `${name} ($${price})` : name;
}

/**
 * Copy dictionary. The key is a semantic identifier, the value is the English string.
 * Covers static UI copy only; proper nouns (department/person names) and AI replies are not translated here.
 */
const DICT: Record<string, string> = {
  // Header / common
  "app.title": "Kiro Efficiency Analytics Assistant",
  "app.admin": "Admin",
  "app.logout": "Sign out",
  "app.language": "Language",
  "common.refresh": "Refresh",
  "common.loadFailed": "Failed to load",
  "common.loading": "Loading…",

  // Navigation
  "nav.dashboard": "Dashboard",
  "nav.report": "Monthly report",
  "nav.modelConcentration": "Model Concentration",
  "nav.adminCenter": "Admin Center",

  // Admin center (left menu)
  "adminCenter.multipliers": "Multipliers",

  // Monthly report page
  "report.title": "Monthly usage report",
  "report.downloadCsv": "Download CSV",
  "report.upgrades": "Suggested upgrades (usage at/above threshold)",
  "report.downgrades": "Suggested downgrades (usage below threshold)",
  "report.shutdown": "Suggested shutdown (low usage / duplicate)",
  "report.col.name": "Name / Email",
  "report.col.department": "Department",
  "report.col.current": "Current tier",
  "report.col.usage": "Usage / Limit",
  "report.col.rate": "Usage rate",
  "report.col.suggested": "Suggested tier",
  "report.col.suggest": "Suggestion",
  "report.col.suggestShutdown": "Suggest shutdown next month",
  "report.col.duplicate": "Duplicate subscription",
  "report.col.primaryEmail": "Primary subscription email",
  "report.dedup.hint":
    "Multiple subscriptions of the same employee (same employee number) keep only the earliest; the rest are suggested for shutdown next month. Empty employee number is treated as an independent subscription.",
  "report.threshold.upgrade": "Upgrade threshold: usage ≥ {pct}%",
  "report.threshold.downgrade": "Downgrade threshold: usage < {pct}%",
  "report.threshold.shutdown": "Shutdown threshold: usage ≤ {pct}%",
  "report.count": "{n} users",
  "report.none": "None",

  // Model multiplier maintenance page
  "mult.title": "Model multipliers",
  "mult.intro":
    "Each model call consumes credits scaled by its multiplier. Official values come from Kiro's site; review estimated/unknown values and save.",
  "mult.col.model": "Model",
  "mult.col.multiplier": "Multiplier",
  "mult.col.source": "Source",
  "mult.source.official": "Official",
  "mult.source.estimated": "Estimated",
  "mult.source.edited": "Edited",
  "mult.source.unknown": "Unknown",
  "mult.estimatedHint": "No official entry; this is an estimated value—please review and save.",
  "mult.unknownHint": "Seen in data but has no multiplier—please set one and save.",
  "mult.unset": "Unset",
  "mult.save": "Save",
  "mult.saved": "Saved",

  // Filters
  "filter.byPeriod": "By period",
  "filter.byRange": "By date range",
  "filter.selectPeriod": "Select period",

  // KPI cards
  "kpi.registeredActive": "Registered / Active",
  "kpi.registeredActive.tip": "Registered = synced users; Active = users with usage in range",
  "kpi.activationRate": "Activation rate",
  "kpi.activationRate.tip": "Active users / Registered total",
  "kpi.dailyActive": "Daily active (avg/peak)",
  "kpi.dailyActive.tip": "Average / peak of daily active users in range",
  "kpi.unit.person": "users",
  "kpi.totalCredits": "Total credits",
  "kpi.overageUsers": "Overage users",
  "kpi.overageUsers.tip": "Users whose cumulative credits exceed their plan limit",
  "kpi.estCost": "Est. cost",
  "kpi.avgCredits": "Credits per active",

  // Tier distribution
  "tier.title": "Subscription tier distribution",
  "tier.unit": "",

  // Charts
  "chart.trend.title": "Daily credits usage trend",
  "chart.trend.credits": "Credits",
  "chart.trend.activeUsers": "Active users",
  "chart.models.title": "Model usage distribution",
  "chart.dept.title": "Credits by department / per capita",
  "chart.dept.total": "Total credits",
  "chart.dept.avg": "Per-capita credits",
  "chart.top.title": "Top {n} users",

  // Model concentration analysis (module A)
  "conc.title": "Model concentration",
  "conc.caption":
    "Metric = message-count share (not cost): the selected models' combined messages as a share of each user's total model messages.",
  "conc.excludeZero": "Denominator = users with messages in range (zero-usage users excluded).",
  "conc.selectModels": "Select models",
  "conc.threshold": "Concentration threshold: {pct}%",
  "conc.rate": "Share above threshold",
  "conc.counts": "Above threshold / total",
  "conc.empty": "Select one or more models to view the distribution",
  "conc.hist.xAxis": "Per-user share range",
  "conc.hist.yAxis": "Users",
  "conc.hist.users": "Users",
  "conc.noData": "Selected models have no usage in this range",
  // conc.table.title is superseded by conc.table.overTitle / conc.table.bucketTitle (kept, harmless).
  "conc.table.title": "Users above threshold",
  "conc.table.title.hint": "Member details",
  "conc.table.empty": "No users above the current threshold",
  "conc.table.empty.hint": "Click a bar above to view members in that range",
  "conc.col.name": "Name",
  "conc.col.department": "Department",
  "conc.col.email": "Email",
  "conc.col.targetMsgs": "Selected-model messages",
  "conc.col.totalMsgs": "Total messages",
  "conc.col.ratio": "Share (%)",
  "conc.export": "Export CSV",
  "conc.exportHint": "Export users above the current threshold",
  "conc.table.overTitle": "Employees above threshold (ratio ≥ {pct}%)",
  "conc.table.bucketTitle": "Employees with ratio {from}–{to}% ({n} total)",
  "conc.table.clearBucket": "Back to threshold view",
  "conc.hist.clickHint": "Click a bar to drill down the member list by share range",

  // User details table
  "users.title": "User details",
  "users.all": "All",
  "users.search": "Search by name / email",
  "users.download": "Download CSV",
  "users.downloadHint": "Export all users in the current department / search scope (including zero usage)",
  "users.downloadFail": "Download failed",
  "users.col.rank": "Rank",
  "users.col.name": "Name / Email",
  "users.col.department": "Department",
  "users.col.tier": "Tier",
  "users.col.credits": "Credits",
  "users.col.limit": "Limit",
  "users.col.balance": "Balance",
  "users.col.usageRate": "Usage rate",
  "users.col.messages": "Messages",
  "users.col.activeDays": "Active days",
  "users.col.status": "Status",
  "users.total": "{n} users",

  // User model usage drill-down (drawer)
  "userModels.title": "User model usage",
  "userModels.subtitle": "Messages per model in range",
  "userModels.messages": "Messages",
  "userModels.empty": "No model usage for this user in the selected range",

  // Status tags (backend returns key, frontend localizes)
  "status.overage": "Overage",
  "status.near_limit": "Near limit",
  "status.normal": "Normal",
  "status.unused": "Unused",

  // ChatBox
  "chat.title": "Natural-language Q&A",
  "chat.greeting": "Hi, I'm your usage analytics assistant. Ask me about credits, departments, overage, model distribution, etc.",
  "chat.placeholder": "Ask a usage-related question…",
  "chat.send": "Send",
  "chat.noAnswer": "(no answer)",
  "chat.error": "Error: ",
  "chat.suggest.1": "Which department used the most credits this month?",
  "chat.suggest.2": "Who triggered overage?",
  "chat.suggest.3": "How many users are in each tier?",
  "chat.suggest.4": "What's the recent credits usage trend?",

  // Sign-in / callback
  "login.title": "Kiro Efficiency Analytics Assistant",
  "login.subtitle": "Sign in with your dashboard account.",
  "login.button": "Sign in",
  "callback.failTitle": "Sign-in failed",
  "callback.back": "Back to sign-in",
  "callback.finishing": "Finishing sign-in…",
  "config.failed": "Config load failed: ",
};

interface I18nCtx {
  lang: Lang;
  setLang: (l: Lang) => void;
  t: (key: string, vars?: Record<string, string | number>) => string;
}

const Ctx = createContext<I18nCtx | null>(null);

export function LanguageProvider({ children }: { children: ReactNode }) {
  const lang: Lang = "en";

  // English-only build: setLang is a no-op kept for call-site compatibility.
  const setLang = useCallback((_l: Lang) => {}, []);

  const t = useCallback(
    (key: string, vars?: Record<string, string | number>) => {
      let s = DICT[key] ?? key;
      if (vars) {
        for (const [k, v] of Object.entries(vars)) {
          s = s.replace(new RegExp(`\\{${k}\\}`, "g"), String(v));
        }
      }
      return s;
    },
    []
  );

  // Browser tab title.
  useEffect(() => {
    document.title = DICT["app.title"];
  }, []);

  const value = useMemo(() => ({ lang, setLang, t }), [lang, setLang, t]);
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useI18n(): I18nCtx {
  const c = useContext(Ctx);
  if (!c) throw new Error("useI18n must be used within LanguageProvider");
  return c;
}
