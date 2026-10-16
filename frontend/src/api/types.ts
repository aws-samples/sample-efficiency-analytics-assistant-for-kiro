/** Types matching the backend/api response shapes. */

export interface DateRange {
  from?: string;
  to?: string;
  period?: string;
}

export interface SummaryResponse {
  range: { from: string | null; to: string | null };
  kpis: {
    registered_users: number;
    total_users: number;
    active_users: number;
    activation_rate: number;
    daily_active: number;
    daily_active_avg: number;
    daily_active_peak: number;
    total_credits: number;
    overage_users: number;
    est_cost: number;
    avg_credits_per_active: number;
  };
  tiers: { Pro: number; ProPlus: number; ProMax: number; Power: number };
  est_cost_note: string;
}

export interface TrendPoint {
  date: string;
  credits: number;
  active_users: number;
}
export interface TrendResponse {
  series: TrendPoint[];
}

export interface ModelSlice {
  model: string;
  messages: number;
}
export interface ModelsResponse {
  models: ModelSlice[];
}

export interface DepartmentRow {
  department: string;
  users: number;
  total_credits: number;
  avg_credits: number;
}
export interface DepartmentsResponse {
  departments: DepartmentRow[];
}

export interface TopUser {
  email: string;
  display_name: string;
  department: string;
  subscription_tier: string;
  credits_used: number;
}
export interface TopUsersResponse {
  users: TopUser[];
}

export interface UserRow {
  email: string;
  display_name: string;
  department: string;
  subscription_tier: string;
  credits_used: number;
  usage_limit: number;
  balance: number;
  usage_rate: number;
  is_overage: boolean;
  est_cost: number;
  total_messages: number;
  active_days: number;
  status: string;
}
export interface UsersResponse {
  total: number;
  page: number;
  page_size: number;
  sort: string;
  direction: string;
  items: UserRow[];
}

export interface FiltersResponse {
  periods: string[];
  departments: string[];
  models: string[];
}

// Monthly report
export interface ReportRecoRow {
  email: string;
  display_name: string;
  department: string;
  subscription_tier: string;
  credits_used: number;
  usage_limit: number;
  usage_rate: number;
  suggested_tier: string;
}
// Usage-rate threshold model: all user rows (with one-step up/down suggestions)
export interface ReportUserRow {
  email: string;
  display_name: string;
  department: string;
  subscription_tier: string;
  credits_used: number;
  usage_limit: number;
  usage_rate: number | null; // null when the backend usage_limit=0; frontend uses ?? 0 when comparing
  suggested_up: string | null;
  suggested_down: string | null;
  employee_number?: string;
  created_date?: string;
  is_duplicate_subscription?: boolean;
  primary_email?: string;
}
export interface ReportResponse {
  range: { from: string | null; to: string | null };
  kpis: {
    registered_users: number;
    active_users: number;
    activation_rate: number;
    total_credits: number;
    overage_users: number;
    est_cost: number;
    daily_active_avg: number;
    daily_active_peak: number;
    tiers: { Pro: number; ProPlus: number; ProMax: number; Power: number };
  };
  upgrades: ReportRecoRow[];
  downgrades: ReportRecoRow[];
  users: ReportUserRow[];
  est_cost_note: string;
}

// Model multipliers
export type ModelMultiplierSource = "official" | "estimated" | "edited" | "unknown";
export interface ModelMultiplier {
  model: string;
  multiplier: number | null;
  source: ModelMultiplierSource;
}
export interface ModelMultipliersResponse {
  items: ModelMultiplier[];
}

// Model concentration
export interface ConcentrationBucket {
  from: number;
  to: number;
  count: number;
}
export interface ConcentrationMember {
  email: string;
  display_name: string;
  department: string;
  target_messages: number;
  total_messages: number;
  ratio: number;
}
export interface ModelConcentrationResponse {
  range: { from: string | null; to: string | null };
  models: string[];
  threshold: number;
  total_users: number;
  concentrated_users: number;
  concentration_rate: number;
  histogram: ConcentrationBucket[];
  ratios: number[];
  members: ConcentrationMember[];
}

