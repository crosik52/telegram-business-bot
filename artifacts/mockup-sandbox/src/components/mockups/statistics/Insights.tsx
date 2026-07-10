import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  RadialBarChart,
  RadialBar,
  Cell,
  PieChart,
  Pie,
} from "recharts";

const STATS = {
  total_messages: 3_847,
  total_users: 24,
  edited_messages: 312,
  deleted_messages: 589,
  media_messages: 1_204,
  text_messages: 2_643,
};

// Simulated daily activity (last 14 days)
const DAILY = [
  { day: "Jun 27", sent: 61, del: 4, edit: 8 },
  { day: "Jun 28", sent: 88, del: 12, edit: 14 },
  { day: "Jun 29", sent: 45, del: 3, edit: 5 },
  { day: "Jun 30", sent: 120, del: 22, edit: 19 },
  { day: "Jul 1",  sent: 95, del: 18, edit: 11 },
  { day: "Jul 2",  sent: 70, del: 7,  edit: 9 },
  { day: "Jul 3",  sent: 55, del: 5,  edit: 6 },
  { day: "Jul 4",  sent: 143, del: 31, edit: 22 },
  { day: "Jul 5",  sent: 102, del: 14, edit: 17 },
  { day: "Jul 6",  sent: 88, del: 9,  edit: 12 },
  { day: "Jul 7",  sent: 77, del: 8,  edit: 10 },
  { day: "Jul 8",  sent: 134, del: 28, edit: 24 },
  { day: "Jul 9",  sent: 111, del: 19, edit: 16 },
  { day: "Jul 10", sent: 92, del: 11, edit: 13 },
];

const MEDIA_BREAKDOWN = [
  { name: "Photos", value: 541, fill: "#6366f1" },
  { name: "Voice",  value: 298, fill: "#8b5cf6" },
  { name: "Video",  value: 187, fill: "#a78bfa" },
  { name: "Docs",   value: 122, fill: "#c4b5fd" },
  { name: "Other",  value: 56,  fill: "#e0e7ff" },
];

const deleteRate = Math.round((STATS.deleted_messages / STATS.total_messages) * 100);
const editRate   = Math.round((STATS.edited_messages  / STATS.total_messages) * 100);
// "Integrity score": how clean the conversation history is (0-100, higher = fewer deletions)
const integrity  = Math.max(0, 100 - deleteRate * 3);

const RADIAL_DATA = [{ value: integrity, fill: integrity > 70 ? "#10b981" : integrity > 40 ? "#f59e0b" : "#ef4444" }];

const CHART_TOOLTIP_STYLE = {
  contentStyle: {
    backgroundColor: "var(--tooltip-bg, #fff)",
    border: "1px solid #e2e8f0",
    borderRadius: "12px",
    fontSize: "12px",
    boxShadow: "0 4px 16px rgba(0,0,0,.08)",
  },
  labelStyle: { fontWeight: 600, color: "#0f172a" },
};

function StatPill({ icon, label, value, accent }: { icon: string; label: string; value: string; accent: string }) {
  return (
    <div className="flex items-center gap-3 bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-800 rounded-2xl px-5 py-4 flex-1">
      <span className="text-2xl">{icon}</span>
      <div>
        <p className="text-xs text-slate-500 dark:text-slate-400 uppercase tracking-wide">{label}</p>
        <p className={`text-2xl font-bold tabular-nums tracking-tight ${accent}`}>{value}</p>
      </div>
    </div>
  );
}

export function Insights() {
  return (
    <div className="min-h-screen bg-slate-50 dark:bg-slate-950 text-slate-900 dark:text-slate-100 p-6 md:p-8 font-sans">
      <div className="max-w-5xl mx-auto space-y-8">

        {/* Header */}
        <div className="flex items-baseline justify-between">
          <div>
            <h1 className="text-2xl font-semibold tracking-tight">Statistics</h1>
            <p className="text-sm text-slate-500 dark:text-slate-400 mt-0.5">Last 14 days · all connections</p>
          </div>
          <span className="text-xs bg-slate-200 dark:bg-slate-800 text-slate-500 dark:text-slate-400 px-3 py-1 rounded-full">
            Updated just now
          </span>
        </div>

        {/* Hero pills */}
        <div className="flex flex-col md:flex-row gap-3">
          <StatPill icon="💬" label="Total messages" value={STATS.total_messages.toLocaleString()} accent="text-indigo-500" />
          <StatPill icon="👤" label="Contacts" value={STATS.total_users.toLocaleString()} accent="text-violet-500" />
          <StatPill icon="🗑" label="Deletion rate" value={`${deleteRate}%`} accent="text-red-500" />
        </div>

        {/* Activity chart */}
        <div className="rounded-2xl bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-800 p-5">
          <h2 className="text-sm font-semibold mb-5 flex items-center gap-2">
            <span>📈</span> Daily Activity — Last 14 days
          </h2>
          <ResponsiveContainer width="100%" height={220}>
            <BarChart data={DAILY} barSize={8} barGap={2}>
              <CartesianGrid strokeDasharray="3 3" stroke="#f1f5f9" vertical={false} />
              <XAxis
                dataKey="day"
                tick={{ fontSize: 10, fill: "#94a3b8" }}
                axisLine={false}
                tickLine={false}
                interval={1}
              />
              <YAxis tick={{ fontSize: 10, fill: "#94a3b8" }} axisLine={false} tickLine={false} width={28} />
              <Tooltip
                {...CHART_TOOLTIP_STYLE}
                cursor={{ fill: "rgba(99,102,241,.06)", radius: 6 }}
              />
              <Bar dataKey="sent"  name="Sent"    fill="#6366f1" radius={[3, 3, 0, 0]} />
              <Bar dataKey="del"   name="Deleted" fill="#ef4444" radius={[3, 3, 0, 0]} />
              <Bar dataKey="edit"  name="Edited"  fill="#f59e0b" radius={[3, 3, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
          <div className="flex items-center gap-5 mt-3 justify-center text-xs text-slate-400">
            <span className="flex items-center gap-1.5"><span className="w-2.5 h-2.5 rounded-full bg-indigo-500 inline-block" />Sent</span>
            <span className="flex items-center gap-1.5"><span className="w-2.5 h-2.5 rounded-full bg-red-500 inline-block" />Deleted</span>
            <span className="flex items-center gap-1.5"><span className="w-2.5 h-2.5 rounded-full bg-amber-500 inline-block" />Edited</span>
          </div>
        </div>

        {/* Bottom row */}
        <div className="grid md:grid-cols-2 gap-6">

          {/* Media breakdown */}
          <div className="rounded-2xl bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-800 p-5">
            <h2 className="text-sm font-semibold mb-4 flex items-center gap-2">
              <span>📎</span> Media Breakdown
            </h2>
            <div className="flex gap-4 items-center">
              <ResponsiveContainer width={120} height={120}>
                <PieChart>
                  <Pie data={MEDIA_BREAKDOWN} cx="50%" cy="50%" innerRadius={32} outerRadius={50} dataKey="value" strokeWidth={0}>
                    {MEDIA_BREAKDOWN.map((d, i) => <Cell key={i} fill={d.fill} />)}
                  </Pie>
                </PieChart>
              </ResponsiveContainer>
              <div className="flex-1 space-y-2">
                {MEDIA_BREAKDOWN.map((d) => {
                  const pct = Math.round((d.value / STATS.media_messages) * 100);
                  return (
                    <div key={d.name} className="flex items-center gap-2">
                      <div className="w-2 h-2 rounded-full shrink-0" style={{ backgroundColor: d.fill }} />
                      <span className="text-xs flex-1 text-slate-600 dark:text-slate-300">{d.name}</span>
                      <span className="text-xs font-semibold tabular-nums">{d.value}</span>
                      <span className="text-xs text-slate-400 w-7 text-right">{pct}%</span>
                    </div>
                  );
                })}
              </div>
            </div>
          </div>

          {/* Integrity gauge */}
          <div className="rounded-2xl bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-800 p-5 flex flex-col">
            <h2 className="text-sm font-semibold mb-1 flex items-center gap-2">
              <span>🛡</span> Conversation Integrity
            </h2>
            <p className="text-xs text-slate-400 mb-4">Based on deletion rate vs total messages</p>
            <div className="flex items-center justify-center gap-8 flex-1">
              <div className="relative">
                <ResponsiveContainer width={130} height={130}>
                  <RadialBarChart
                    cx="50%"
                    cy="50%"
                    innerRadius={40}
                    outerRadius={60}
                    startAngle={210}
                    endAngle={-30}
                    data={RADIAL_DATA}
                    barSize={14}
                  >
                    <RadialBar dataKey="value" background={{ fill: "#f1f5f9" }} cornerRadius={8}>
                      {RADIAL_DATA.map((d, i) => <Cell key={i} fill={d.fill} />)}
                    </RadialBar>
                  </RadialBarChart>
                </ResponsiveContainer>
                <div className="absolute inset-0 flex flex-col items-center justify-center pointer-events-none">
                  <span className="text-2xl font-bold tabular-nums">{integrity}</span>
                  <span className="text-[10px] text-slate-400 uppercase tracking-wide">/ 100</span>
                </div>
              </div>
              <div className="space-y-3 text-sm">
                <div>
                  <p className="text-xs text-slate-400">Edit rate</p>
                  <p className="text-lg font-bold text-amber-500">{editRate}%</p>
                </div>
                <div>
                  <p className="text-xs text-slate-400">Delete rate</p>
                  <p className="text-lg font-bold text-red-500">{deleteRate}%</p>
                </div>
                <div>
                  <p className="text-xs text-slate-400">Media rate</p>
                  <p className="text-lg font-bold text-indigo-500">
                    {Math.round((STATS.media_messages / STATS.total_messages) * 100)}%
                  </p>
                </div>
              </div>
            </div>
          </div>
        </div>

        {/* Footer note */}
        <div className="rounded-2xl border border-sky-200 dark:border-sky-800 bg-sky-50 dark:bg-sky-900/20 p-4 text-sm text-sky-600 dark:text-sky-400 flex items-start gap-3">
          <span className="text-lg shrink-0">📤</span>
          <p>Media is auto-forwarded to the owner when contacts delete or edit messages — no external storage required.</p>
        </div>

      </div>
    </div>
  );
}
