import "./design.css";

const stats = [
  { label: "Сообщений", value: "2 841", sub: "+12% за неделю", icon: "💬", trend: "up" },
  { label: "Активных чатов", value: "47", sub: "3 новых сегодня", icon: "🔥", trend: "up" },
  { label: "Реакций", value: "1 204", sub: "среднее 25.6/чат", icon: "❤️", trend: "up" },
  { label: "Монет", value: "3 550 ⭐", sub: "+80 за стрик", icon: "🪙", trend: "neutral" },
];

export function VioletDream() {
  return (
    <div style={{
      minHeight: "100vh",
      background: "#08040f",
      color: "#f0e8ff",
      fontFamily: "'Inter', system-ui, sans-serif",
      padding: "0 0 80px",
      userSelect: "none",
      position: "relative",
    }}>
      {/* Aurora background blobs */}
      <div style={{ position: "fixed", inset: 0, pointerEvents: "none", zIndex: 0, overflow: "hidden" }}>
        <div style={{
          position: "absolute", top: -120, left: -60, width: 350, height: 350,
          borderRadius: "50%",
          background: "radial-gradient(circle, rgba(139,92,246,0.22) 0%, transparent 65%)",
        }} />
        <div style={{
          position: "absolute", top: 80, right: -80, width: 280, height: 280,
          borderRadius: "50%",
          background: "radial-gradient(circle, rgba(236,72,153,0.16) 0%, transparent 65%)",
        }} />
        <div style={{
          position: "absolute", top: 400, left: "30%", width: 220, height: 220,
          borderRadius: "50%",
          background: "radial-gradient(circle, rgba(99,102,241,0.12) 0%, transparent 65%)",
        }} />
      </div>

      {/* Header */}
      <div style={{ position: "relative", zIndex: 1, padding: "52px 20px 0", display: "flex", alignItems: "center", justifyContent: "space-between" }}>
        <div>
          <div style={{ fontSize: 11, color: "rgba(196,181,253,0.45)", fontWeight: 600, letterSpacing: 1.2, textTransform: "uppercase" }}>Telegram Mini App</div>
          <div style={{ fontSize: 22, fontWeight: 700, marginTop: 3, background: "linear-gradient(90deg, #c4b5fd, #f0abfc)", WebkitBackgroundClip: "text", WebkitTextFillColor: "transparent" }}>Добрый вечер, Алекс</div>
        </div>
        <div style={{
          width: 40, height: 40, borderRadius: "50%",
          background: "linear-gradient(135deg, #8b5cf6, #ec4899)",
          display: "flex", alignItems: "center", justifyContent: "center",
          fontSize: 17, fontWeight: 700, color: "#fff",
          boxShadow: "0 0 20px rgba(139,92,246,0.55)",
        }}>А</div>
      </div>

      {/* Hero streak card */}
      <div style={{
        position: "relative", zIndex: 1,
        margin: "20px 16px 0",
        background: "linear-gradient(135deg, rgba(139,92,246,0.18) 0%, rgba(236,72,153,0.12) 100%)",
        border: "1px solid rgba(167,139,250,0.25)",
        borderRadius: 22, padding: "20px 18px 22px",
        overflow: "hidden",
      }}>
        <div style={{
          position: "absolute", top: 0, left: 0, right: 0, height: 3,
          background: "linear-gradient(90deg, #8b5cf6, #ec4899, #06b6d4)",
        }} />
        <div style={{
          position: "absolute", bottom: -50, right: -50,
          width: 200, height: 200, borderRadius: "50%",
          background: "radial-gradient(circle, rgba(236,72,153,0.2) 0%, transparent 60%)",
          pointerEvents: "none",
        }} />

        <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", marginBottom: 16 }}>
          <div>
            <div style={{ fontSize: 11, color: "rgba(196,181,253,0.5)", letterSpacing: 1, textTransform: "uppercase", fontWeight: 600, marginBottom: 4 }}>Стрик активности</div>
            <div style={{ display: "flex", alignItems: "baseline", gap: 6 }}>
              <span style={{
                fontSize: 52, fontWeight: 900, lineHeight: 1,
                background: "linear-gradient(135deg, #c4b5fd, #f0abfc)",
                WebkitBackgroundClip: "text", WebkitTextFillColor: "transparent",
              }}>4</span>
              <span style={{ fontSize: 15, color: "rgba(240,232,255,0.45)", fontWeight: 500 }}>дня подряд</span>
            </div>
          </div>
          <div style={{
            background: "linear-gradient(135deg, rgba(139,92,246,0.3), rgba(236,72,153,0.2))",
            border: "1.5px solid rgba(167,139,250,0.35)",
            borderRadius: 16, padding: "10px 12px",
            textAlign: "center",
          }}>
            <div style={{ fontSize: 22 }}>✨</div>
            <div style={{ fontSize: 9, color: "#c4b5fd", fontWeight: 700, marginTop: 2 }}>Уровень 3</div>
          </div>
        </div>

        {/* Week dots */}
        <div style={{ display: "flex", gap: 6 }}>
          {["Пн","Вт","Ср","Чт","Пт","Сб","Вс"].map((d, i) => {
            const done = i < 4;
            const today = i === 4;
            return (
              <div key={i} style={{ flex: 1, textAlign: "center" }}>
                <div style={{
                  width: "100%", aspectRatio: "1", borderRadius: 10,
                  background: done
                    ? "linear-gradient(135deg, #8b5cf6, #ec4899)"
                    : today ? "rgba(139,92,246,0.15)" : "rgba(255,255,255,0.04)",
                  border: today ? "1.5px solid rgba(167,139,250,0.4)" : "1.5px solid transparent",
                  display: "flex", alignItems: "center", justifyContent: "center",
                  fontSize: 11, color: done ? "#fff" : "transparent",
                  boxShadow: done ? "0 2px 12px rgba(139,92,246,0.45)" : "none",
                  marginBottom: 4,
                }}>
                  {done ? "✓" : ""}
                </div>
                <div style={{ fontSize: 9, color: "rgba(196,181,253,0.35)", fontWeight: 600 }}>{d}</div>
              </div>
            );
          })}
        </div>

        {/* XP bar */}
        <div style={{ marginTop: 14 }}>
          <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 5 }}>
            <span style={{ fontSize: 10, color: "rgba(196,181,253,0.4)" }}>1 140 / 2 000 XP</span>
            <span style={{ fontSize: 10, fontWeight: 700, background: "linear-gradient(90deg,#a78bfa,#f0abfc)", WebkitBackgroundClip: "text", WebkitTextFillColor: "transparent" }}>57%</span>
          </div>
          <div style={{ height: 5, background: "rgba(255,255,255,0.07)", borderRadius: 999, overflow: "hidden" }}>
            <div style={{
              width: "57%", height: "100%",
              background: "linear-gradient(90deg, #8b5cf6, #ec4899)",
              borderRadius: 999,
              boxShadow: "0 0 10px rgba(139,92,246,0.7)",
            }} />
          </div>
        </div>
      </div>

      {/* Stats grid */}
      <div style={{ margin: "14px 16px 0", display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10, position: "relative", zIndex: 1 }}>
        {stats.map((s, i) => {
          const glows = [
            "rgba(99,102,241,0.15)",
            "rgba(236,72,153,0.12)",
            "rgba(139,92,246,0.15)",
            "rgba(6,182,212,0.1)",
          ];
          const borders = [
            "rgba(99,102,241,0.2)",
            "rgba(236,72,153,0.18)",
            "rgba(167,139,250,0.2)",
            "rgba(6,182,212,0.15)",
          ];
          return (
            <div key={i} style={{
              background: glows[i],
              border: `1px solid ${borders[i]}`,
              borderRadius: 16, padding: "14px",
              backdropFilter: "blur(12px)",
              position: "relative", overflow: "hidden",
            }}>
              <div style={{ fontSize: 22, marginBottom: 6 }}>{s.icon}</div>
              <div style={{ fontSize: 21, fontWeight: 800, color: "#f0e8ff", letterSpacing: -0.5 }}>{s.value}</div>
              <div style={{ fontSize: 10.5, color: "rgba(196,181,253,0.45)", marginTop: 2 }}>{s.label}</div>
              <div style={{
                marginTop: 7, fontSize: 9.5, fontWeight: 600,
                color: s.trend === "up" ? "#a78bfa" : "rgba(196,181,253,0.4)",
              }}>✦ {s.sub}</div>
            </div>
          );
        })}
      </div>

      {/* Premium banner */}
      <div style={{
        position: "relative", zIndex: 1,
        margin: "14px 16px 0",
        background: "linear-gradient(135deg, rgba(139,92,246,0.22), rgba(236,72,153,0.15))",
        border: "1px solid rgba(167,139,250,0.28)",
        borderRadius: 16, padding: "14px 16px",
        display: "flex", alignItems: "center", justifyContent: "space-between",
      }}>
        <div>
          <div style={{ fontSize: 12, fontWeight: 700, color: "#c4b5fd" }}>⭐ Premium активен</div>
          <div style={{ fontSize: 10.5, color: "rgba(196,181,253,0.45)", marginTop: 2 }}>Осталось 14 дней · ×1.5 XP</div>
        </div>
        <div style={{
          background: "linear-gradient(135deg, #8b5cf6, #ec4899)",
          borderRadius: 10, padding: "7px 14px",
          fontSize: 11, fontWeight: 700, color: "#fff",
          boxShadow: "0 4px 14px rgba(139,92,246,0.5)",
        }}>Продлить</div>
      </div>

      {/* Bottom nav */}
      <div style={{
        position: "fixed", bottom: 0, left: 0, right: 0, zIndex: 100,
        background: "rgba(8,4,15,0.95)",
        backdropFilter: "blur(24px)",
        borderTop: "1px solid rgba(139,92,246,0.15)",
        display: "flex", padding: "8px 0 20px",
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
              filter: tab.active ? "drop-shadow(0 0 8px rgba(167,139,250,0.9))" : "none",
              transform: tab.active ? "scale(1.12)" : "scale(1)",
            }}>{tab.icon}</div>
            <div style={{ fontSize: 9, fontWeight: 600, color: tab.active ? "#c4b5fd" : "rgba(196,181,253,0.25)" }}>{tab.label}</div>
          </div>
        ))}
      </div>
    </div>
  );
}
