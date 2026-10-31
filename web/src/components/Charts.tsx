// Charts (FR-54): bars for litres, lines for pH and temperature. Hovering or
// tapping shows the exact value. Each chart also has a hidden text summary
// for screen readers.
import {
  Bar,
  BarChart,
  CartesianGrid,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

const axis = { stroke: "var(--muted)", fontSize: 12 };
const tooltipStyle = {
  contentStyle: { background: "var(--surface)", border: "1px solid var(--border)", color: "var(--text)" },
  labelStyle: { color: "var(--muted)" },
};

export interface Point {
  label: string;
  value: number;
}

export function BarsChart({ data, unit, title }: { data: Point[]; unit: string; title: string }) {
  return (
    <figure style={{ margin: 0 }}>
      <div className="chart" aria-hidden="true">
        <ResponsiveContainer>
          <BarChart accessibilityLayer={false} data={data} margin={{ top: 8, right: 8, bottom: 0, left: -12 }}>
            <CartesianGrid stroke="var(--border)" vertical={false} />
            <XAxis dataKey="label" tick={axis} interval="preserveStartEnd" minTickGap={16} />
            <YAxis tick={axis} />
            <Tooltip {...tooltipStyle} formatter={(v: number) => [`${v.toLocaleString()} ${unit}`, title]} />
            <Bar dataKey="value" fill="var(--chart-1)" radius={[4, 4, 0, 0]} isAnimationActive={false} />
          </BarChart>
        </ResponsiveContainer>
      </div>
      <figcaption className="sr-only">
        {title}: {data.map((p) => `${p.label} ${p.value} ${unit}`).join(", ")}
      </figcaption>
    </figure>
  );
}

export function LinesChart({
  data,
  unit,
  title,
  low,
  high,
  domain,
}: {
  data: Point[];
  unit: string;
  title: string;
  low?: number;
  high?: number;
  domain?: [number, number];
}) {
  const values = data.map((p) => p.value);
  const last = data.at(-1);
  const summary = last
    ? `${title}: ${values.length} points, lowest ${Math.min(...values)}, highest ${Math.max(...values)} ${unit}; latest ${last.value} ${unit} at ${last.label}`
    : title;
  return (
    <figure style={{ margin: 0 }}>
      <div className="chart" aria-hidden="true">
        <ResponsiveContainer>
          <LineChart accessibilityLayer={false} data={data} margin={{ top: 8, right: 8, bottom: 0, left: -12 }}>
            <CartesianGrid stroke="var(--border)" vertical={false} />
            <XAxis dataKey="label" tick={axis} interval="preserveStartEnd" minTickGap={24} />
            <YAxis tick={axis} domain={domain ?? ["auto", "auto"]} />
            <Tooltip {...tooltipStyle} formatter={(v: number) => [`${v} ${unit}`, title]} />
            {low !== undefined && <ReferenceLine y={low} stroke="var(--bad)" strokeDasharray="4 4" />}
            {high !== undefined && <ReferenceLine y={high} stroke="var(--bad)" strokeDasharray="4 4" />}
            <Line dataKey="value" stroke="var(--chart-1)" dot={false} strokeWidth={2} isAnimationActive={false} />
          </LineChart>
        </ResponsiveContainer>
      </div>
      <figcaption className="sr-only">{summary}</figcaption>
    </figure>
  );
}
