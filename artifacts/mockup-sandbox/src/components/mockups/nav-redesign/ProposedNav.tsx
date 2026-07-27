import React from 'react';
import { BarChart2, Gamepad2, ShoppingBag, Heart, Gift, ArrowRight, CheckCircle2 } from 'lucide-react';

export function ProposedNav() {
  return (
    <div className="min-h-[100dvh] bg-[#0a0a0a] text-white flex flex-col md:flex-row items-center justify-center p-8 gap-12 font-sans overflow-x-hidden">
      
      {/* Phone Frame */}
      <div className="relative w-[375px] h-[780px] bg-[#1c1c1e] rounded-[48px] border-[8px] border-zinc-900 overflow-hidden shadow-2xl flex flex-col shrink-0 relative ring-1 ring-white/10">
        
        {/* Dynamic Island / Notch Fake */}
        <div className="absolute top-0 inset-x-0 h-7 flex justify-center z-20 pointer-events-none">
          <div className="w-32 h-7 bg-zinc-900 rounded-b-3xl"></div>
        </div>

        {/* Top Status Bar (fake) */}
        <div className="h-12 w-full flex justify-between items-center px-6 text-[13px] font-semibold text-white/90 z-10 pt-2">
          <span>9:41</span>
          <div className="flex gap-1.5 items-center">
            <div className="w-4 h-3 bg-white/90 rounded-sm"></div>
            <div className="w-4 h-3 bg-white/90 rounded-sm"></div>
            <div className="w-6 h-3 bg-white/90 rounded-sm"></div>
          </div>
        </div>

        {/* Content Area */}
        <div className="flex-1 flex flex-col p-4 overflow-y-auto pt-2">
          
          <div className="flex items-center justify-between mb-5">
            <h1 className="text-[28px] font-bold text-white tracking-tight">Статистика</h1>
            <div className="w-8 h-8 rounded-full bg-gradient-to-tr from-blue-500 to-purple-500 ring-2 ring-[#2c2c2e]"></div>
          </div>
          
          {/* Pill Tabs */}
          <div className="flex p-[2px] bg-[#2c2c2e] rounded-xl mb-6 shadow-inner">
            <div className="flex-1 bg-[#1c1c1e] rounded-[10px] py-1.5 text-center text-[13px] font-semibold text-white shadow-sm border border-white/5">
              Общая
            </div>
            <div className="flex-1 py-1.5 text-center text-[13px] font-medium text-[#8e8e93]">
              Топ
            </div>
            <div className="flex-1 py-1.5 text-center text-[13px] font-medium text-[#8e8e93]">
              Детально
            </div>
          </div>

          {/* Scrollable Content Simulation */}
          <div className="flex flex-col gap-4 pb-8">
            <div className="bg-[#2c2c2e] p-5 rounded-3xl flex justify-between items-center">
              <span className="text-[#8e8e93] font-medium">Баланс профиля</span>
              <span className="text-2xl font-bold tracking-tight">14,250 <span className="text-xl">💎</span></span>
            </div>
            
            <div className="grid grid-cols-2 gap-4">
              <div className="bg-[#2c2c2e] p-5 rounded-3xl flex flex-col justify-between h-28">
                <span className="text-[13px] font-medium text-[#8e8e93]">Сыграно игр</span>
                <span className="text-2xl font-bold">128</span>
              </div>
              <div className="bg-[#2c2c2e] p-5 rounded-3xl flex flex-col justify-between h-28">
                <span className="text-[13px] font-medium text-[#8e8e93]">Побед подряд</span>
                <span className="text-2xl font-bold text-emerald-400">12</span>
              </div>
            </div>

            <div className="h-40 bg-[#2c2c2e] rounded-3xl mt-2 flex flex-col items-center justify-center relative overflow-hidden border border-white/5">
              <span className="text-[#8e8e93] text-[13px] font-medium absolute top-4 left-5">Активность за неделю</span>
              
              <div className="absolute bottom-0 inset-x-0 h-16 flex items-end justify-between px-6 pb-4 gap-2">
                {[40, 70, 45, 90, 65, 80, 50].map((h, i) => (
                  <div key={i} className="w-full bg-[#007AFF] rounded-t-sm opacity-80" style={{ height: `${h}%` }}></div>
                ))}
              </div>
            </div>
            
            <div className="bg-[#2c2c2e] p-5 rounded-3xl flex flex-col gap-3">
              <span className="text-[#8e8e93] text-[13px] font-medium">Последние действия</span>
              
              {[1, 2].map((i) => (
                <div key={i} className="flex items-center justify-between py-2 border-t border-white/5 first:border-0">
                  <div className="flex items-center gap-3">
                    <div className="w-8 h-8 rounded-full bg-[#1c1c1e] flex items-center justify-center">
                      <Gamepad2 size={14} className="text-[#007AFF]" />
                    </div>
                    <span className="text-[15px] font-medium">Казино</span>
                  </div>
                  <span className="text-[15px] font-bold text-emerald-400">+50 💎</span>
                </div>
              ))}
            </div>
          </div>
        </div>

        {/* Bottom Nav - The Star of the Show */}
        <div className="h-[84px] bg-[#1c1c1e]/90 backdrop-blur-xl border-t border-[#38383a] flex px-2 pb-6 pt-2 items-start justify-between absolute bottom-0 w-full">
          <NavItem icon={<BarChart2 size={24} strokeWidth={2.5} />} label="Стата" active />
          <NavItem icon={<Gamepad2 size={24} strokeWidth={2.5} />} label="Игры" />
          <NavItem icon={<ShoppingBag size={24} strokeWidth={2.5} />} label="Магазин" />
          <NavItem icon={<Heart size={24} strokeWidth={2.5} />} label="Связи" />
          <NavItem icon={<Gift size={24} strokeWidth={2.5} />} label="Конкурс" />
        </div>
      </div>

      {/* Info Panels */}
      <div className="flex flex-col gap-6 max-w-[400px]">
        {/* Badge */}
        <div className="bg-[#1c1c1e] border border-[#2c2c2e] px-5 py-4 rounded-3xl flex items-center gap-4 shadow-xl">
          <div className="w-10 h-10 rounded-full bg-emerald-500/20 flex items-center justify-center shrink-0">
            <CheckCircle2 size={20} className="text-emerald-500" />
          </div>
          <div>
            <h3 className="font-bold text-white text-base">5 вкладок</h3>
            <p className="text-[#8e8e93] text-[13px] leading-tight mt-0.5">Чистый и просторный дизайн</p>
          </div>
        </div>

        {/* Legend Card */}
        <div className="bg-[#1c1c1e] border border-[#2c2c2e] rounded-[32px] p-7 shadow-2xl relative overflow-hidden">
          <div className="absolute top-0 right-0 w-32 h-32 bg-[#007AFF] rounded-full blur-[80px] opacity-10"></div>
          
          <h2 className="text-xl font-bold mb-3 text-white">Что изменилось?</h2>
          <p className="text-[14px] text-[#8e8e93] mb-8 leading-relaxed">
            Мы объединили редко используемые разделы в логические группы. Теперь интерфейс дышит, а нужные функции всегда под рукой.
          </p>
          
          <div className="flex flex-col gap-3">
            <MergerItem 
              from="🏆 Рейтинг" 
              to="🎮 Игры" 
              desc="Теперь это верхний таб внутри раздела Игры."
            />
            <MergerItem 
              from="💎 Подписки" 
              to="🛍 Магазин" 
              desc="Логично перенесены в раздел Магазин."
            />
            <MergerItem 
              from="👥 Рефералы" 
              to="🎁 Конкурс" 
              desc="Объединены с конкурсами для мотивации."
            />
          </div>
        </div>
      </div>
    </div>
  );
}

function NavItem({ icon, label, active = false }: { icon: React.ReactNode, label: string, active?: boolean }) {
  return (
    <div className="flex flex-col items-center justify-center flex-1 gap-1 cursor-pointer group">
      <div className={`transition-all duration-300 ${active ? 'text-[#007AFF] scale-100' : 'text-[#8e8e93] scale-95 group-hover:text-zinc-300'}`}>
        {icon}
      </div>
      <span className={`text-[10px] font-medium transition-colors duration-300 ${active ? 'text-[#007AFF]' : 'text-[#8e8e93] group-hover:text-zinc-300'}`}>
        {label}
      </span>
    </div>
  );
}

function MergerItem({ from, to, desc }: { from: string, to: string, desc: string }) {
  return (
    <div className="flex flex-col p-4 bg-[#2c2c2e] rounded-2xl border border-white/5 transition-transform hover:scale-[1.02] cursor-default">
      <div className="flex items-center gap-3 mb-2">
        <span className="font-semibold text-[15px] text-white">{from}</span>
        <ArrowRight size={14} className="text-[#8e8e93]" />
        <span className="font-semibold text-[15px] text-[#007AFF]">{to}</span>
      </div>
      <p className="text-[13px] text-[#8e8e93] leading-snug">{desc}</p>
    </div>
  );
}
