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
