import React from 'react';

export function CoinShop() {
  return (
    <div className="w-full h-[100dvh] max-w-[390px] mx-auto bg-[#f8fafc] overflow-y-auto no-scrollbar relative font-sans flex flex-col">
      <style dangerouslySetInnerHTML={{__html: `
        .no-scrollbar::-webkit-scrollbar { display: none; }
        .no-scrollbar { -ms-overflow-style: none; scrollbar-width: none; }
      `}} />

      {/* Header Area */}
      <div className="bg-gradient-to-b from-[#0f172a] to-[#1e293b] pt-14 pb-8 px-5 rounded-b-[2.5rem] shadow-[0_10px_30px_rgba(15,23,42,0.3)] relative z-20 flex flex-col items-center">
        {/* Sparkles decorative */}
        <div className="absolute top-10 left-8 text-[#f59e0b] opacity-40 text-sm animate-pulse">✨</div>
        <div className="absolute top-16 right-10 text-[#f59e0b] opacity-30 text-xl animate-pulse" style={{ animationDelay: '1s' }}>✨</div>
        
        <div className="text-4xl mb-1 flex items-center gap-3 relative">
          <span className="text-4xl drop-shadow-[0_0_15px_rgba(245,158,11,0.5)]">🪙</span>
          <span className="text-transparent bg-clip-text bg-gradient-to-r from-[#fbbf24] to-[#f59e0b] font-extrabold tracking-tight" 
                style={{ filter: 'drop-shadow(0 2px 8px rgba(245, 158, 11, 0.3))' }}>
            480 монет
          </span>
        </div>
        <div className="text-slate-400 text-[13px] font-medium mb-6 uppercase tracking-widest">
          Зарабатывай больше ↓
        </div>
        
        <div className="flex flex-col gap-2.5 w-full max-w-sm">
          <div className="flex items-center gap-3 bg-white/5 hover:bg-white/10 transition-colors rounded-2xl px-4 py-3.5 border border-white/10 backdrop-blur-md">
             <div className="bg-emerald-500/20 w-8 h-8 rounded-full flex items-center justify-center shrink-0">
               <span className="text-sm">✅</span>
             </div>
             <span className="text-slate-200 text-[14px] font-medium">Бонус получен сегодня</span>
          </div>
          <div className="flex items-center justify-between bg-white/5 hover:bg-white/10 transition-colors rounded-2xl px-4 py-3.5 border border-white/10 backdrop-blur-md cursor-pointer group">
             <div className="flex items-center gap-3">
               <div className="bg-blue-500/20 w-8 h-8 rounded-full flex items-center justify-center shrink-0">
                 <span className="text-sm">📋</span>
               </div>
               <span className="text-slate-200 text-[14px] font-medium group-hover:text-white transition-colors">3 квеста доступно</span>
             </div>
             <div className="bg-white/10 w-7 h-7 rounded-full flex items-center justify-center text-slate-300 group-hover:bg-white/20 group-hover:text-white transition-all text-xs">
               →
             </div>
          </div>
        </div>
      </div>

      {/* Main Content Body */}
      <div className="flex-1 px-5 py-8 space-y-8 relative z-10">
        
        {/* Category: Питомцы */}
        <section>
          <h3 className="text-xl font-extrabold text-slate-800 mb-4 flex items-center gap-2.5 px-1 tracking-tight">
            <span className="text-2xl drop-shadow-sm">🐾</span> Питомцы
          </h3>
          <div className="grid grid-cols-2 gap-3.5">
            <ShopCard 
              emoji="🍣" title="Покормить питомца" price="20" 
              btnText="Купить" btnTheme="gold" 
            />
            <ShopCard 
              emoji="✏️" title="Переименовать питомца" price="50" 
              btnText="Купить" btnTheme="gold" 
            />
          </div>
        </section>

        {/* Category: Казино */}
        <section>
          <h3 className="text-xl font-extrabold text-slate-800 mb-4 flex items-center gap-2.5 px-1 tracking-tight">
            <span className="text-2xl drop-shadow-sm">🎰</span> Казино
          </h3>
          <div className="grid grid-cols-2 gap-3.5">
            <ShopCard 
              emoji="🎰" title="Крутить слоты" price="от 10" 
              btnText="Играть" btnTheme="purple" 
            />
            <ShopCard 
              emoji="🪙" title="Монетка" price="10–500" 
              btnText="Играть" btnTheme="purple" 
            />
          </div>
        </section>

        {/* Category: Бусты */}
        <section>
          <div className="flex items-center justify-between mb-4 px-1">
            <h3 className="text-xl font-extrabold text-slate-800 flex items-center gap-2.5 tracking-tight">
              <span className="text-2xl drop-shadow-sm">🚀</span> Бусты
            </h3>
            <span className="bg-[#14b8a6]/10 text-[#14b8a6] text-[10px] font-bold px-2 py-1 rounded-md uppercase tracking-wider">New</span>
          </div>
          <div className="grid grid-cols-2 gap-3.5">
            <ShopCard 
              emoji="⚡" title="Двойной опыт × 24ч" price="200" 
              btnText="Активировать" btnTheme="teal" 
            />
            <ShopCard 
              emoji="📌" title="Любимый чат" price="75" 
              btnText="Активировать" btnTheme="teal" 
            />
          </div>
        </section>

        {/* Category: Кастомизация */}
        <section>
          <div className="flex items-center justify-between mb-4 px-1">
            <h3 className="text-xl font-extrabold text-slate-800 flex items-center gap-2.5 tracking-tight">
              <span className="text-2xl drop-shadow-sm">🎨</span> Кастомизация
            </h3>
            <span className="bg-[#8b5cf6]/10 text-[#8b5cf6] text-[10px] font-bold px-2 py-1 rounded-md uppercase tracking-wider">New</span>
          </div>
          <div className="grid grid-cols-2 gap-3.5">
            <ShopCard 
              emoji="🎨" title="Тема интерфейса" price="100" 
              btnText="Выбрать" btnTheme="purple" 
            />
            <ShopCard 
              emoji="🖼" title="Рамка профиля" price="150" 
              btnText="Выбрать" btnTheme="purple" 
            />
          </div>
        </section>

        {/* Category: Подарки */}
        <section>
          <div className="flex items-center justify-between mb-4 px-1">
            <h3 className="text-xl font-extrabold text-slate-800 flex items-center gap-2.5 tracking-tight">
              <span className="text-2xl drop-shadow-sm">🎁</span> Подарки
            </h3>
            <span className="bg-[#f43f5e]/10 text-[#f43f5e] text-[10px] font-bold px-2 py-1 rounded-md uppercase tracking-wider">New</span>
          </div>
          <WideCard 
            emoji="🎁" title="Подарить монетку собеседнику" price="30" 
            btnText="Подарить" btnTheme="pink" 
          />
        </section>

        {/* Bottom Earn Strip */}
        <div className="bg-white rounded-[1.5rem] p-6 shadow-[0_8px_30px_-12px_rgba(0,0,0,0.06)] border border-slate-100 mt-12 mb-6">
          <div className="text-center font-extrabold text-slate-400 mb-5 text-[11px] uppercase tracking-[0.15em]">
            Как зарабатывать монеты?
          </div>
          <div className="flex flex-wrap gap-2.5 justify-center">
            <Badge emoji="📅" text="Ежедневный бонус" />
            <Badge emoji="📋" text="Квесты" />
            <Badge emoji="💬" text="Активность" />
          </div>
        </div>
        
      </div>
    </div>
  );
}

// Components

function ShopCard({ emoji, title, price, btnText, btnTheme }: any) {
  const themes: Record<string, string> = {
    gold: 'bg-gradient-to-r from-[#f59e0b] to-[#fbbf24] text-white shadow-[0_4px_15px_rgba(245,158,11,0.3)]',
    purple: 'bg-gradient-to-r from-[#8b5cf6] to-[#a855f7] text-white shadow-[0_4px_15px_rgba(139,92,246,0.3)]',
    teal: 'bg-gradient-to-r from-[#14b8a6] to-[#2dd4bf] text-white shadow-[0_4px_15px_rgba(20,184,166,0.3)]',
  };

  return (
    <div className="bg-white rounded-[24px] p-4 pb-4 shadow-[0_8px_24px_-8px_rgba(0,0,0,0.04)] border border-slate-50 flex flex-col items-center text-center relative overflow-hidden group hover:-translate-y-1 transition-all duration-300">
      <div className="absolute inset-0 bg-gradient-to-b from-white to-slate-50/50 z-0 pointer-events-none"></div>
      
      <div className="relative z-10 w-full flex flex-col items-center flex-1 h-full">
        <div className="text-[40px] mb-3 mt-1 drop-shadow-md group-hover:scale-110 group-hover:rotate-3 transition-transform duration-300">
          {emoji}
        </div>
        
        <div className="font-bold text-slate-800 text-[13px] leading-[1.3] mb-3 flex-1 flex items-center justify-center min-h-[34px]">
          {title}
        </div>
        
        <div className="w-full flex justify-center mb-3">
          <div className="text-[#f59e0b] font-extrabold text-[13px] bg-[#fef3c7] px-3 py-1.5 rounded-xl flex items-center gap-1.5 whitespace-nowrap">
            <span className="text-[11px] drop-shadow-sm">🪙</span> {price}
          </div>
        </div>
        
        <button className={`w-full py-2.5 rounded-xl font-bold text-[14px] transition-transform active:scale-95 ${themes[btnTheme]}`}>
          {btnText}
        </button>
      </div>
    </div>
  );
}

function WideCard({ emoji, title, price, btnText, btnTheme }: any) {
  const themes: Record<string, string> = {
    pink: 'bg-gradient-to-r from-[#f43f5e] to-[#fb7185] text-white shadow-[0_4px_15px_rgba(244,63,94,0.3)] hover:from-[#e11d48] hover:to-[#f43f5e]',
  };

  return (
    <div className="bg-white rounded-[24px] p-4 shadow-[0_8px_24px_-8px_rgba(0,0,0,0.04)] border border-slate-50 flex items-center justify-between gap-3 group hover:-translate-y-1 transition-all duration-300 relative overflow-hidden">
      <div className="absolute inset-0 bg-gradient-to-r from-white to-slate-50/50 z-0 pointer-events-none"></div>
      
      <div className="relative z-10 flex items-center gap-4">
        <div className="text-3xl bg-slate-50/80 border border-slate-100 w-[52px] h-[52px] rounded-2xl flex items-center justify-center shadow-inner group-hover:scale-110 group-hover:-rotate-6 transition-transform duration-300 shrink-0">
          {emoji}
        </div>
        <div className="pr-2">
          <div className="font-bold text-slate-800 text-[14px] leading-tight mb-1.5">
            {title}
          </div>
          <div className="text-[#f59e0b] font-extrabold text-[13px] flex items-center gap-1.5">
            <span className="text-[11px] drop-shadow-sm">🪙</span> {price}
          </div>
        </div>
      </div>
      
      <button className={`relative z-10 py-2.5 px-5 rounded-xl font-bold text-[14px] shadow-sm transition-transform active:scale-95 whitespace-nowrap ${themes[btnTheme]}`}>
        {btnText}
      </button>
    </div>
  );
}

function Badge({ emoji, text }: any) {
  return (
    <div className="bg-[#f8fafc] px-3.5 py-2.5 rounded-xl text-[13px] font-bold text-slate-600 shadow-[inset_0_2px_4px_rgba(0,0,0,0.02)] border border-slate-100 flex items-center gap-2 hover:bg-slate-100 transition-colors cursor-default">
      <span className="text-[15px]">{emoji}</span> {text}
    </div>
  );
}

export default CoinShop;
