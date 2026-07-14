import "./design.css";

const stats = [
  { label: "Сообщений", value: "2 841", sub: "+12% за неделю", icon: "💬", trend: "up" },
  { label: "Активных чатов", value: "47", sub: "3 новых сегодня", icon: "🔥", trend: "up" },
  { label: "Реакций", value: "1 204", sub: "среднее 25.6/чат", icon: "❤️", trend: "up" },
  { label: "Монет", value: "3 550 ⭐", sub: "+80 за стрик", icon: "🪙", trend: "neutral" },
];

export function Ember() {
  return (
    <div style={{
      minHeight: "100vh",
      background: "#0e0a07",
      color: "#f5e8d0",
      fontFamily: "'Inter', system-ui, sans-serif",
      padding: "0 0 80px",
      userSelect: "none",
    }}>
      {/* warm noise overlay */}
      <div style={{
        position: "fixed", inset: 0, pointerEvents: "none", zIndex: 0,
        background: "radial-gradient(ellipse 120% 80% at 60% -10%, rgba(251,146,60,0.12) 0%, transparent 60%)",
      }} />

      {/* Header */}
      <div style={{ position: "relative", zIndex: 1, padding: "52px 20px 0", display: "flex", alignItems: "center", justifyContent: "space-between" }}>
        <div>
          <div style={{ fontSize: 11, color: "rgba(251,146,60,0.5)", fontWeight: 600, letterSpacing: 1.2, textTransform: "uppercase" }}>Telegram Mini App</div>
          <div style={{ fontSize: 22, fontWeight: 700, marginTop: 3 }}>Добрый вечер, Алекс 👋</div>
        </div>
        <div style={{
          width: 40, height: 40, borderRadius: "50%",
          background: "linear-gradient(135deg, #f97316, #ef4444)",
          display: "flex", alignItems: "center", justifyContent: "center",
          fontSize: 17, fontWeight: 700, color: "#fff",
          boxShadow: "0 0 20px rgba(249,115,22,0.5)",
        }}>А</div>
      </div>

      {/* Hero — streak card */}
      <div style={{
        position: "relative", zIndex: 1,
        margin: "20px 16px 0",
        background: "linear-gradient(135deg, #1c1007 0%, #231509 100%)",
        border: "1px solid rgba(251,146,60,0.2)",
        borderRadius: 22,
        padding: "20px 18px 22px",
        overflow: "hidden",
      }}>
        {/* ember glow blob */}
        <div style={{
          position: "absolute", right: -30, top: -30,
          width: 180, height: 180, borderRadius: "50%",
          background: "radial-gradient(circle, rgba(251,146,60,0.25) 0%, transparent 65%)",
          pointerEvents: "none",
        }} />
        {/* top accent line */}
        <div style={{
          position: "absolute", top: 0, left: 0, right: 0, height: 3,
          background: "linear-gradient(90deg, #f97316, #ef4444, #f59e0b)",
          borderRadius: "22px 22px 0 0",
        }} />

        <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", marginBottom: 16 }}>
          <div>
            <div style={{ fontSize: 11, color: "rgba(251,146,60,0.5)", letterSpacing: 1, textTransform: "uppercase", fontWeight: 600, marginBottom: 4 }}>Стрик</div>
            <div style={{ display: "flex", alignItems: "baseline", gap: 6 }}>
              <span style={{ fontSize: 52, fontWeight: 900, color: "#fb923c", lineHeight: 1, fontVariantNumeric: "tabular-nums" }}>4</span>
              <span style={{ fontSize: 15, color: "rgba(245,232,208,0.5)", fontWeight: 500 }}>дня</span>
            </div>
            <div style={{ fontSize: 11, color: "rgba(251,146,60,0.55)", marginTop: 2 }}>Не прерывай серию! 🔥</div>
          </div>
          <div style={{
            background: "rgba(249,115,22,0.15)",
            border: "1px solid rgba(249,115,22,0.3)",
            borderRadius: 14, padding: "8px 12px",
            fontSize: 10, fontWeight: 700, color: "#fb923c",
            textAlign: "center",
          }}>
            <div style={{ fontSize: 18 }}>🏅</div>
            <div>Уровень 3</div>
          </div>
        </div>

        {/* Week */}
        <div style={{ display: "flex", gap: 6 }}>
          {["Пн","Вт","Ср","Чт","Пт","Сб","Вс"].map((d, i) => {
            const done = i < 4;
            const today = i === 4;
            return (
              <div key={i} style={{ flex: 1, textAlign: "center" }}>
                <div style={{
                  width: "100%", aspectRatio: "1", borderRadius: 10,
                  background: done
                    ? "linear-gradient(135deg, #f97316, #ef4444)"
                    : today ? "rgba(249,115,22,0.12)" : "rgba(255,255,255,0.04)",
                  border: today ? "1.5px solid rgba(249,115,22,0.4)" : "1.5px solid transparent",
                  display: "flex", alignItems: "center", justifyContent: "center",
                  fontSize: 11, color: done ? "#fff" : "transparent",
                  boxShadow: done ? "0 2px 10px rgba(249,115,22,0.35)" : "none",
                  marginBottom: 4,
                }}>
                  {done ? "✓" : ""}
                </div>
                <div style={{ fontSize: 9, color: "rgba(245,232,208,0.3)", fontWeight: 600 }}>{d}</div>
              </div>
            );
          })}
        </div>

        {/* XP */}
        <div style={{ marginTop: 14 }}>
          <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 5 }}>
            <span style={{ fontSize: 10, color: "rgba(251,146,60,0.5)" }}>1 140 / 2 000 XP</span>
            <span style={{ fontSize: 10, fontWeight: 700, color: "#f97316" }}>57%</span>
          </div>
          <div style={{ height: 5, background: "rgba(255,255,255,0.07)", borderRadius: 999, overflow: "hidden" }}>
            <div style={{ width: "57%", height: "100%", background: "linear-gradient(90deg, #f97316, #ef4444)", borderRadius: 999, boxShadow: "0 0 8px rgba(249,115,22,0.6)" }} />
          </div>
        </div>
      </div>

      {/* Stats grid */}
      <div style={{ margin: "14px 16px 0", display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10, position: "relative", zIndex: 1 }}>
        {stats.map((s, i) => {
          const accents = [
            ["#f97316","#ef4444"],
            ["#f59e0b","#f97316"],
            ["#ef4444","#ec4899"],
            ["#eab308","#f59e0b"],
          ];
          return (
            <div key={i} style={{
              background: "rgba(255,255,255,0.03)",
              border: "1px solid rgba(251,146,60,0.1)",
              borderRadius: 16,
              padding: "14px",
              overflow: "hidden",
              position: "relative",
            }}>
              <div style={{
                position: "absolute", bottom: -20, right: -20,
                width: 70, height: 70, borderRadius: "50%",
                background: `radial-gradient(circle, ${accents[i][0]}22, transparent)`,
              }} />
              <div style={{ fontSize: 22, marginBottom: 6 }}>{s.icon}</div>
              <div style={{ fontSize: 21, fontWeight: 800, color: "#f5e8d0", letterSpacing: -0.5 }}>{s.value}</div>
              <div style={{ fontSize: 10.5, color: "rgba(245,232,208,0.4)", marginTop: 2 }}>{s.label}</div>
              <div style={{
                marginTop: 7, display: "inline-flex", alignItems: "center", gap: 3,
                background: `linear-gradient(135deg, ${accents[i][0]}22, ${accents[i][1]}11)`,
                border: `1px solid ${accents[i][0]}33`,
                borderRadius: 6, padding: "2px 7px",
                fontSize: 9.5, fontWeight: 700,
                color: accents[i][0],
              }}>{s.trend === "up" ? "↑" : "→"} {s.sub}</div>
            </div>
          );
        })}
      </div>

      {/* Banner */}
      <div style={{
        position: "relative", zIndex: 1,
        margin: "14px 16px 0",
        background: "linear-gradient(135deg, rgba(249,115,22,0.18), rgba(239,68,68,0.12))",
        border: "1px solid rgba(249,115,22,0.22)",
        borderRadius: 16,
        padding: "14px 16px",
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
      }}>
        <div>
          <div style={{ fontSize: 12, fontWeight: 700, color: "#fb923c" }}>⭐ Premium активен</div>
          <div style={{ fontSize: 10.5, color: "rgba(245,232,208,0.45)", marginTop: 2 }}>Осталось 14 дней · ×1.5 XP</div>
        </div>
        <div style={{
          background: "linear-gradient(135deg, #f97316, #ef4444)",
          borderRadius: 10, padding: "7px 14px",
          fontSize: 11, fontWeight: 700, color: "#fff",
          boxShadow: "0 4px 12px rgba(249,115,22,0.4)",
        }}>Продлить</div>
      </div>

      {/* Bottom nav */}
      <div style={{
        position: "fixed", bottom: 0, left: 0, right: 0,
        background: "rgba(14,10,7,0.96)",
        backdropFilter: "blur(20px)",
        borderTop: "1px solid rgba(251,146,60,0.1)",
        display: "flex",
        padding: "8px 0 20px",
        zIndex: 100,
      }}>
        {[
          { icon: "📊", label: "Статы", active: true },
          { icon: "🎰", label: "Казино" },
          { icon: "🏆", label: "Задания" },
          { icon: "💎", label: "Поддержать" },
          { icon: "🛒", label: "Магазин" },
        ].map((tab, i) => (
          <div key={i} style={{ flex: 1, display: "flex", flexDirection: "column", alignItems: "center", gap: 3 }}>
            <div style={{
              fontSize: 20,
              filter: tab.active ? "drop-shadow(0 0 8px rgba(249,115,22,0.9))" : "none",
              transform: tab.active ? "scale(1.12)" : "scale(1)",
            }}>{tab.icon}</div>
            <div style={{
              fontSize: 9, fontWeight: 600,
              color: tab.active ? "#fb923c" : "rgba(245,232,208,0.25)",
            }}>{tab.label}</div>
          </div>
        ))}
      </div>
    </div>
  );
}
