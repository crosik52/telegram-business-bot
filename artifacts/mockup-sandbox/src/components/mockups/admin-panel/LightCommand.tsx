import { useState } from "react";

const COLORS = ["#6366f1","#06b6d4","#10b981","#f59e0b","#ef4444","#8b5cf6","#ec4899","#14b8a6"];
function colorFor(s: string) { let h=0; for(let i=0;i<s.length;i++) h=(h*31+s.charCodeAt(i))>>>0; return COLORS[h%COLORS.length]; }
function initials(n: string) { const p=n.trim().split(/\s+/); return p.length===1?p[0][0].toUpperCase():(p[0][0]+p[1][0]).toUpperCase(); }

const USERS = [
  { id: 1, name: "Алексей Морозов", username: "alex_m", msgs: 1842, chats: 14, coins: 5200, sub: true, blocked: false, active: "2 мин" },
  { id: 2, name: "Марина Соколова", username: "marina_s", msgs: 976, chats: 8, coins: 1100, sub: false, blocked: false, active: "1 ч" },
  { id: 3, name: "Дмитрий Кузнецов", username: "dkuznetsov", msgs: 3421, chats: 22, coins: 8900, sub: true, blocked: false, active: "5 мин" },
  { id: 4, name: "Ольга Петрова", username: "olga_p", msgs: 234, chats: 3, coins: 0, sub: false, blocked: true, active: "5 дн" },
  { id: 5, name: "Иван Сидоров", username: "ivan_sid", msgs: 561, chats: 7, coins: 2300, sub: false, blocked: false, active: "40 мин" },
];

const BARS = [12,8,19,24,15,31,28,22,35,41,38,29,44,51,47,39,56,62,58,49,67,71,65,78,83,74,91,88,95,102];
type Tab = "users"|"analytics"|"broadcast"|"log"|"more";

export function LightCommand() {
  const [tab, setTab] = useState<Tab>("users");
  const [search, setSearch] = useState("");

  const filtered = USERS.filter(u =>
    u.name.toLowerCase().includes(search.toLowerCase()) || u.username.includes(search.toLowerCase())
  );

  return (
    <div style={{
      width: 390, minHeight: 844, background: "#f0f2f8",
      color: "#111", fontFamily: "-apple-system, BlinkMacSystemFont, 'SF Pro Text', sans-serif",
      display: "flex", flexDirection: "column",
    }}>
      {/* ── Header ── */}
      <div style={{
        background: "linear-gradient(135deg, #6366f1 0%, #8b5cf6 100%)",
        padding: "48px 20px 20px",
        position: "relative", overflow: "hidden", flexShrink: 0
      }}>
        {/* bg circles */}
        <div style={{ position: "absolute", top: -30, right: -30, width: 140, height: 140, borderRadius: "50%", background: "rgba(255,255,255,0.08)" }}/>
        <div style={{ position: "absolute", top: 20, right: 60, width: 60, height: 60, borderRadius: "50%", background: "rgba(255,255,255,0.05)" }}/>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 20, position: "relative" }}>
          <div>
            <div style={{ fontSize: 13, color: "rgba(255,255,255,0.65)", fontWeight: 600, marginBottom: 2 }}>Добро пожаловать,</div>
            <div style={{ fontSize: 22, fontWeight: 800, color: "#fff" }}>Admin Panel 🛠</div>
          </div>
          <div style={{
            width: 42, height: 42, borderRadius: "50%",
            background: "rgba(255,255,255,0.2)", display: "flex", alignItems: "center",
            justifyContent: "center", fontSize: 18, backdropFilter: "blur(10px)"
          }}>👨‍💼</div>
        </div>
        {/* KPI strip */}
        <div style={{ display: "grid", gridTemplateColumns: "repeat(4,1fr)", gap: 6, position: "relative" }}>
          {[
            { v: "847", l: "Польз.", bg: "rgba(255,255,255,0.18)" },
            { v: "634", l: "Активных", bg: "rgba(16,185,129,0.3)" },
            { v: "12", l: "Заблок.", bg: "rgba(239,68,68,0.3)" },
            { v: "52K", l: "Сообщ.", bg: "rgba(245,158,11,0.3)" },
          ].map((k,i) => (
            <div key={i} style={{ background: k.bg, borderRadius: 12, padding: "8px 6px", textAlign: "center", backdropFilter: "blur(10px)" }}>
              <div style={{ fontSize: 17, fontWeight: 900, color: "#fff", lineHeight: 1, letterSpacing: -0.3 }}>{k.v}</div>
              <div style={{ fontSize: 9.5, color: "rgba(255,255,255,0.75)", marginTop: 3, fontWeight: 700 }}>{k.l}</div>
            </div>
          ))}
        </div>
      </div>

      {/* ── Pill tabs ── */}
      <div style={{
        background: "#fff", padding: "12px 16px 0",
        boxShadow: "0 1px 0 rgba(0,0,0,0.06)", flexShrink: 0
      }}>
        <div style={{ display: "flex", gap: 4, overflowX: "auto", paddingBottom: 12 }}>
          {([
            { t: "users" as Tab, l: "👥 Польз." },
            { t: "analytics" as Tab, l: "📈 Аналит." },
            { t: "broadcast" as Tab, l: "📢 Рассылка" },
            { t: "log" as Tab, l: "📜 Журнал" },
            { t: "more" as Tab, l: "⋯ Ещё" },
          ] as const).map(n => (
            <button key={n.t} onClick={() => setTab(n.t)} style={{
              flexShrink: 0, border: "none", borderRadius: 20, padding: "8px 16px",
              fontSize: 13, fontWeight: 700, cursor: "pointer",
              background: tab===n.t ? "#6366f1" : "rgba(99,102,241,0.08)",
              color: tab===n.t ? "#fff" : "#6366f1",
              transition: "all 0.18s", boxShadow: tab===n.t ? "0 3px 10px rgba(99,102,241,0.35)" : "none"
            }}>{n.l}</button>
          ))}
        </div>
      </div>

      {/* ── Content ── */}
      <div style={{ flex: 1, overflowY: "auto", padding: "14px 14px 24px" }}>

        {tab === "users" && (
          <>
            {/* Search */}
            <div style={{
              display: "flex", alignItems: "center", gap: 8,
              background: "#fff", borderRadius: 14, padding: "10px 14px",
              marginBottom: 10, boxShadow: "0 1px 6px rgba(0,0,0,0.06)"
            }}>
              <span style={{ fontSize: 16, opacity: 0.4 }}>🔍</span>
              <input
                value={search} onChange={e => setSearch(e.target.value)}
                placeholder="Поиск…"
                style={{ flex: 1, border: "none", outline: "none", background: "none", fontSize: 14, color: "#111" }}
              />
            </div>
            {/* Segment */}
            <div style={{ display: "flex", background: "rgba(99,102,241,0.08)", borderRadius: 12, padding: 3, marginBottom: 12 }}>
              {["Все","Активные","Заблок."].map((f,i) => (
                <button key={f} style={{
                  flex: 1, border: "none", borderRadius: 9, padding: "7px 0",
                  fontSize: 12, fontWeight: 700, cursor: "pointer",
                  background: i===0 ? "#fff" : "transparent",
                  color: i===0 ? "#6366f1" : "#8b8da0",
                  boxShadow: i===0 ? "0 1px 4px rgba(0,0,0,0.1)" : "none"
                }}>{f}</button>
              ))}
            </div>
            <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
              {filtered.map(u => (
                <div key={u.id} style={{
                  background: "#fff", borderRadius: 18, padding: "14px 14px",
                  boxShadow: "0 2px 8px rgba(0,0,0,0.06)",
                  borderLeft: `4px solid ${u.blocked ? "#ef4444" : u.sub ? "#f59e0b" : colorFor(u.name)}`
                }}>
                  <div style={{ display: "flex", gap: 12, alignItems: "center" }}>
                    <div style={{
                      width: 46, height: 46, borderRadius: "50%",
                      background: colorFor(u.name), display: "flex", alignItems: "center",
                      justifyContent: "center", fontSize: 17, fontWeight: 800,
                      color: "#fff", flexShrink: 0
                    }}>{initials(u.name)}</div>
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <div style={{ fontSize: 14, fontWeight: 800, marginBottom: 2 }}>{u.name}</div>
                      <div style={{ display: "flex", gap: 5, alignItems: "center", flexWrap: "wrap" }}>
                        <span style={{ fontSize: 11.5, color: "#8b8da0" }}>@{u.username}</span>
                        {u.sub && <span style={{ fontSize: 10, background: "rgba(245,158,11,0.15)", color: "#d97706", borderRadius: 6, padding: "1px 7px", fontWeight: 800 }}>⭐ PRO</span>}
                        {u.blocked && <span style={{ fontSize: 10, background: "rgba(239,68,68,0.12)", color: "#ef4444", borderRadius: 6, padding: "1px 7px", fontWeight: 800 }}>🚫 Заблок.</span>}
                      </div>
                    </div>
                    <div style={{ textAlign: "right" }}>
                      <div style={{ fontSize: 18, fontWeight: 900, color: "#6366f1" }}>{u.msgs.toLocaleString("ru-RU")}</div>
                      <div style={{ fontSize: 10, color: "#aaa" }}>сообщ.</div>
                      <div style={{ fontSize: 10, color: "#bbb", marginTop: 2 }}>· {u.active}</div>
                    </div>
                  </div>
                  {/* Stats row */}
                  <div style={{ display: "grid", gridTemplateColumns: "repeat(3,1fr)", gap: 6, marginTop: 10 }}>
                    {[
                      { v: u.chats, l: "Чатов", c: "#06b6d4", bg: "#e0f7fa" },
                      { v: u.coins.toLocaleString("ru-RU"), l: "🪙 Монеты", c: "#8b5cf6", bg: "#f3e8ff" },
                      { v: u.msgs, l: "Сообщ.", c: "#6366f1", bg: "#eef2ff" },
                    ].map((s,i) => (
                      <div key={i} style={{ background: s.bg, borderRadius: 10, padding: "7px 6px", textAlign: "center" }}>
                        <div style={{ fontSize: 14, fontWeight: 900, color: s.c }}>{s.v}</div>
                        <div style={{ fontSize: 9.5, color: "#888", marginTop: 2 }}>{s.l}</div>
                      </div>
                    ))}
                  </div>
                  {/* Actions */}
                  <div style={{ display: "flex", gap: 6, marginTop: 10 }}>
                    <button style={{ flex: 1, background: "#eef2ff", color: "#6366f1", border: "none", borderRadius: 10, padding: "8px 6px", fontSize: 11, fontWeight: 800, cursor: "pointer" }}>💬 Чаты</button>
                    <button style={{ flex: 1, background: "#fef2f2", color: "#ef4444", border: "none", borderRadius: 10, padding: "8px 6px", fontSize: 11, fontWeight: 800, cursor: "pointer" }}>🚫 Блок.</button>
                    <button style={{ flex: 1, background: "#f3e8ff", color: "#8b5cf6", border: "none", borderRadius: 10, padding: "8px 6px", fontSize: 11, fontWeight: 800, cursor: "pointer" }}>🪙 +/-</button>
                    <button style={{ flex: 1, background: "#fff7ed", color: "#f59e0b", border: "none", borderRadius: 10, padding: "8px 6px", fontSize: 11, fontWeight: 800, cursor: "pointer" }}>✉️ Написать</button>
                  </div>
                </div>
              ))}
            </div>
          </>
        )}

        {tab === "analytics" && (
          <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
            {/* Chart */}
            <div style={{ background: "#fff", borderRadius: 18, padding: 16, boxShadow: "0 2px 8px rgba(0,0,0,0.06)" }}>
              <div style={{ fontSize: 14, fontWeight: 800, marginBottom: 12 }}>📈 Активность за 30 дней</div>
              <div style={{ display: "flex", alignItems: "flex-end", gap: 2, height: 80 }}>
                {BARS.map((v,i) => {
                  const max = Math.max(...BARS);
                  return <div key={i} style={{ flex: 1, borderRadius: "3px 3px 0 0", background: i>=25 ? "linear-gradient(to top,#6366f1,#8b5cf6)" : "rgba(99,102,241,0.25)", height: `${v/max*100}%`, minHeight: 2 }}/>;
                })}
              </div>
              <div style={{ display: "flex", justifyContent: "space-between", marginTop: 6 }}>
                <span style={{ fontSize: 10, color: "#aaa" }}>20 июн</span>
                <span style={{ fontSize: 10, color: "#aaa" }}>20 июл</span>
              </div>
            </div>
            {/* Metrics */}
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8 }}>
              {[
                { ico: "📈", v: "+14%", l: "Рост / неделя", bg: "#f0fdf4", vc: "#16a34a" },
                { ico: "⚡", v: "73/100", l: "Целостность", bg: "#fffbeb", vc: "#d97706" },
                { ico: "💬", v: "1 812", l: "Сообщ. сегодня", bg: "#eef2ff", vc: "#6366f1" },
                { ico: "🆕", v: "+12", l: "Новых сегодня", bg: "#faf5ff", vc: "#7c3aed" },
              ].map((m,i) => (
                <div key={i} style={{ background: m.bg, borderRadius: 16, padding: 14, boxShadow: "0 1px 4px rgba(0,0,0,0.04)" }}>
                  <div style={{ fontSize: 22, marginBottom: 8 }}>{m.ico}</div>
                  <div style={{ fontSize: 22, fontWeight: 900, color: m.vc, letterSpacing: -0.5 }}>{m.v}</div>
                  <div style={{ fontSize: 11, color: "#888", marginTop: 3 }}>{m.l}</div>
                </div>
              ))}
            </div>
            {/* Media */}
            <div style={{ background: "#fff", borderRadius: 18, padding: 16, boxShadow: "0 2px 8px rgba(0,0,0,0.06)" }}>
              <div style={{ fontSize: 14, fontWeight: 800, marginBottom: 12 }}>📎 Медиа</div>
              {[
                { ico: "🖼", l: "Фото", v: 4231, pct: 100 },
                { ico: "🎙", l: "Голос", v: 1872, pct: 44 },
                { ico: "🎬", l: "Видео", v: 891, pct: 21 },
                { ico: "🎯", l: "Стикеры", v: 654, pct: 15 },
              ].map((m,i) => (
                <div key={i} style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 10 }}>
                  <span style={{ fontSize: 16, width: 24, textAlign: "center" }}>{m.ico}</span>
                  <span style={{ fontSize: 12, color: "#888", width: 58 }}>{m.l}</span>
                  <div style={{ flex: 1, height: 6, background: "#f0f2f8", borderRadius: 3, overflow: "hidden" }}>
                    <div style={{ width: `${m.pct}%`, height: "100%", background: "linear-gradient(90deg,#6366f1,#8b5cf6)", borderRadius: 3 }}/>
                  </div>
                  <span style={{ fontSize: 12, fontWeight: 800, color: "#6366f1", width: 40, textAlign: "right" }}>{m.v.toLocaleString("ru-RU")}</span>
                </div>
              ))}
            </div>
          </div>
        )}

        {tab === "broadcast" && (
          <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
            <div style={{ background: "#fff", borderRadius: 18, padding: 18, boxShadow: "0 2px 8px rgba(0,0,0,0.06)" }}>
              <div style={{ fontSize: 14, fontWeight: 800, marginBottom: 12 }}>📢 Рассылка</div>
              <textarea placeholder="Введите текст рассылки…" style={{
                width: "100%", minHeight: 120, background: "#f7f8fc",
                border: "1.5px solid rgba(99,102,241,0.2)", borderRadius: 12, padding: 12,
                fontSize: 14, resize: "none", outline: "none", color: "#111", boxSizing: "border-box"
              }}/>
              <div style={{ display: "flex", gap: 8, marginTop: 10 }}>
                <div style={{ flex: 1, background: "#eef2ff", borderRadius: 12, padding: "10px 14px", display: "flex", gap: 8, alignItems: "center" }}>
                  <span style={{ fontSize: 18 }}>👥</span>
                  <div>
                    <div style={{ fontSize: 16, fontWeight: 900, color: "#6366f1" }}>634</div>
                    <div style={{ fontSize: 10, color: "#8b8da0" }}>получат</div>
                  </div>
                </div>
                <button style={{
                  flex: 2, background: "linear-gradient(135deg,#6366f1,#8b5cf6)", color: "#fff",
                  border: "none", borderRadius: 12, fontSize: 15, fontWeight: 800, cursor: "pointer",
                  boxShadow: "0 4px 14px rgba(99,102,241,0.4)"
                }}>Отправить 🚀</button>
              </div>
            </div>
          </div>
        )}

        {tab === "log" && (
          <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            {[
              { ico: "🚫", text: "Заблокирован @ivan_sid", time: "Сегодня, 14:22", c: "#ef4444", bg: "#fef2f2" },
              { ico: "⭐", text: "Premium выдан @alex_m", time: "Сегодня, 12:08", c: "#d97706", bg: "#fffbeb" },
              { ico: "🪙", text: "Начислено 500 монет @marina_s", time: "Вчера, 18:44", c: "#7c3aed", bg: "#faf5ff" },
              { ico: "📢", text: "Рассылка — 634 польз.", time: "Вчера, 10:00", c: "#6366f1", bg: "#eef2ff" },
              { ico: "✅", text: "Разблокирован @olga_p", time: "2 дня назад", c: "#16a34a", bg: "#f0fdf4" },
            ].map((l,i) => (
              <div key={i} style={{ display: "flex", gap: 12, alignItems: "center", background: "#fff", borderRadius: 16, padding: "12px 14px", boxShadow: "0 1px 6px rgba(0,0,0,0.05)" }}>
                <div style={{ width: 38, height: 38, borderRadius: 12, background: l.bg, display: "flex", alignItems: "center", justifyContent: "center", fontSize: 18, flexShrink: 0 }}>{l.ico}</div>
                <div style={{ flex: 1 }}>
                  <div style={{ fontSize: 13, fontWeight: 700 }}>{l.text}</div>
                  <div style={{ fontSize: 11, color: "#aaa", marginTop: 2 }}>{l.time}</div>
                </div>
                <div style={{ width: 6, height: 6, borderRadius: "50%", background: l.c }}/>
              </div>
            ))}
          </div>
        )}

        {tab === "more" && (
          <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            {[
              { ico: "⭐", l: "Подписки", sub: "213 активных PRO", c: "#d97706", bg: "#fffbeb" },
              { ico: "🛒", l: "Магазин", sub: "Товары и бустеры", c: "#6366f1", bg: "#eef2ff" },
              { ico: "🔗", l: "Рефералы", sub: "Программа приглашений", c: "#16a34a", bg: "#f0fdf4" },
              { ico: "📊", l: "Инфографика", sub: "Скачать PNG-отчёт", c: "#7c3aed", bg: "#faf5ff" },
            ].map((m,i) => (
              <div key={i} style={{ display: "flex", gap: 14, alignItems: "center", background: "#fff", borderRadius: 18, padding: "16px 16px", boxShadow: "0 2px 8px rgba(0,0,0,0.06)", cursor: "pointer" }}>
                <div style={{ width: 48, height: 48, borderRadius: 14, background: m.bg, display: "flex", alignItems: "center", justifyContent: "center", fontSize: 24, flexShrink: 0 }}>{m.ico}</div>
                <div style={{ flex: 1 }}>
                  <div style={{ fontSize: 15, fontWeight: 800 }}>{m.l}</div>
                  <div style={{ fontSize: 12, color: "#aaa", marginTop: 2 }}>{m.sub}</div>
                </div>
                <svg width="8" height="14" viewBox="0 0 8 14" fill="none"><path d="M1 1l6 6-6 6" stroke="#d1d5db" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/></svg>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
