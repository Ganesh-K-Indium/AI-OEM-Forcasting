// Hand-maintained mirror of backend Pydantic contracts (app/schemas). `npm run gen:types` produces the
// authoritative generated file at src/lib/api-schema.d.ts; this file keeps the app compiling offline.
export type Role = "admin" | "planner" | "sales_rep" | "steward" | "viewer";
export type ReasonCode = "PROJECT_DELAY" | "NEW_WIN" | "CAPACITY_CAP" | "CUSTOMER_DIRECT_GUIDANCE";
export type Level = "TOTAL" | "REGION" | "OEM" | "PRODUCT" | "OEM_REGION" | "BOTTOM";
export const REASONS: ReasonCode[] = ["PROJECT_DELAY", "NEW_WIN", "CAPACITY_CAP", "CUSTOMER_DIRECT_GUIDANCE"];

export interface User { email: string; full_name: string; role: Role; scope_oems: string[] | null; scope_regions: string[] | null }
export interface Meta {
  synthetic_mode: boolean; label: string; current_run_id: string | null; cycle_month: string | null; cycle_status: string | null;
  horizon: number; fx_policy: string; currency: string; units_label: string; version: string;
}
export interface Run { id: string; cycle_month: string; kind: string; status: string; horizon: number; is_synthetic: boolean; created_at: string; locked_at: string | null; summary: Record<string, any> | null; error: string | null }
export interface Filters { oems: string[]; regions: string[]; products: { code: string; name?: string }[]; horizons: number[] }

export interface HistPoint { month: string; units: number; revenue: number; asp: number | null }
export interface FcPoint {
  month: string; horizon: number; units_p10: number; units_p50: number; units_p90: number;
  revenue_p10: number; revenue_p50: number; revenue_p90: number; baseline_units: number; uplift_units: number; gross_uplift_units: number;
  uplift_revenue: number; asp_usd: number; override_revenue: number | null; override_units: number | null; override_reason: ReasonCode | null;
  consensus_units: number; consensus_revenue: number; backlog_value: number | null; coverage: number | null; capacity_units: number | null; model_name: string | null;
}
export interface Explorer {
  run_id: string; level: Level; oem: string; region: string; product: string; segment: string | null; champion_model: string | null;
  scenario_tag: string | null; cycle_month: string; synthetic: boolean; history: HistPoint[]; forecast: FcPoint[]; totals: Record<string, number>;
}
export interface OppRow { opportunity_id: number; sfdc_id: string; oem: string; region: string; product: string; stage: number; win_prob: number; rep_probability: number; expected_units: number; unweighted_units: number; share_of_uplift: number }
export interface Override {
  id: number; run_id: string; level: Level; oem: string; region: string; product: string; month: string; ai_p50_forecast: number; ai_p50_units: number;
  override_basis: string; sales_override_value: number; override_units: number; override_revenue: number; consensus_value: number; reason_code: ReasonCode;
  comment: string | null; user_id: string; timestamp: string; revision: number; status: string; approval_status: string; is_synthetic: boolean;
}
export interface Fva {
  run_id: string; scope: string; horizon: number | null; n_obs: number; wmape_naive: number | null; wmape_ai: number | null; wmape_consensus: number | null;
  fva_sales: number | null; fva_ai: number | null; fva_sales_ci_low: number | null; fva_sales_ci_high: number | null; significant: boolean;
}
export interface Audit { id: number; ts: string; user_id: string; action: string; entity_type: string; entity_id: string; before: any; after: any; hash: string }
export interface Detail {
  explorer: Explorer; opportunities: OppRow[]; overrides: Override[]; fva: Fva[]; audit: Audit[]; coverage: Record<string, any>[]; drivers: Record<string, any>;
}
export interface BenchRow {
  model_name: string; model_family: string; segment: string; horizon: number; wmape: number | null; mae: number | null; mase: number | null; bias: number | null;
  pinball: number | null; coverage80: number | null; n_obs: number; is_champion: boolean; status: string; note: string | null; prior_models: string | null;
}
export interface Benchmark { run_id: string; rows: BenchRow[]; champions: Record<string, any>[]; segments: Record<string, number>; backtest_origins: string[]; notes: string[] }

export interface Dashboard {
  run_id: string; cycle_month: string; cycle_status: string | null; locked: boolean; synthetic: boolean;
  kpis: {
    total_consensus_revenue: number; total_ai_revenue: number; consensus_vs_ai_pct: number | null; backlog_coverage: number | null; backlog_value_t3: number; forecast_value_t3: number;
    revenue_at_risk: number; pipeline_at_risk: number; upside_potential: number; overall_wmape_realized: number | null; backtest_wmape: number | null; active_overrides: number; net_uplift_revenue: number;
  };
  ai_vs_actual: null | { months: string[]; ai_revenue: number; actual_revenue: number; variance_pct: number | null; ai_wmape: number; consensus_wmape: number; n: number };
  trend: { history: HistPoint[]; forecast: FcPoint[] };
  top_risks: { id: number; type: string; severity: string; title: string; impact: number; oem: string; region: string; product: string }[];
  headline: Record<string, any> | null; segments: Record<string, number> | null;
}
export interface Alert {
  id: number; run_id: string; alert_type: string; severity: string; oem: string; region: string; product: string; first_month: string; last_month: string;
  financial_impact_usd: number; title: string; detail: Record<string, any> | null; status: string; owner: string | null; note: string | null;
}
export interface RiskSummary { run_id: string; open_alerts: number; by_type: Record<string, Record<string, any>>; revenue_at_risk_usd: number; top_oems: Record<string, any>[] }
export interface Threshold { id: number; product_code: string | null; region_code: string | null; min_coverage: number; use_historical_baseline: boolean; concentration_threshold: number; concentration_max_stage: number; min_uplift_share: number }

export interface Account { id: number; erp_customer_id: string; name: string; account_type: string; country: string | null; region_code: string | null; duns: string | null; tax_id: string | null; mapped_to: Record<string, any>[]; status: string }
export interface Candidate { mapping_id: number; account_id: number; account_name: string; account_type: string; suggested_oem: string; region_code: string; confidence: number; source: string; evidence: Record<string, any> | null; created_at: string | null }
export interface Allocation { oem_code: string; region_code: string; allocation_pct: number }
export interface Rule { id: number; name: string; rule_type: string; priority: number; confidence: number; pattern: string | null; target_oem_code: string | null; target_region: string | null; enabled: boolean }
export interface Oem { id: number; code: string; name: string; identifiers: Record<string, any>[]; aliases: string[] }

export interface Job { id: string; job_type: string; state: string; progress: number; message: string | null; params: Record<string, any> | null; result: Record<string, any> | null; error: string | null; created_by: string | null; created_at: string; finished_at: string | null }
export interface Dq { check_name: string; severity: string; passed: boolean; metric: number | null; threshold: number | null; message: string; run_ts: string }
export interface Drift { run_id: string; kind: string; name: string; value: number; threshold: number; breached: boolean }
export interface Setting { key: string; value: any; description: string | null }
export interface Cycle { [k: string]: any }
