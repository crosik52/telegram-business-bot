import React from 'react';
import { 
  BarChart2, 
  Gamepad2, 
  Trophy, 
  Gem, 
  ShoppingBag, 
  Heart, 
  Gift,
  Wifi,
  Battery,
  Signal
} from 'lucide-react';

export function CurrentNav() {
  return (
    <div className="min-h-screen bg-[#0a0a0a] flex items-center justify-center p-8 font-sans">
      <div className="relative w-[375px] h-[780px] bg-[#1c1c1e] rounded-[40px] shadow-2xl overflow-hidden border-[8px] border-[#000000] flex flex-col">
        
        {/* iOS Status Bar */}
        <div className="h-[44px] w-full flex items-center justify-between px-6 text-white pt-1 shrink-0 z-10">
          <span className="text-[15px] font-semibold tracking-tight w-14 text-center">9:41</span>
          <div className="flex items-center gap-1.5 w-14 justify-end">
            <Signal size={15} strokeWidth={2.5} />
            <Wifi size={15} strokeWidth={2.5} />
            <Battery size={22} strokeWidth={2} className="opacity-90" />
          </div>
        </div>

        {/* Content */}
        <div className="flex-1 overflow-y-auto pb-32 no-scrollbar">
          <div className="px-4 pt-2 pb-6">
            <h1 className="text-2xl font-bold text-[#ffffff] mb-6 tracking-tight">Привет, Алексей 👋</h1>
            
            <div className="grid grid-cols-2 gap-3 mb-8">
              <StatCard label="Сообщений" value="3 847" />
              <StatCard label="Собеседников" value="42" />
              <StatCard label="Правок" value="118" />
              <StatCard label="Удалений" value="29" />
            </div>

            <h2 className="text-[17px] font-semibold text-[#ffffff] mb-3 tracking-tight">Топ собеседников</h2>
            <div className="bg-[#2c2c2e] rounded-2xl overflow-hidden">
              <UserRow name="Анна В." messages="1 204 msg" color="bg-[#FF9500]" initial="А" />
              <UserRow name="Дмитрий К." messages="842 msg" color="bg-[#34C759]" initial="Д" />
              <UserRow name="Иван С." messages="531 msg" color="bg-[#AF52DE]" initial="И" border={false} />
            </div>
          </div>
        </div>

        {/* Annotation Overlay */}
        <div className="absolute bottom-[96px] left-1/2 -translate-x-1/2 flex flex-col items-center drop-shadow-xl z-30 animate-pulse">
          <div className="bg-[#FF3B30] text-white px-3.5 py-2 rounded-xl text-[13px] font-semibold whitespace-nowrap shadow-lg shadow-[#FF3B30]/20">
            7 вкладок — перегружено
          </div>
          <div className="w-0 h-0 border-l-[6px] border-l-transparent border-r-[6px] border-r-transparent border-t-[6px] border-t-[#FF3B30]"></div>
        </div>

        {/* Overcrowded Bottom Nav */}
        <div className="absolute bottom-0 w-full bg-[#1c1c1e]/90 backdrop-blur-xl border-t border-[#38383a] pb-[34px] pt-2 px-1 flex justify-between items-start z-20">
          <NavItem icon={<BarChart2 size={24} strokeWidth={2} />} label="Стата" active />
          <NavItem icon={<Gamepad2 size={24} strokeWidth={2} />} label="Игры" />
          <NavItem icon={<Trophy size={24} strokeWidth={2} />} label="Рейтинг" />
          <NavItem icon={<Gem size={24} strokeWidth={2} />} label="Поддержать" />
          <NavItem icon={<ShoppingBag size={24} strokeWidth={2} />} label="Магазин" />
          <NavItem icon={<Heart size={24} strokeWidth={2} />} label="Связи" />
          <NavItem icon={<Gift size={24} strokeWidth={2} />} label="Розыгрыш" />
        </div>

        {/* Home Indicator */}
        <div className="absolute bottom-2 left-1/2 -translate-x-1/2 w-[134px] h-[5px] bg-[#ffffff] rounded-full z-30" />
      </div>
    </div>
  );
}

function StatCard({ label, value }: { label: string; value: string }) {
  return (
    <div className="bg-[#2c2c2e] p-4 rounded-2xl flex flex-col gap-1.5">
      <span className="text-[13px] text-[#8e8e93] font-medium leading-none">{label}</span>
      <span className="text-[22px] font-bold text-[#ffffff] leading-none tracking-tight">{value}</span>
    </div>
  );
}

function UserRow({ name, messages, color, initial, border = true }: { name: string, messages: string, color: string, initial: string, border?: boolean }) {
  return (
    <div className="relative">
      <div className="flex items-center justify-between px-4 py-3">
        <div className="flex items-center gap-3.5">
          <div className={`w-10 h-10 rounded-full ${color} flex items-center justify-center text-white font-semibold text-[17px]`}>
            {initial}
          </div>
          <span className="text-[#ffffff] font-medium text-[16px] tracking-tight">{name}</span>
        </div>
        <span className="text-[#8e8e93] text-[15px]">{messages}</span>
      </div>
      {border && (
        <div className="absolute bottom-0 left-[70px] right-0 h-[1px] bg-[#38383a]" />
      )}
    </div>
  );
}

function NavItem({ icon, label, active = false }: { icon: React.ReactNode, label: string, active?: boolean }) {
  return (
    <div className="flex flex-col items-center gap-1 flex-1 min-w-0 px-0.5 pt-1">
      <div className={`${active ? 'text-[#007AFF]' : 'text-[#8e8e93]'}`}>
        {icon}
      </div>
      <span className={`text-[10px] leading-none font-medium w-full text-center truncate ${active ? 'text-[#007AFF]' : 'text-[#8e8e93]'}`}>
        {label}
      </span>
    </div>
  );
}
