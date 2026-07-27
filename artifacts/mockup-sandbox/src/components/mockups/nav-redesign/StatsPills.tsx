import React from 'react';
import { BarChart2, MessageCircle, User, Settings, Clock, Image, Video, Music, File } from 'lucide-react';

export function StatsPills() {
  return (
    <div className="flex items-center justify-center min-h-screen bg-black p-4 font-sans">
      <div className="w-[375px] h-[812px] bg-[#1c1c1e] text-white rounded-[40px] overflow-hidden shadow-2xl relative flex flex-col border-[8px] border-black">
        
        {/* Header */}
        <div className="pt-12 pb-3 px-4 text-center font-semibold text-[17px] sticky top-0 bg-[#1c1c1e]/95 backdrop-blur-md z-30 border-b border-[#2c2c2e]">
          Статистика
        </div>

        {/* Sticky Pill Bar */}
        <div className="sticky top-[73px] bg-[#1c1c1e]/95 backdrop-blur-md z-20 px-4 py-3 flex gap-2 overflow-x-auto no-scrollbar border-b border-[#2c2c2e]/50">
          <button className="px-4 py-1.5 bg-[#007AFF] rounded-full text-sm font-medium flex items-center gap-1.5 shrink-0 transition-colors">
            Общая <div className="w-1.5 h-1.5 rounded-full bg-white"></div>
          </button>
          <button className="px-4 py-1.5 bg-[#2c2c2e] text-[#8e8e93] rounded-full text-sm font-medium shrink-0 transition-colors">
            Топ
          </button>
          <button className="px-4 py-1.5 bg-[#2c2c2e] text-[#8e8e93] rounded-full text-sm font-medium shrink-0 transition-colors">
            Детально
          </button>
        </div>

        {/* Scrollable Content */}
        <div className="flex-1 overflow-y-auto pb-[90px] no-scrollbar">
          
          {/* Section 1: Общая */}
          <div className="p-4 space-y-4">
            {/* Streak banner */}
            <div className="bg-gradient-to-r from-[#ff9500] to-[#ff2d55] rounded-2xl p-4 shadow-lg shadow-[#ff2d55]/20">
              <div className="flex items-center gap-2 text-white font-bold text-lg">
                <span className="text-2xl drop-shadow-md">🔥</span> 14 дней подряд с Мария
              </div>
            </div>

            {/* 4 Stats Cards */}
            <div className="grid grid-cols-2 gap-3">
              <div className="bg-[#2c2c2e] rounded-2xl p-3.5 border border-[#3a3a3c]/50">
                <div className="text-[#8e8e93] text-[13px] mb-1">Сообщений</div>
                <div className="text-xl font-bold">3 847</div>
              </div>
              <div className="bg-[#2c2c2e] rounded-2xl p-3.5 border border-[#3a3a3c]/50">
                <div className="text-[#8e8e93] text-[13px] mb-1">Собеседников</div>
                <div className="text-xl font-bold">42</div>
              </div>
              <div className="bg-[#2c2c2e] rounded-2xl p-3.5 border border-[#3a3a3c]/50">
                <div className="text-[#8e8e93] text-[13px] mb-1">Правок</div>
                <div className="text-xl font-bold">118</div>
              </div>
              <div className="bg-[#2c2c2e] rounded-2xl p-3.5 border border-[#3a3a3c]/50">
                <div className="text-[#8e8e93] text-[13px] mb-1">Удалений</div>
                <div className="text-xl font-bold text-[#ff3b30]">29</div>
              </div>
            </div>

            {/* Activity heatmap */}
            <div className="bg-[#2c2c2e] rounded-2xl p-4 border border-[#3a3a3c]/50">
              <div className="text-[15px] font-semibold mb-3">Активность</div>
              <div className="flex gap-1.5">
                {Array.from({ length: 7 }).map((_, colIndex) => (
                  <div key={colIndex} className="flex flex-col gap-1.5 flex-1">
                    {Array.from({ length: 4 }).map((_, rowIndex) => {
                      // Deterministic mock opacity for visual consistency
                      const opacities = [0.2, 0.6, 1, 0.2];
                      const opacity = opacities[(colIndex + rowIndex) % 4];
                      return (
                        <div 
                          key={rowIndex} 
                          className="w-full aspect-square rounded-[4px] bg-[#007AFF]"
                          style={{ opacity }}
                        ></div>
                      )
                    })}
                  </div>
                ))}
              </div>
              <div className="flex justify-between text-[11px] text-[#8e8e93] mt-2 font-medium">
                <span>Пн</span>
                <span>Вт</span>
                <span>Ср</span>
                <span>Чт</span>
                <span>Пт</span>
                <span>Сб</span>
                <span>Вс</span>
              </div>
            </div>

            {/* Дайджест дня card */}
            <div className="bg-[#2c2c2e] rounded-2xl p-4 border border-[#3a3a3c]/50">
              <div className="text-[15px] font-semibold mb-3">Дайджест дня</div>
              <div className="flex items-end justify-between mb-3">
                <div className="text-[28px] font-bold leading-none">47</div>
                <div className="text-[13px] text-[#8e8e93] font-medium mb-1">сообщений</div>
              </div>
              <div className="flex gap-4">
                <div className="flex flex-col gap-0.5">
                  <div className="text-[13px] text-[#30d158] font-semibold">↓ 12</div>
                  <div className="text-[11px] text-[#8e8e93]">входящих</div>
                </div>
                <div className="flex flex-col gap-0.5">
                  <div className="text-[13px] text-[#007AFF] font-semibold">↑ 35</div>
                  <div className="text-[11px] text-[#8e8e93]">исходящих</div>
                </div>
                <div className="flex flex-col gap-0.5 ml-auto text-right">
                  <div className="text-[13px] text-white font-semibold">8</div>
                  <div className="text-[11px] text-[#8e8e93]">активных чатов</div>
                </div>
              </div>
            </div>
          </div>

          {/* Section Divider: Топ собеседников */}
          <div className="flex items-center gap-2 px-4 pt-4 pb-2">
            <div className="w-1.5 h-1.5 rounded-full bg-[#8e8e93]"></div>
            <div className="text-[13px] font-semibold text-[#8e8e93] uppercase tracking-wider">Топ собеседников</div>
          </div>

          {/* Section 2: Топ собеседников */}
          <div className="px-4 pb-4 space-y-2.5">
            {[
              { name: "Мария К.", count: 847, medal: "🥇", color: "bg-[#FF9500]", initial: "М" },
              { name: "Иван П.", count: 623, medal: "🥈", color: "bg-[#E5E5EA]", initial: "И", textColor: "text-black" },
              { name: "Анна С.", count: 401, medal: "🥉", color: "bg-[#A2845E]", initial: "А" },
              { name: "Дмитрий В.", count: 288, medal: "4", color: "bg-[#34C759]", initial: "Д" },
              { name: "Елена Н.", count: 195, medal: "5", color: "bg-[#AF52DE]", initial: "Е" }
            ].map((user, i) => (
              <div key={i} className="flex items-center bg-[#2c2c2e] rounded-2xl p-3 gap-3 border border-[#3a3a3c]/30">
                <div className="w-5 text-center text-[15px] font-bold text-[#8e8e93] shrink-0">
                  {user.medal}
                </div>
                <div className={`w-[42px] h-[42px] rounded-full flex items-center justify-center text-lg font-bold shrink-0 shadow-inner ${user.color} ${user.textColor || 'text-white'}`}>
                  {user.initial}
                </div>
                <div className="flex-1 min-w-0 py-1">
                  <div className="text-[15px] font-semibold truncate text-white">{user.name}</div>
                  <div className="text-[12px] text-[#8e8e93] mt-0.5">Был(а) недавно</div>
                </div>
                <div className="text-right py-1">
                  <div className="text-[15px] font-bold">{user.count}</div>
                  <div className="w-14 h-1.5 bg-[#1c1c1e] rounded-full mt-1.5 overflow-hidden">
                    <div className="h-full bg-[#007AFF] rounded-full" style={{ width: `${Math.max(10, (user.count / 847) * 100)}%` }}></div>
                  </div>
                </div>
              </div>
            ))}
          </div>

          {/* Section Divider: Детально */}
          <div className="flex items-center gap-2 px-4 pt-4 pb-2">
            <div className="w-1.5 h-1.5 rounded-full bg-[#8e8e93]"></div>
            <div className="text-[13px] font-semibold text-[#8e8e93] uppercase tracking-wider">Детально</div>
          </div>

          {/* Section 3: Детально */}
          <div className="px-4 pb-8 space-y-4">
            
            {/* Word cloud */}
            <div className="bg-[#2c2c2e] rounded-2xl p-4 border border-[#3a3a3c]/50">
              <div className="text-[15px] font-semibold mb-3">Популярные слова</div>
              <div className="flex flex-wrap gap-2">
                {[
                  { w: "привет", c: "bg-[#007AFF]/15 text-[#007AFF]", s: "text-[16px]" },
                  { w: "спасибо", c: "bg-[#34C759]/15 text-[#34C759]", s: "text-[15px]" },
                  { w: "завтра", c: "bg-[#FF9500]/15 text-[#FF9500]", s: "text-[14px]" },
                  { w: "хорошо", c: "bg-[#AF52DE]/15 text-[#AF52DE]", s: "text-[14px]" },
                  { w: "понял", c: "bg-[#FF3B30]/15 text-[#FF3B30]", s: "text-[13px]" },
                  { w: "ок", c: "bg-[#5AC8FA]/15 text-[#5AC8FA]", s: "text-[16px]" },
                  { w: "встреча", c: "bg-[#FF2D55]/15 text-[#FF2D55]", s: "text-[13px]" },
                  { w: "документ", c: "bg-[#8E8E93]/20 text-[#a1a1a6]", s: "text-[12px]" },
                  { w: "отлично", c: "bg-[#FF9500]/15 text-[#FF9500]", s: "text-[14px]" },
                  { w: "когда", c: "bg-[#007AFF]/15 text-[#007AFF]", s: "text-[12px]" },
                ].map((word, i) => (
                  <div key={i} className={`px-3 py-1.5 rounded-full font-medium ${word.c} ${word.s}`}>
                    {word.w}
                  </div>
                ))}
              </div>
            </div>

            {/* Media stats */}
            <div className="grid grid-cols-2 gap-3">
              <div className="bg-[#2c2c2e] rounded-2xl p-3.5 flex items-center gap-3 border border-[#3a3a3c]/50">
                <div className="w-9 h-9 rounded-full bg-[#5AC8FA]/15 flex items-center justify-center text-[#5AC8FA]">
                  <Image size={18} strokeWidth={2.5} />
                </div>
                <div>
                  <div className="text-[12px] text-[#8e8e93] font-medium">Фото</div>
                  <div className="text-[16px] font-bold">142</div>
                </div>
              </div>
              <div className="bg-[#2c2c2e] rounded-2xl p-3.5 flex items-center gap-3 border border-[#3a3a3c]/50">
                <div className="w-9 h-9 rounded-full bg-[#FF2D55]/15 flex items-center justify-center text-[#FF2D55]">
                  <Video size={18} strokeWidth={2.5} />
                </div>
                <div>
                  <div className="text-[12px] text-[#8e8e93] font-medium">Видео</div>
                  <div className="text-[16px] font-bold">38</div>
                </div>
              </div>
              <div className="bg-[#2c2c2e] rounded-2xl p-3.5 flex items-center gap-3 border border-[#3a3a3c]/50">
                <div className="w-9 h-9 rounded-full bg-[#AF52DE]/15 flex items-center justify-center text-[#AF52DE]">
                  <Music size={18} strokeWidth={2.5} />
                </div>
                <div>
                  <div className="text-[12px] text-[#8e8e93] font-medium">Аудио</div>
                  <div className="text-[16px] font-bold">91</div>
                </div>
              </div>
              <div className="bg-[#2c2c2e] rounded-2xl p-3.5 flex items-center gap-3 border border-[#3a3a3c]/50">
                <div className="w-9 h-9 rounded-full bg-[#007AFF]/15 flex items-center justify-center text-[#007AFF]">
                  <File size={18} strokeWidth={2.5} />
                </div>
                <div>
                  <div className="text-[12px] text-[#8e8e93] font-medium">Файлы</div>
                  <div className="text-[16px] font-bold">27</div>
                </div>
              </div>
            </div>

            {/* Sticker stats */}
            <div className="bg-[#2c2c2e] rounded-2xl p-5 flex justify-around border border-[#3a3a3c]/50">
              <div className="flex flex-col items-center gap-2">
                <div className="text-[32px] drop-shadow-sm">😂</div>
                <div className="text-[13px] font-bold text-[#8e8e93]">×34</div>
              </div>
              <div className="flex flex-col items-center gap-2">
                <div className="text-[32px] drop-shadow-sm">❤️</div>
                <div className="text-[13px] font-bold text-[#8e8e93]">×28</div>
              </div>
              <div className="flex flex-col items-center gap-2">
                <div className="text-[32px] drop-shadow-sm">👍</div>
                <div className="text-[13px] font-bold text-[#8e8e93]">×21</div>
              </div>
            </div>

            {/* Badges */}
            <div className="flex gap-2">
              <div className="bg-[#2c2c2e] px-2 py-2.5 rounded-xl flex-1 flex items-center justify-center gap-1.5 text-[13px] font-medium border border-[#3a3a3c]/80 shadow-sm">
                Болтун 💬
              </div>
              <div className="bg-[#2c2c2e] px-2 py-2.5 rounded-xl flex-1 flex items-center justify-center gap-1.5 text-[13px] font-medium border border-[#3a3a3c]/80 shadow-sm">
                Ночная сова 🦉
              </div>
              <div className="bg-[#2c2c2e] px-2 py-2.5 rounded-xl flex-1 flex items-center justify-center gap-1.5 text-[13px] font-medium border border-[#3a3a3c]/80 shadow-sm">
                Стрикер 🔥
              </div>
            </div>

          </div>
        </div>

        {/* Bottom Nav */}
        <div className="absolute bottom-0 left-0 right-0 h-[88px] bg-[#1c1c1e]/90 backdrop-blur-xl border-t border-[#2c2c2e] flex justify-around px-2 pt-2.5 pb-7 z-30">
          <button className="flex flex-col items-center gap-1 w-[20%] text-[#8e8e93]">
            <MessageCircle size={24} strokeWidth={1.5} />
            <span className="text-[10px] font-medium">Чаты</span>
          </button>
          <button className="flex flex-col items-center gap-1 w-[20%] text-[#007AFF]">
            <BarChart2 size={24} strokeWidth={2} />
            <span className="text-[10px] font-medium">Стата</span>
          </button>
          <button className="flex flex-col items-center gap-1 w-[20%] text-[#8e8e93]">
            <Clock size={24} strokeWidth={1.5} />
            <span className="text-[10px] font-medium">История</span>
          </button>
          <button className="flex flex-col items-center gap-1 w-[20%] text-[#8e8e93]">
            <User size={24} strokeWidth={1.5} />
            <span className="text-[10px] font-medium">Профиль</span>
          </button>
          <button className="flex flex-col items-center gap-1 w-[20%] text-[#8e8e93]">
            <Settings size={24} strokeWidth={1.5} />
            <span className="text-[10px] font-medium">Настройки</span>
          </button>
        </div>

      </div>
    </div>
  );
}
