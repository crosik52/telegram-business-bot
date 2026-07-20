import { useState } from "react";

const COLORS = ["#FF6B6B","#4ECDC4","#45AAF2","#F7B731","#A55EEA","#26DE81","#FD9644","#2BCBBA"];
function colorFor(s: string) { let h=0; for(let i=0;i<s.length;i++) h=(h*31+s.charCodeAt(i))>>>0; return COLORS[h%COLORS.length]; }
function initials(n: string) { const p=n.trim().split(/\s+/); return p.length===1?p[0][0].toUpperCase():(p[0][0]+p[1][0]).toUpperCase(); }

const USERS = [
  { id: 1, name: "Алексей Морозов", username: "alex_m", msgs: 1842, chats: 14, coins: 5200, sub: true, blocked: false, active: "2 мин назад" },
  { id: 2, name: "Марина Соколова", username: "marina_s", msgs: 976, chats: 8, coins: 1100, sub: false, blocked: false, active: "1 ч назад" },
  { id: 3, name: "Дмитрий Кузнецов", username: "dkuznetsov", msgs: 3421, chats: 22, coins: 8900, sub: true, blocked: false, active: "5 мин назад" },
  { id: 4, name: "Ольга Петрова", username: "olga_p", msgs: 234, chats: 3, coins: 0, sub: false, blocked: true, active: "5 дн назад" },
  { id: 5, name: "Иван Сидоров", username: "ivan_sid", msgs: 561, chats: 7, coins: 2300, sub: false, blocked: false, active: "40 мин назад" },
];

const CHART_DATA = [12,8,19,24,15,31,28,22,35,41,38,29,44,51,47,39,56,62,58,49,67,71,65,78,83,74,91,88,95,102];

type Tab = "users"|"analytics"|"broadcast"|"log"|"more";

export function DarkPro() {
  const [tab, setTab] = useState<Tab>("users");
  const [search, setSearch] = useState("");
  const [expanded, setExpanded] = useState<number|null>(null);

  const filtered = USERS.filter(u =>
    u.name.toLowerCase().includes(search.toLowerCase()) ||
    u.username.includes(search.toLowerCase())
  );

  const maxBar = Math.max(...CHART_DATA);

  return (
    <div style={{
      width: 390, minHeight: 844, background: "#0f0f14",
      color: "#fff", fontFamily: "-apple-system, BlinkMacSystemFont, 'SF Pro Display', sans-serif",
      display: "flex", flexDirection: "column", position: "relative", overflow: "hidden"
    }}>
      {/* ── Status bar ── */}
      <div style={{ height: 44, background: "#0f0f14", display: "flex", alignItems: "center", justifyContent: "space-between", padding: "0 20px", flexShrink: 0 }}>
        <span style={{ fontSize: 15, fontWeight: 600 }}>9:41</span>
        <div style={{ display: "flex", gap: 6, alignItems: "center" }}>
          <span style={{ fontSize: 12 }}>●●●</span>
          <span style={{ fontSize: 12 }}>WiFi</span>
          <span style={{ fontSize: 12 }}>🔋</span>
        </div>
      </div>

      {/* ── Header ── */}
      <div style={{
        padding: "12px 20px 16px",
        background: "linear-gradient(145deg, #1a1a2e 0%, #16213e 50%, #0f3460 100%)",
        flexShrink: 0
      }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: 16 }}>
          <div>
            <div style={{ fontSize: 11, color: "rgba(255,255,255,0.45)", fontWeight: 600, textTransform: "uppercase", letterSpacing: 1, marginBottom: 3 }}>Панель управления</div>
            <div style={{ fontSize: 22, fontWeight: 800, letterSpacing: -0.5 }}>Admin HQ 🛠</div>
          </div>
          <div style={{
            width: 40, height: 40, borderRadius: "50%",
            background: "linear-gradient(135deg, #6c5ce7, #a855f7)",
            display: "flex", alignItems: "center", justifyContent: "center",
            fontSize: 18, boxShadow: "0 4px 16px rgba(108,92,231,0.5)"
          }}>👤</div>
        </div>
        {/* KPI row */}
        <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 8 }}>
          {[
            { v: "847", l: "Польз.", c: "#45AAF2", ico: "👥" },
            { v: "634", l: "Активных", c: "#26DE81", ico: "✅" },
            { v: "12", l: "Заблок.", c: "#FF6B6B", ico: "🚫" },
            { v: "52K", l: "Сообщ.", c: "#F7B731", ico: "💬" },
          ].map((k,i) => (
            <div key={i} style={{
              background: "rgba(255,255,255,0.07)", borderRadius: 14,
              padding: "10px 8px", textAlign: "center",
              border: "1px solid rgba(255,255,255,0.06)"
            }}>
              <div style={{ fontSize: 16, marginBottom: 3 }}>{k.ico}</div>
              <div style={{ fontSize: 18, fontWeight: 900, color: k.c, lineHeight: 1, letterSpacing: -0.5 }}>{k.v}</div>
              <div style={{ fontSize: 9, color: "rgba(255,255,255,0.4)", marginTop: 3, fontWeight: 600 }}>{k.l}</div>
            </div>
          ))}
        </div>
      </div>

      {/* ── Content ── */}
      <div style={{ flex: 1, overflowY: "auto", padding: "14px 16px 80px" }}>

        {tab === "users" && (
          <>
            {/* Search */}
            <div style={{
              display: "flex", alignItems: "center", gap: 10,
              background: "rgba(255,255,255,0.07)", borderRadius: 14,
              padding: "10px 14px", marginBottom: 10,
              border: "1px solid rgba(255,255,255,0.06)"
            }}>
              <span style={{ fontSize: 16, opacity: 0.5 }}>🔍</span>
              <input
                value={search} onChange={e => setSearch(e.target.value)}
                placeholder="Поиск пользователя…"
                style={{ flex: 1, background: "none", border: "none", outline: "none", color: "#fff", fontSize: 14 }}
              />
            </div>
            {/* Filter chips */}
            <div style={{ display: "flex", gap: 6, marginBottom: 14, overflowX: "auto" }}>
              {["Все","Активные","Заблок.","Без увед."].map((f,i) => (
                <button key={f} style={{
                  flexShrink: 0, border: "none", borderRadius: 20, padding: "6px 14px",
                  fontSize: 12, fontWeight: 700, cursor: "pointer",
                  background: i===0 ? "#6c5ce7" : "rgba(255,255,255,0.07)",
                  color: i===0 ? "#fff" : "rgba(255,255,255,0.5)"
                }}>{f}</button>
              ))}
            </div>
            <div style={{ fontSize: 11, color: "rgba(255,255,255,0.35)", fontWeight: 700, textTransform: "uppercase", letterSpacing: 0.8, marginBottom: 10 }}>
              Пользователи · {filtered.length}
            </div>
            {/* User cards */}
            <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
              {filtered.map(u => (
                <div key={u.id} onClick={() => setExpanded(expanded === u.id ? null : u.id)}
                  style={{
                    background: "rgba(255,255,255,0.05)", borderRadius: 18,
                    padding: "14px 14px", cursor: "pointer",
                    border: expanded===u.id ? "1px solid rgba(108,92,231,0.5)" : "1px solid rgba(255,255,255,0.05)",
                    transition: "all 0.2s"
                  }}>
                  <div style={{ display: "flex", gap: 12, alignItems: "center" }}>
                    <div style={{
                      width: 44, height: 44, borderRadius: "50%",
                      background: colorFor(u.name), display: "flex", alignItems: "center",
                      justifyContent: "center", fontSize: 16, fontWeight: 800,
                      color: "#fff", flexShrink: 0, boxShadow: `0 0 0 2px ${u.blocked ? "#FF6B6B" : u.sub ? "#F7B731" : "rgba(255,255,255,0.08)"}`
                    }}>{initials(u.name)}</div>
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <div style={{ fontSize: 14, fontWeight: 700, marginBottom: 3, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{u.name}</div>
                      <div style={{ display: "flex", gap: 6, alignItems: "center" }}>
                        <span style={{ fontSize: 11, color: "rgba(255,255,255,0.4)" }}>@{u.username}</span>
                        {u.sub && <span style={{ fontSize: 10, background: "rgba(247,183,49,0.2)", color: "#F7B731", border: "1px solid rgba(247,183,49,0.3)", borderRadius: 6, padding: "1px 6px", fontWeight: 700 }}>⭐ PRO</span>}
                        {u.blocked && <span style={{ fontSize: 10, background: "rgba(255,107,107,0.2)", color: "#FF6B6B", border: "1px solid rgba(255,107,107,0.3)", borderRadius: 6, padding: "1px 6px", fontWeight: 700 }}>🚫</span>}
                      </div>
                    </div>
                    <div style={{ textAlign: "right" }}>
                      <div style={{ fontSize: 11, color: "rgba(255,255,255,0.3)", marginBottom: 2 }}>{u.active}</div>
                      <div style={{ fontSize: 16, fontWeight: 800, color: "#45AAF2" }}>{u.msgs.toLocaleString("ru-RU")}</div>
                      <div style={{ fontSize: 9, color: "rgba(255,255,255,0.3)", textTransform: "uppercase", letterSpacing: 0.3 }}>сообщ.</div>
                    </div>
                  </div>
                  {/* Stats row */}
                  <div style={{ display: "grid", gridTemplateColumns: "repeat(3,1fr)", gap: 6, marginTop: 12, padding: "10px 0 0", borderTop: "1px solid rgba(255,255,255,0.05)" }}>
                    {[
                      { v: u.chats, l: "Чатов", c: "#4ECDC4" },
                      { v: u.coins.toLocaleString("ru-RU"), l: "🪙 Монеты", c: "#A55EEA" },
                      { v: u.msgs, l: "Сообщ.", c: "#45AAF2" },
                    ].map((s,i) => (
                      <div key={i} style={{ textAlign: "center", background: "rgba(255,255,255,0.04)", borderRadius: 10, padding: "6px 4px" }}>
                        <div style={{ fontSize: 15, fontWeight: 800, color: s.c }}>{s.v}</div>
                        <div style={{ fontSize: 10, color: "rgba(255,255,255,0.3)", marginTop: 2 }}>{s.l}</div>
                      </div>
                    ))}
                  </div>
                  {expanded === u.id && (
                    <div style={{ display: "flex", gap: 8, marginTop: 10 }}>
                      <button style={{ flex: 1, background: "rgba(69,170,242,0.2)", color: "#45AAF2", border: "1px solid rgba(69,170,242,0.3)", borderRadius: 10, padding: "8px", fontSize: 12, fontWeight: 700, cursor: "pointer" }}>💬 Чаты</button>
                      <button style={{ flex: 1, background: "rgba(255,107,107,0.15)", color: "#FF6B6B", border: "1px solid rgba(255,107,107,0.25)", borderRadius: 10, padding: "8px", fontSize: 12, fontWeight: 700, cursor: "pointer" }}>🚫 Заблок.</button>
                      <button style={{ flex: 1, background: "rgba(165,94,234,0.15)", color: "#A55EEA", border: "1px solid rgba(165,94,234,0.25)", borderRadius: 10, padding: "8px", fontSize: 12, fontWeight: 700, cursor: "pointer" }}>🪙 Монеты</button>
                    </div>
                  )}
                </div>
              ))}
            </div>
          </>
        )}

        {tab === "analytics" && (
          <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
            <div style={{ fontSize: 11, color: "rgba(255,255,255,0.35)", fontWeight: 700, textTransform: "uppercase", letterSpacing: 0.8, marginBottom: 4 }}>Активность · 30 дней</div>
            {/* Bar chart */}
            <div style={{ background: "rgba(255,255,255,0.04)", borderRadius: 18, padding: "16px 14px", border: "1px solid rgba(255,255,255,0.06)" }}>
              <div style={{ display: "flex", alignItems: "flex-end", gap: 2, height: 80 }}>
                {CHART_DATA.map((v,i) => (
                  <div key={i} style={{
                    flex: 1, borderRadius: "3px 3px 0 0",
                    background: i>=25 ? "linear-gradient(to top,#6c5ce7,#a855f7)" : i>=20 ? "rgba(108,92,231,0.6)" : "rgba(108,92,231,0.25)",
                    height: `${v/maxBar*100}%`, minHeight: 2, transition: "height 0.3s"
                  }}/>
                ))}
              </div>
              <div style={{ display: "flex", justifyContent: "space-between", marginTop: 8 }}>
                <span style={{ fontSize: 10, color: "rgba(255,255,255,0.3)" }}>20 июн</span>
                <span style={{ fontSize: 10, color: "rgba(255,255,255,0.3)" }}>20 июл</span>
              </div>
            </div>
            {/* Metric cards */}
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8 }}>
              {[
                { ico: "📈", v: "+14%", l: "Рост за неделю", c: "#26DE81", bg: "rgba(38,222,129,0.1)" },
                { ico: "⚡", v: "73%", l: "Целостность", c: "#F7B731", bg: "rgba(247,183,49,0.1)" },
                { ico: "💬", v: "1.8K", l: "Сообщ. сегодня", c: "#45AAF2", bg: "rgba(69,170,242,0.1)" },
                { ico: "🆕", v: "+12", l: "Новых польз.", c: "#A55EEA", bg: "rgba(165,94,234,0.1)" },
              ].map((m,i) => (
                <div key={i} style={{ background: m.bg, borderRadius: 16, padding: "14px", border: `1px solid ${m.c}22` }}>
                  <div style={{ fontSize: 24, marginBottom: 6 }}>{m.ico}</div>
                  <div style={{ fontSize: 24, fontWeight: 900, color: m.c, letterSpacing: -0.5 }}>{m.v}</div>
                  <div style={{ fontSize: 11, color: "rgba(255,255,255,0.4)", marginTop: 3 }}>{m.l}</div>
                </div>
              ))}
            </div>
          </div>
        )}

        {tab === "broadcast" && (
          <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
            <div style={{ background: "rgba(255,255,255,0.04)", borderRadius: 18, padding: 16, border: "1px solid rgba(255,255,255,0.06)" }}>
              <div style={{ fontSize: 12, color: "rgba(255,255,255,0.4)", fontWeight: 700, marginBottom: 8 }}>📢 ТЕКСТ СООБЩЕНИЯ</div>
              <textarea placeholder="Введите текст рассылки…" style={{
                width: "100%", minHeight: 120, background: "rgba(255,255,255,0.05)",
                border: "1px solid rgba(255,255,255,0.1)", borderRadius: 12, padding: 12,
                color: "#fff", fontSize: 14, resize: "none", outline: "none", boxSizing: "border-box"
              }}/>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginTop: 10 }}>
                <span style={{ fontSize: 12, color: "rgba(255,255,255,0.4)" }}>Получат: <strong style={{ color: "#26DE81" }}>634 польз.</strong></span>
                <button style={{ background: "linear-gradient(135deg,#6c5ce7,#a855f7)", color: "#fff", border: "none", borderRadius: 12, padding: "10px 20px", fontSize: 14, fontWeight: 700, cursor: "pointer" }}>
                  Отправить 🚀
                </button>
              </div>
            </div>
          </div>
        )}

        {tab === "log" && (
          <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            <div style={{ fontSize: 11, color: "rgba(255,255,255,0.35)", fontWeight: 700, textTransform: "uppercase", letterSpacing: 0.8, marginBottom: 4 }}>Журнал действий</div>
            {[
              { ico: "🚫", text: "Заблокирован @ivan_sid", time: "сегодня 14:22", c: "#FF6B6B" },
              { ico: "⭐", text: "Premium выдан @alex_m", time: "сегодня 12:08", c: "#F7B731" },
              { ico: "🪙", text: "Начислено 500 монет @marina_s", time: "вчера 18:44", c: "#A55EEA" },
              { ico: "📢", text: "Рассылка: 634 получили", time: "вчера 10:00", c: "#45AAF2" },
              { ico: "✅", text: "Разблокирован @olga_p", time: "2 дня назад", c: "#26DE81" },
            ].map((l,i) => (
              <div key={i} style={{ display: "flex", gap: 12, alignItems: "center", background: "rgba(255,255,255,0.04)", borderRadius: 14, padding: "12px 14px", border: "1px solid rgba(255,255,255,0.05)" }}>
                <div style={{ width: 36, height: 36, borderRadius: "50%", background: l.c + "22", display: "flex", alignItems: "center", justifyContent: "center", fontSize: 16, flexShrink: 0 }}>{l.ico}</div>
                <div style={{ flex: 1 }}>
                  <div style={{ fontSize: 13, fontWeight: 600 }}>{l.text}</div>
                  <div style={{ fontSize: 11, color: "rgba(255,255,255,0.35)", marginTop: 2 }}>{l.time}</div>
                </div>
              </div>
            ))}
          </div>
        )}

        {tab === "more" && (
          <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            {[
              { ico: "⭐", l: "Подписки", sub: "PRO-аккаунты · 213 активных", c: "#F7B731" },
              { ico: "🛒", l: "Магазин", sub: "Товары и бустеры", c: "#45AAF2" },
              { ico: "🔗", l: "Рефералы", sub: "Программа приглашений", c: "#26DE81" },
              { ico: "📊", l: "Инфографика", sub: "Скачать PNG-отчёт", c: "#A55EEA" },
            ].map((m,i) => (
              <div key={i} style={{ display: "flex", gap: 14, alignItems: "center", background: "rgba(255,255,255,0.04)", borderRadius: 18, padding: "16px 16px", border: "1px solid rgba(255,255,255,0.06)", cursor: "pointer" }}>
                <div style={{ width: 44, height: 44, borderRadius: 14, background: m.c + "22", display: "flex", alignItems: "center", justifyContent: "center", fontSize: 22, flexShrink: 0 }}>{m.ico}</div>
                <div style={{ flex: 1 }}>
                  <div style={{ fontSize: 15, fontWeight: 700 }}>{m.l}</div>
                  <div style={{ fontSize: 12, color: "rgba(255,255,255,0.4)", marginTop: 2 }}>{m.sub}</div>
                </div>
                <span style={{ color: "rgba(255,255,255,0.2)", fontSize: 18 }}>›</span>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* ── Bottom nav ── */}
      <div style={{
        position: "absolute", bottom: 0, left: 0, right: 0,
        background: "rgba(15,15,20,0.95)", backdropFilter: "blur(20px)",
        borderTop: "1px solid rgba(255,255,255,0.07)",
        display: "flex", padding: "8px 0 24px"
      }}>
        {([
          { t: "users" as Tab, ico: "👥", l: "Польз." },
          { t: "analytics" as Tab, ico: "📈", l: "Аналит." },
          { t: "broadcast" as Tab, ico: "📢", l: "Рассылка" },
          { t: "log" as Tab, ico: "📜", l: "Журнал" },
          { t: "more" as Tab, ico: "⋯", l: "Ещё" },
        ] as const).map(n => (
          <button key={n.t} onClick={() => setTab(n.t)} style={{
            flex: 1, border: "none", background: "none", cursor: "pointer",
            display: "flex", flexDirection: "column", alignItems: "center", gap: 2, padding: "6px 0"
          }}>
            <div style={{
              fontSize: 22, lineHeight: 1,
              filter: tab===n.t ? "none" : "grayscale(1) opacity(0.35)",
              transform: tab===n.t ? "scale(1.15)" : "scale(1)", transition: "all 0.18s"
            }}>{n.ico}</div>
            <span style={{ fontSize: 10, fontWeight: 700, color: tab===n.t ? "#6c5ce7" : "rgba(255,255,255,0.3)" }}>{n.l}</span>
          </button>
        ))}
      </div>
    </div>
  );
}
