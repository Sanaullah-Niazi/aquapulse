// The shapes of the API's answers (the same names as in /api/docs).

export type Role = "sysadmin" | "owner" | "resident";
export type Severity = "info" | "warning" | "critical";

export interface Me {
  id: string;
  email: string;
  full_name: string;
  phone: string | null;
  role: Role;
  is_active: boolean;
  building_ids: string[];
  home_ids: string[];
  two_factor: boolean;
}

export interface Home {
  id: string;
  building_id: string;
  unit_no: string;
  floor: number;
  occupants: number;
  auto_shutoff_enabled?: boolean;
}

export interface Reading {
  time: string;
  total_l: number;
  delta_l: number;
  flow_lpm: number;
  ph: number;
  temp_c: number;
  valve: string;
  rssi: number;
}

export interface Command {
  id: string;
  action: string | null;
  state: "queued" | "pending" | "succeeded" | "failed" | "rejected" | "timeout";
  result_reason: string | null;
}

export interface ValveLock {
  locked_state: string;
  reason: string;
  locked_at: string;
}

export interface HomeLive {
  home_id: string;
  device_id: string | null;
  online: boolean;
  last_seen_at: string | null;
  valve: string | null;
  reading: Reading | null;
  lock: ValveLock | null;
  pending_command: Command | null;
  unit_no: string;
  building_name: string;
  ph_safe_low: number;
  ph_safe_high: number;
}

export interface ConsumptionPoint {
  start: string;
  litres: number;
}

export interface Consumption {
  granularity: "hour" | "day" | "month";
  timezone: string;
  from: string;
  to: string;
  total_litres: number;
  points: ConsumptionPoint[];
}

export interface Comparison {
  month: string;
  until: string;
  litres: number;
  previous_month_litres: number | null;
  people_in_home: number;
  litres_per_person: number | null;
  building_litres_per_person: number | null;
  occupied_homes: number;
}

export interface BillEstimate {
  month: string;
  from: string;
  to: string;
  currency_code: string;
  litres: number | null;
  total_text: string | null;
  message: string;
}

export type BillStatus = "draft" | "issued" | "partially_paid" | "paid" | "overdue" | "void";

export interface BillSummary {
  id: string;
  home_id: string;
  issued_at?: string | null;
  bill_no: string | null;
  unit_no: string;
  bill_to: string;
  month: string;
  period_start: string;
  period_end: string;
  currency_code: string;
  litres: string;
  total: string;
  amount_paid: string;
  outstanding: string;
  total_text: string;
  status: BillStatus;
  due_date: string | null;
}

export interface BillLine {
  kind: "slab" | "extra" | "tax";
  description: string;
  quantity_l: string | null;
  rate: string | null;
  amount: string;
}

export interface Payment {
  id: string;
  amount: string;
  method: "cash" | "bank" | "stripe_test" | "other";
  reference: string;
  received_on: string;
  reversed_at: string | null;
}

export interface Bill extends BillSummary {
  month_days: number;
  reading_start: string | null;
  reading_end: string | null;
  subtotal: string;
  tax_total: string;
  lines: BillLine[];
  payments: Payment[];
  void_reason: string | null;
  can_pay_online: boolean;
}

export interface Alert {
  id: string;
  rule: string;
  severity: Severity;
  state: "open" | "acknowledged" | "resolved" | "auto_resolved";
  building_id: string | null;
  home_id: string | null;
  device_id: string | null;
  title: string;
  message: string;
  seen_count: number;
  opened_at: string;
  last_seen_at: string;
  cleared_at: string | null;
  acknowledged_at: string | null;
  resolved_at: string | null;
}

export interface NotificationPrefs {
  alert_email_min: "info" | "warning" | "critical" | "off";
  email_muted_rules: string[];
  quiet_from: string | null;
  quiet_until: string | null;
  /** M9: push choices for the Android app (edited there, kept as they are here). */
  push_min?: "info" | "warning" | "critical" | "off";
  push_muted_rules?: string[];
  /** Read-only: is push set up on the server? Never sent back. */
  push_available?: boolean;
}

/** A message from the live WebSocket (FR-25). */
export interface LiveEvent {
  type: "ready" | "telemetry" | "tank" | "device" | "command" | "alert" | "bill" | string;
  homeId?: string | null;
  deviceId?: string;
  commandId?: string;
  state?: string;
  billId?: string;
  time?: string;
  flowLpm?: number;
  ph?: number;
  tempC?: number;
  valve?: string;
  totalL?: number;
  deltaL?: number;
  [key: string]: unknown;
}

// ----- owner screens (M8 part 2) -----

export interface Building {
  id: string;
  owner_id?: string;
  name: string;
  address: string;
  timezone: string;
  currency_code: string;
  nonpayment_shutoff_enabled: boolean;
  due_days: number;
  code: string;
  bills_auto_issue: boolean;
}

export interface OverviewRow {
  home_id: string;
  unit_no: string;
  floor: number;
  residents: string[];
  litres_today: number;
  litres_month: number;
  flow_lpm: number;
  ph: number | null;
  temp_c: number | null;
  reading_at: string | null;
  device_id: string | null;
  online: boolean;
  last_seen_at: string | null;
  valve: string | null;
  locked: "open" | "closed" | null;
  lock_reason: string | null;
  open_alerts: number;
}

export interface Overview {
  building_id: string;
  name: string;
  timezone: string;
  currency_code: string;
  litres_today: number;
  litres_month: number;
  litres_last_month_to_date: number;
  flow_lpm: number;
  homes: number;
  homes_online: number;
  homes_with_device: number;
  valves_closed: number;
  valves_locked: number;
  alerts: Record<Severity, number>;
  tank: {
    tank_id: string;
    level_pct: number | null;
    level_l: number | null;
    pump_on: boolean | null;
    mode: string;
    online: boolean;
  } | null;
  unpaid_bills: number;
  unpaid_text: string;
  ph_safe_low: number;
  ph_safe_high: number;
  rows: OverviewRow[];
}

export interface Occupancy {
  id: string;
  home_id: string;
  user_id: string;
  full_name: string;
  email: string;
  move_in: string;
  move_out: string | null;
  is_billing_contact: boolean;
}

export interface Device {
  id: string;
  kind: "home" | "tank";
  home_id: string | null;
  tank_id: string | null;
  status: "active" | "revoked";
  online: boolean;
  fw_version: string | null;
  last_seen_at: string | null;
  created_at: string;
  credentials_changed_at: string | null;
  revoked_at: string | null;
  has_own_password: boolean;
}

export interface DeviceCredentials {
  device: Device;
  username: string;
  password: string;
  broker: { host: string; port: number; tls: boolean; ca_sha256: string | null };
  config: Record<string, unknown>;
  qr_svg: string;
}

export interface TankReading {
  time: string;
  level_pct: number | null;
  level_l: number | null;
  pump_on: boolean;
  sump_ok: boolean;
  sensor_ok: boolean;
  dry_run_protect: boolean;
  overflow_protect: boolean;
  mode: string;
}

export interface Tank {
  id: string;
  capacity_l: number;
  low_pct: number;
  high_pct: number;
  mode: string;
  pump_kw: number;
  device_id: string | null;
  online: boolean;
  last_seen_at: string | null;
  reading: TankReading | null;
  open_commands: Command[];
}

export interface TankHistory {
  levels: { start: string; level_pct: number | null }[];
  days: { day: string; pump_hours: number; energy_kwh: number }[];
}

export interface Slab {
  from_l: number;
  to_l: number | null;
  rate_per_kl: string;
}

export interface Charge {
  name: string;
  kind: "tax_percent" | "extra_percent" | "extra_fixed";
  value: string;
}

export interface Tariff {
  id: string;
  name: string;
  version: number;
  effective_from: string;
  currency_code: string;
  created_at: string;
  slabs: Slab[];
  charges: Charge[];
}

export interface Preview {
  lines: BillLine[];
  subtotal: string;
  tax_total: string;
  total: string;
  total_text: string;
}

export interface AlertRule {
  rule: string;
  title: string;
  severity: Severity;
  about: string;
  description: string;
  enabled: boolean;
  values: Record<string, number>;
  limits: Record<string, Record<string, number | string>>;
}

// ----- System Admin (M8 part 3) -----

export interface Health {
  ok: boolean;
  services: { name: "database" | "broker" | "ingestor" | "worker"; ok: boolean; detail: string; seen_at: string | null }[];
  devices_active: number;
  devices_online: number;
  readings_last_minute: number;
  received_since_start: number | null;
  rejected_since_start: Record<string, number>;
  notifications_failed_7d: number;
  last_audit_check: AuditCheck | null;
  last_backup_at: string | null;
}

export interface AuditCheck {
  ok: boolean;
  first_broken_id: number | null;
  entries: number;
  seconds: number;
  at: string;
  requested_by: string | null;
}

export interface AuditEntry {
  id: number;
  at: string;
  actor_id: string | null;
  actor_role: string | null;
  ip: string | null;
  action: string;
  target_type: string | null;
  target_id: string | null;
  building_id: string | null;
  details: Record<string, unknown>;
}

export interface NotificationRow {
  id: string;
  kind: string;
  status: string;
  attempts: number;
  last_error: string | null;
  created_at: string;
}

export interface UserRow {
  id: string;
  email: string;
  full_name: string;
  phone: string | null;
  role: Role;
  is_active: boolean;
}

// ----- M10: AI (FR-61, FR-63 to FR-67) -----

export interface Insight {
  alert_id: string;
  kind: string; // leak, burst, unusual_use
  title: string;
  severity: Severity;
  state: string;
  explanation: string;
  confidence: number | null;
  high_confidence: boolean;
  opened_at: string;
  last_seen_at: string;
  resolved_at: string | null;
  evidence: {
    points?: [string, number][];
    lowest_lpm?: number;
    burst_lpm?: number;
    litres?: number;
    usual_7d?: number;
    model_version?: number;
    top_features?: { feature: string; value: number; normal: number; distance: number }[];
    [key: string]: unknown;
  };
}

export interface ForecastDay {
  day: string;
  litres: number;
  low: number;
  high: number;
}

export interface Forecast {
  method: "holt_winters" | "seasonal_naive";
  history_days: number;
  explanation: string;
  recent: { day: string; litres: number }[];
  days: ForecastDay[];
  month_end: { last_day: string; litres: number; low: number; high: number };
  bill: {
    currency_code: string;
    total: string | null;
    total_text: string | null;
    low_text: string | null;
    high_text: string | null;
    message: string;
  };
}

export interface AiModel {
  id: string;
  kind: "forecast" | "unusual_use";
  version: number;
  trained_at: string;
  data_from: string;
  data_to: string;
  rows: number;
  metrics: Record<string, number | null>;
  params: Record<string, unknown>;
  live: boolean;
  note: string;
}
