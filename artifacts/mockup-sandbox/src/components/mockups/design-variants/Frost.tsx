import "./design.css";

const stats = [
  { label: "Сообщений", value: "2 841", sub: "+12% за неделю", icon: "💬", trend: "up" },
  { label: "Активных чатов", value: "47", sub: "3 новых сегодня", icon: "🔥", trend: "up" },
  { label: "Реакций", value: "1 204", sub: "среднее 25.6/чат", icon: "❤️", trend: "up" },
  { label: "Монет", value: "3 550 ⭐", sub: "+80 за стрик", icon: "🪙", trend: "neutral" },
];

const streakDays = [true, true, true, true, false, false, false];
const todayIdx = 4;

export function Frost() {
  return (
    <div style={{
      minHeight: "100vh",
      background: "linear-gradient(180deg, #070d1a 0%, #0a1428 60%, #060c18 100%)",
      color: "#e8f0ff",
      fontFamily: "'Inter', system-ui, sans-serif",
      padding: "0 0 80px",
      userSelect: "none",
    }}>
      {/* Header */}
      <div style={{
        padding: "52px 20px 0",
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
      }}>
        <div>
          <div style={{ fontSize: 12, color: "rgba(160,185,255,0.55)", fontWeight: 500, letterSpacing: 1.2, textTransform: "uppercase" }}>Telegram Mini App</div>
          <div style={{ fontSize: 22, fontWeight: 700, marginTop: 3, color: "#c8daff" }}>Добрый вечер, Алекс</div>
        </div>
        <div style={{
          width: 40, height: 40, borderRadius: "50%",
          background: "linear-gradient(135deg, #3b82f6, #6366f1)",
          display: "flex", alignItems: "center", justifyContent: "center",
          fontSize: 18, boxShadow: "0 0 16px rgba(99,102,241,0.4)"
        }}>А</div>
      </div>

      {/* Hero streak card */}
      <div style={{
        margin: "20px 16px 0",
        background: "linear-gradient(135deg, rgba(59,130,246,0.15) 0%, rgba(99,102,241,0.1) 100%)",
        border: "1px solid rgba(99,130,255,0.2)",
        borderRadius: 20,
        padding: "18px 18px 20px",
        backdropFilter: "blur(20px)",
        position: "relative",
        overflow: "hidden",
      }}>
        {/* ice shimmer */}
        <div style={{
          position: "absolute", top: -40, right: -40,
          width: 160, height: 160,
          borderRadius: "50%",
          background: "radial-gradient(circle, rgba(147,197,253,0.12) 0%, transparent 70%)",
          pointerEvents: "none",
        }} />
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 14 }}>
          <div>
            <div style={{ fontSize: 11, color: "rgba(147,197,253,0.6)", letterSpacing: 1, textTransform: "uppercase", marginBottom: 4 }}>Стрик активности</div>
            <div style={{ display: "flex", alignItems: "baseline", gap: 6 }}>
              <span style={{ fontSize: 40, fontWeight: 800, color: "#93c5fd", lineHeight: 1 }}>4</span>
              <span style={{ fontSize: 14, color: "rgba(147,197,253,0.6)", fontWeight: 500 }}>дня подряд</span>
            </div>
          </div>
          <div style={{
            width: 56, height: 56, borderRadius: "50%",
            background: "rgba(59,130,246,0.2)",
            border: "1.5px solid rgba(99,155,255,0.3)",
            display: "flex", alignItems: "center", justifyContent: "center", fontSize: 26,
          }}>❄️</div>
        </div>
        {/* Week dots */}
        <div style={{ display: "flex", gap: 6, justifyContent: "space-between" }}>
          {["Пн","Вт","Ср","Чт","Пт","Сб","Вс"].map((d, i) => (
            <div key={i} style={{ flex: 1, display: "flex", flexDirection: "column", alignItems: "center", gap: 4 }}>
              <div style={{
                width: "100%", aspectRatio: "1",
                borderRadius: 8,
                background: i < todayIdx
                  ? "linear-gradient(135deg, #3b82f6, #6366f1)"
                  : i === todayIdx
                  ? "rgba(59,130,246,0.2)"
                  : "rgba(255,255,255,0.05)",
                border: i === todayIdx ? "1.5px solid rgba(99,155,255,0.5)" : "1.5px solid transparent",
                display: "flex", alignItems: "center", justifyContent: "center",
                fontSize: 12,
                boxShadow: i < todayIdx ? "0 2px 8px rgba(59,130,246,0.3)" : "none",
              }}>
                {i < todayIdx ? "✓" : ""}
              </div>
              <div style={{ fontSize: 9, color: "rgba(147,197,253,0.45)", fontWeight: 600 }}>{d}</div>
            </div>
          ))}
        </div>
        {/* XP bar */}
        <div style={{ marginTop: 14, background: "rgba(255,255,255,0.06)", borderRadius: 999, height: 4, overflow: "hidden" }}>
          <div style={{ width: "57%", height: "100%", background: "linear-gradient(90deg, #3b82f6, #818cf8)", borderRadius: 999 }} />
        </div>
        <div style={{ marginTop: 6, display: "flex", justifyContent: "space-between" }}>
          <span style={{ fontSize: 10, color: "rgba(147,197,253,0.4)" }}>1 140 / 2 000 XP до следующего уровня</span>
          <span style={{ fontSize: 10, color: "#60a5fa", fontWeight: 600 }}>57%</span>
        </div>
      </div>

      {/* Stats grid */}
      <div style={{
        margin: "14px 16px 0",
        display: "grid",
        gridTemplateColumns: "1fr 1fr",
        gap: 10,
      }}>
        {stats.map((s, i) => (
          <div key={i} style={{
            background: "rgba(255,255,255,0.04)",
            border: "1px solid rgba(100,130,255,0.12)",
            borderRadius: 16,
            padding: "14px 14px",
            backdropFilter: "blur(10px)",
            position: "relative",
            overflow: "hidden",
          }}>
            <div style={{
              position: "absolute", top: 0, left: 0, right: 0, height: 2,
              background: i === 0 ? "linear-gradient(90deg,#3b82f6,#6366f1)"
                        : i === 1 ? "linear-gradient(90deg,#22d3ee,#3b82f6)"
                        : i === 2 ? "linear-gradient(90deg,#ec4899,#a855f7)"
                        : "linear-gradient(90deg,#f59e0b,#eab308)",
              opacity: 0.7,
            }} />
            <div style={{ fontSize: 20, marginBottom: 6 }}>{s.icon}</div>
            <div style={{ fontSize: 20, fontWeight: 800, color: "#e8f0ff", letterSpacing: -0.5 }}>{s.value}</div>
            <div style={{ fontSize: 10.5, color: "rgba(160,185,255,0.5)", marginTop: 2 }}>{s.label}</div>
            <div style={{
              marginTop: 6, fontSize: 9.5, fontWeight: 600,
              color: s.trend === "up" ? "#34d399" : "rgba(160,185,255,0.4)",
            }}>{s.sub}</div>
          </div>
        ))}
      </div>

      {/* Subscription banner */}
      <div style={{
        margin: "14px 16px 0",
        background: "linear-gradient(135deg, rgba(99,102,241,0.2), rgba(59,130,246,0.12))",
        border: "1px solid rgba(99,102,241,0.25)",
        borderRadius: 16,
        padding: "14px 16px",
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
      }}>
        <div>
          <div style={{ fontSize: 12, fontWeight: 700, color: "#a5b4fc" }}>⭐ Premium активен</div>
          <div style={{ fontSize: 10.5, color: "rgba(160,185,255,0.5)", marginTop: 2 }}>Осталось 14 дней · ×1.5 XP</div>
        </div>
        <div style={{
          background: "linear-gradient(135deg,#3b82f6,#6366f1)",
          borderRadius: 10,
          padding: "7px 14px",
          fontSize: 11, fontWeight: 700, color: "#fff",
        }}>Продлить</div>
      </div>

      {/* Bottom nav */}
      <div style={{
        position: "fixed", bottom: 0, left: 0, right: 0,
        background: "rgba(7,13,26,0.95)",
        backdropFilter: "blur(24px)",
        borderTop: "1px solid rgba(99,130,255,0.12)",
        display: "flex",
        padding: "8px 0 20px",
      }}>
        {[
          { icon: "📊", label: "Статы", active: true },
          { icon: "🎰", label: "Казино" },
          { icon: "🏆", label: "Задания" },
          { icon: "💎", label: "Поддержать" },
          { icon: "🛒", label: "Магазин" },
        ].map((tab, i) => (
          <div key={i} style={{
            flex: 1, display: "flex", flexDirection: "column",
            alignItems: "center", gap: 3,
          }}>
            <div style={{
              fontSize: 20,
              filter: tab.active ? "drop-shadow(0 0 8px rgba(99,102,241,0.8))" : "none",
              transform: tab.active ? "scale(1.1)" : "scale(1)",
            }}>{tab.icon}</div>
            <div style={{
              fontSize: 9, fontWeight: 600,
              color: tab.active ? "#93c5fd" : "rgba(147,197,253,0.3)",
            }}>{tab.label}</div>
          </div>
        ))}
      </div>
    </div>
  );
}
