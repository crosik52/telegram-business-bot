import React from 'react';

export default function PetScreen() {
  return (
    <div className="pet-screen-container" style={styles.container}>
      <style dangerouslySetInnerHTML={{__html: `
        @keyframes float {
          0%, 100% { transform: translateY(0px) rotate(0deg); }
          50% { transform: translateY(-8px) rotate(2deg); }
        }
        @keyframes pulse-warn {
          0%, 100% { opacity: 1; }
          50% { opacity: 0.6; }
        }
        @keyframes bounce-bubble {
          0%, 100% { transform: translateY(0); }
          50% { transform: translateY(-4px); }
        }
        .pet-avatar {
          animation: float 4s ease-in-out infinite;
        }
        .speech-bubble {
          animation: bounce-bubble 3s ease-in-out infinite;
        }
        .warning-bar {
          animation: pulse-warn 1.5s ease-in-out infinite;
        }
        * {
          box-sizing: border-box;
          font-family: system-ui, -apple-system, sans-serif;
        }
        .action-button:active {
          transform: scale(0.96);
        }
      `}} />

      {/* Top bar */}
      <div style={styles.topBar}>
        <div style={styles.title}>Мой питомец</div>
        <div style={styles.coinBalance}>
          <span>🪙</span> <span style={{ fontWeight: 700 }}>480</span>
        </div>
      </div>

      {/* Hero section */}
      <div style={styles.heroSection}>
        <div style={{...styles.speechBubble, position: 'relative'}} className="speech-bubble">
          Мяу! Я немного голодна 🍣
          <div style={styles.speechBubbleTail}></div>
        </div>

        <div style={styles.avatarContainer} className="pet-avatar">
          <div style={styles.avatarCircle}>
            <span style={{ fontSize: '72px', lineHeight: 1, filter: 'drop-shadow(0 8px 16px rgba(245, 158, 11, 0.2))' }}>🐱</span>
          </div>
        </div>

        <div style={styles.nameSection}>
          <h2 style={styles.petName}>Пуговка</h2>
          <div style={styles.badgeContainer}>
            <span style={styles.badge}>Ур. 7</span>
            <span style={styles.badgePrimary}>Игривая</span>
          </div>
        </div>
      </div>

      {/* Stats section */}
      <div style={styles.statsSection}>
        <StatBar label="Опыт: 340 / 500" progress={68} color="#fcd34d" />
        <StatBar label="Настроение 😊" valueText="82%" progress={82} color="#34d399" />
        <StatBar label="Сытость 🍚" valueText="60%" progress={60} color="#fb923c" pulse={true} />
      </div>

      {/* Action buttons */}
      <div style={styles.actionsGrid}>
        <ActionButton 
          icon="🍣" title="Покормить" subtitle="20 🪙" 
          tint="#fffbeb" borderColor="#fde68a" textColor="#b45309" 
        />
        <ActionButton 
          icon="🎾" title="Поиграть" subtitle="Готово!" 
          tint="#ecfdf5" borderColor="#a7f3d0" textColor="#047857" 
        />
        <ActionButton 
          icon="🤗" title="Обнять" subtitle="через 45 мин" 
          tint="#f8fafc" borderColor="#e2e8f0" textColor="#64748b" opacity={0.65}
        />
        <ActionButton 
          icon="✏️" title="Переименовать" subtitle="50 🪙" 
          tint="#faf5ff" borderColor="#e9d5ff" textColor="#7e22ce" 
        />
      </div>

      {/* Bottom strip */}
      <div style={styles.bottomStrip}>
        Чат с Аней • Родилась 14 дней назад
      </div>
    </div>
  );
}

const StatBar = ({ label, valueText, progress, color, pulse }: any) => (
  <div style={styles.statRow}>
    <div style={styles.statLabelContainer}>
      <span style={styles.statLabel}>{label}</span>
      {valueText && <span style={styles.statValue}>{valueText}</span>}
    </div>
    <div style={styles.barBackground}>
      <div 
        className={pulse ? "warning-bar" : ""}
        style={{...styles.barFill, width: `${progress}%`, backgroundColor: color}} 
      />
    </div>
  </div>
);

const ActionButton = ({ icon, title, subtitle, tint, borderColor, textColor, opacity = 1 }: any) => (
  <button className="action-button" style={{...styles.actionButton, backgroundColor: tint, borderColor: borderColor, opacity}}>
    <div style={styles.actionIcon}>{icon}</div>
    <div style={{...styles.actionTitle, color: textColor}}>{title}</div>
    <div style={{...styles.actionSubtitle, color: textColor}}>{subtitle}</div>
  </button>
);

const styles: Record<string, React.CSSProperties> = {
  container: {
    width: '100%',
    height: '100%',
    minHeight: '100dvh',
    backgroundColor: '#fdf8f2',
    color: '#3f3f46',
    display: 'flex',
    flexDirection: 'column',
    padding: '24px 20px',
    overflowY: 'auto',
    position: 'relative' as 'relative',
    WebkitTapHighlightColor: 'transparent',
  },
  topBar: {
    display: 'flex',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginBottom: '40px',
  },
  title: {
    fontSize: '22px',
    fontWeight: 800,
    color: '#27272a',
    letterSpacing: '-0.02em',
  },
  coinBalance: {
    display: 'flex',
    alignItems: 'center',
    gap: '6px',
    backgroundColor: 'white',
    padding: '8px 14px',
    borderRadius: '100px',
    boxShadow: '0 4px 12px rgba(245, 158, 11, 0.08)',
    fontSize: '16px',
    color: '#b45309',
  },
  heroSection: {
    display: 'flex',
    flexDirection: 'column',
    alignItems: 'center',
    marginBottom: '48px',
  },
  speechBubble: {
    backgroundColor: 'white',
    padding: '14px 24px',
    borderRadius: '24px',
    fontSize: '15px',
    fontWeight: 600,
    color: '#3f3f46',
    boxShadow: '0 12px 32px rgba(0,0,0,0.04)',
    marginBottom: '24px',
    position: 'relative',
    zIndex: 10,
  },
  speechBubbleTail: {
    content: '""',
    position: 'absolute',
    bottom: '-6px',
    left: '50%',
    transform: 'translateX(-50%) rotate(45deg)',
    width: '16px',
    height: '16px',
    backgroundColor: 'white',
    zIndex: -1,
    borderRadius: '2px',
  },
  avatarContainer: {
    position: 'relative',
    marginBottom: '28px',
  },
  avatarCircle: {
    width: '130px',
    height: '130px',
    backgroundColor: '#fff',
    borderRadius: '50%',
    display: 'flex',
    justifyContent: 'center',
    alignItems: 'center',
    boxShadow: '0 16px 40px rgba(245, 158, 11, 0.12), inset 0 0 0 6px #fdf8f2, inset 0 0 0 8px #fef3c7',
  },
  nameSection: {
    display: 'flex',
    flexDirection: 'column',
    alignItems: 'center',
    gap: '12px',
  },
  petName: {
    fontSize: '32px',
    fontWeight: 800,
    margin: 0,
    color: '#27272a',
    letterSpacing: '-0.03em',
  },
  badgeContainer: {
    display: 'flex',
    gap: '8px',
  },
  badge: {
    backgroundColor: 'rgba(245, 158, 11, 0.12)',
    color: '#b45309',
    padding: '6px 14px',
    borderRadius: '100px',
    fontSize: '14px',
    fontWeight: 700,
  },
  badgePrimary: {
    backgroundColor: 'white',
    color: '#fb7185',
    padding: '6px 14px',
    borderRadius: '100px',
    fontSize: '14px',
    fontWeight: 700,
    boxShadow: '0 4px 12px rgba(251, 113, 133, 0.1)',
  },
  statsSection: {
    backgroundColor: 'white',
    borderRadius: '32px',
    padding: '28px',
    boxShadow: '0 12px 40px rgba(0,0,0,0.03)',
    display: 'flex',
    flexDirection: 'column',
    gap: '24px',
    marginBottom: '28px',
  },
  statRow: {
    display: 'flex',
    flexDirection: 'column',
    gap: '10px',
  },
  statLabelContainer: {
    display: 'flex',
    justifyContent: 'space-between',
    alignItems: 'center',
  },
  statLabel: {
    fontSize: '15px',
    fontWeight: 600,
    color: '#52525b',
  },
  statValue: {
    fontSize: '15px',
    fontWeight: 700,
    color: '#27272a',
  },
  barBackground: {
    height: '10px',
    backgroundColor: '#f4f4f5',
    borderRadius: '100px',
    overflow: 'hidden',
  },
  barFill: {
    height: '100%',
    borderRadius: '100px',
    transition: 'width 0.5s ease-out',
  },
  actionsGrid: {
    display: 'grid',
    gridTemplateColumns: '1fr 1fr',
    gap: '16px',
    marginBottom: '32px',
  },
  actionButton: {
    display: 'flex',
    flexDirection: 'column',
    alignItems: 'center',
    justifyContent: 'center',
    padding: '24px 16px',
    borderRadius: '28px',
    border: '2px solid transparent',
    cursor: 'pointer',
    transition: 'all 0.15s ease',
    boxShadow: '0 4px 16px rgba(0,0,0,0.02)',
    outline: 'none',
    appearance: 'none',
  },
  actionIcon: {
    fontSize: '36px',
    marginBottom: '14px',
    filter: 'drop-shadow(0 4px 8px rgba(0,0,0,0.1))',
  },
  actionTitle: {
    fontSize: '16px',
    fontWeight: 700,
    marginBottom: '6px',
  },
  actionSubtitle: {
    fontSize: '13px',
    fontWeight: 600,
    opacity: 0.8,
  },
  bottomStrip: {
    textAlign: 'center',
    fontSize: '14px',
    fontWeight: 600,
    color: '#a1a1aa',
    marginTop: 'auto',
    paddingTop: '16px',
    paddingBottom: '16px',
  }
};
