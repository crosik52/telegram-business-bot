import {
  PieChart,
  Pie,
  Cell,
  BarChart,
  Bar,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
} from "recharts";

const STATS = {
  total_messages: 3_847,
  total_users: 24,
  edited_messages: 312,
  deleted_messages: 589,
  media_messages: 1_204,
  text_messages: 2_643,
};

const MEDIA_BREAKDOWN = [
  { media_type: "photo", count: 541, color: "#6366f1" },
  { media_type: "voice", count: 298, color: "#8b5cf6" },
  { media_type: "video", count: 187, color: "#a78bfa" },
  { media_type: "document", count: 122, color: "#c4b5fd" },
  { media_type: "sticker", count: 56, color: "#ddd6fe" },
];

const MEDIA_ICONS: Record<string, string> = {
  photo: "🖼",
  voice: "🎙",
  video: "🎬",
  document: "📄",
  sticker: "🎯",
  audio: "🎵",
  animation: "🎞",
  video_note: "⭕",
};

const MEDIA_LABELS: Record<string, string> = {
  photo: "Photos",
  voice: "Voice",
  video: "Video",
  document: "Docs",
  sticker: "Stickers",
  audio: "Audio",
  animation: "GIFs",
  video_note: "Rounds",
};

const deleteRate = Math.round((STATS.deleted_messages / STATS.total_messages) * 100);
const editRate = Math.round((STATS.edited_messages / STATS.total_messages) * 100);
const mediaRate = Math.round((STATS.media_messages / STATS.total_messages) * 100);

const SPLIT_DATA = [
  { name: "Text", value: STATS.text_messages, color: "#6366f1" },
  { name: "Media", value: STATS.media_messages, color: "#a78bfa" },
];

const kpis = [
  {
    label: "Total Messages",
    value: STATS.total_messages.toLocaleString(),
    sub: `+14% this month`,
    icon: "💬",
    accent: "text-brand",
  },
  {
    label: "Active Contacts",
    value: STATS.total_users.toLocaleString(),
    sub: "Unique counterparts",
    icon: "👤",
    accent: "text-violet-500",
  },
  {
    label: "Deleted",
    value: STATS.deleted_messages.toLocaleString(),
    sub: `${deleteRate}% of total`,
    icon: "🗑",
    accent: "text-red-500",
  },
  {
    label: "Edited",
    value: STATS.edited_messages.toLocaleString(),
    sub: `${editRate}% of total`,
    icon: "✏️",
    accent: "text-amber-500",
  },
  {
    label: "With Media",
    value: STATS.media_messages.toLocaleString(),
    sub: `${mediaRate}% of total`,
    icon: "📎",
    accent: "text-sky-500",
  },
  {
    label: "Text Only",
    value: STATS.text_messages.toLocaleString(),
    sub: `${100 - mediaRate}% of total`,
    icon: "📝",
    accent: "text-emerald-500",
  },
];

function RateBar({ label, value, max, color }: { label: string; value: number; max: number; color: string }) {
  const pct = Math.round((value / max) * 100);
  return (
    <div className="flex items-center gap-3">
      <span className="text-xs text-slate-500 dark:text-slate-400 w-16 shrink-0">{label}</span>
      <div className="flex-1 bg-slate-100 dark:bg-slate-800 rounded-full h-2 overflow-hidden">
        <div
          className="h-full rounded-full transition-all duration-700"
          style={{ width: `${pct}%`, backgroundColor: color }}
        />
      </div>
      <span className="text-xs font-medium tabular-nums w-12 text-right text-slate-700 dark:text-slate-300">
        {value.toLocaleString()}
      </span>
      <span className="text-xs text-slate-400 w-8 text-right">{pct}%</span>
    </div>
  );
}

const CustomTooltip = ({ active, payload }: any) => {
  if (!active || !payload?.length) return null;
  return (
    <div className="bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-lg px-3 py-2 shadow-lg text-xs">
      <p className="font-semibold">{payload[0].name}</p>
      <p className="text-slate-500">{payload[0].value.toLocaleString()} messages</p>
    </div>
  );
};

export function Compact() {
  return (
    <div className="min-h-screen bg-slate-50 dark:bg-slate-950 text-slate-900 dark:text-slate-100 p-6 md:p-8 font-sans">
      <div className="max-w-5xl mx-auto space-y-8">

        {/* Header */}
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Statistics</h1>
          <p className="text-sm text-slate-500 dark:text-slate-400 mt-0.5">
            All-time totals across all connections
          </p>
        </div>

        {/* KPI grid */}
        <div className="grid grid-cols-2 md:grid-cols-3 gap-3">
          {kpis.map((kpi) => (
            <div
              key={kpi.label}
              className="rounded-2xl bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-800 p-4 flex flex-col gap-1"
            >
              <div className="flex items-center justify-between">
                <span className="text-xs uppercase tracking-wide text-slate-500 dark:text-slate-400">{kpi.label}</span>
                <span className="text-lg">{kpi.icon}</span>
              </div>
              <p className="text-3xl font-bold tabular-nums tracking-tight mt-1">{kpi.value}</p>
              <p className="text-xs text-slate-400">{kpi.sub}</p>
            </div>
          ))}
        </div>

        {/* Two-column row */}
        <div className="grid md:grid-cols-2 gap-6">

          {/* Message Health */}
          <div className="rounded-2xl bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-800 p-5">
            <h2 className="text-sm font-semibold mb-4 flex items-center gap-2">
              <span>📊</span> Message Health
            </h2>
            <div className="space-y-3">
              <RateBar label="Deleted" value={STATS.deleted_messages} max={STATS.total_messages} color="#ef4444" />
              <RateBar label="Edited" value={STATS.edited_messages} max={STATS.total_messages} color="#f59e0b" />
              <RateBar label="Media" value={STATS.media_messages} max={STATS.total_messages} color="#6366f1" />
              <RateBar label="Text only" value={STATS.text_messages} max={STATS.total_messages} color="#10b981" />
            </div>
          </div>

          {/* Text vs Media donut */}
          <div className="rounded-2xl bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-800 p-5">
            <h2 className="text-sm font-semibold mb-4 flex items-center gap-2">
              <span>🍩</span> Content Split
            </h2>
            <div className="flex items-center gap-6">
              <ResponsiveContainer width={140} height={140}>
                <PieChart>
                  <Pie
                    data={SPLIT_DATA}
                    cx="50%"
                    cy="50%"
                    innerRadius={42}
                    outerRadius={60}
                    dataKey="value"
                    strokeWidth={0}
                  >
                    {SPLIT_DATA.map((entry, i) => (
                      <Cell key={i} fill={entry.color} />
                    ))}
                  </Pie>
                  <Tooltip content={<CustomTooltip />} />
                </PieChart>
              </ResponsiveContainer>
              <div className="space-y-3 flex-1">
                {SPLIT_DATA.map((d) => (
                  <div key={d.name} className="flex items-center gap-2">
                    <div className="w-2.5 h-2.5 rounded-full shrink-0" style={{ backgroundColor: d.color }} />
                    <div className="flex-1 min-w-0">
                      <p className="text-xs font-medium">{d.name}</p>
                      <p className="text-xs text-slate-400 tabular-nums">{d.value.toLocaleString()}</p>
                    </div>
                    <span className="text-xs font-semibold tabular-nums">
                      {Math.round((d.value / STATS.total_messages) * 100)}%
                    </span>
                  </div>
                ))}
              </div>
            </div>
          </div>
        </div>

        {/* Media breakdown bar chart */}
        <div className="rounded-2xl bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-800 p-5">
          <h2 className="text-sm font-semibold mb-5 flex items-center gap-2">
            <span>📎</span> Media Breakdown
          </h2>
          <div className="space-y-3">
            {MEDIA_BREAKDOWN.map((item) => {
              const pct = Math.round((item.count / STATS.media_messages) * 100);
              return (
                <div key={item.media_type} className="flex items-center gap-3">
                  <span className="text-base w-6 text-center shrink-0">
                    {MEDIA_ICONS[item.media_type] ?? "📁"}
                  </span>
                  <span className="text-sm text-slate-600 dark:text-slate-300 w-20 shrink-0">
                    {MEDIA_LABELS[item.media_type] ?? item.media_type}
                  </span>
                  <div className="flex-1 bg-slate-100 dark:bg-slate-800 rounded-full h-2.5 overflow-hidden">
                    <div
                      className="h-full rounded-full"
                      style={{ width: `${(item.count / MEDIA_BREAKDOWN[0].count) * 100}%`, backgroundColor: item.color }}
                    />
                  </div>
                  <span className="text-sm font-medium tabular-nums w-10 text-right">{item.count}</span>
                  <span className="text-xs text-slate-400 w-8 text-right">{pct}%</span>
                </div>
              );
            })}
          </div>
        </div>

        {/* Info footer */}
        <div className="rounded-2xl border border-sky-200 dark:border-sky-800 bg-sky-50 dark:bg-sky-900/20 p-4 text-sm text-sky-600 dark:text-sky-400 flex items-start gap-3">
          <span className="text-lg shrink-0">📤</span>
          <p>
            Media files are resent to the owner automatically when a contact deletes or edits them —
            using Telegram file references stored in this database. No external storage required.
          </p>
        </div>

      </div>
    </div>
  );
}
