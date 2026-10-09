import { useEffect, useState } from "react";
import { SANTIAGO } from "./marketing/calendar";
import { asShared } from "./sharedAccount";

const MONTHS = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"];

/** «Viernes 9 de octubre» and «15:47», in Santiago, whatever the browser's zone. */
function santiagoParts(now: Date): { date: string; time: string } {
  const f = new Intl.DateTimeFormat("es-CL", {
    timeZone: SANTIAGO, weekday: "long", day: "numeric", month: "numeric", hour: "2-digit", minute: "2-digit", hourCycle: "h23",
  });
  const p = Object.fromEntries(f.formatToParts(now).map((x) => [x.type, x.value]));
  const weekday = p.weekday.charAt(0).toUpperCase() + p.weekday.slice(1);
  return { date: `${weekday} ${Number(p.day)} de ${MONTHS[Number(p.month) - 1]}`, time: `${p.hour}:${p.minute}` };
}

const LINK =
  "inline-flex min-h-11 items-center gap-1.5 rounded-lg border border-white/25 px-4 text-sm font-semibold text-white transition-colors hover:border-[#5eead4] hover:bg-white/10 focus:outline-none focus-visible:ring-2 focus-visible:ring-[#5eead4]";

/**
 * «Hoy»'s greeting: the OrigenLab charcoal card with the logo's two teals, who is working, the
 * Santiago date and time, today's two counts and the three tools the team leaves the panel for.
 */
export function HeroHeader({ name, counts, driveCasosUrl, now }: {
  name: string;
  counts: { decide: number; followUps: number } | null;
  driveCasosUrl: string | null;
  now?: Date;
}) {
  const [tick, setTick] = useState(() => now ?? new Date());
  useEffect(() => {
    if (now) return;
    const id = window.setInterval(() => setTick(new Date()), 30_000);
    return () => window.clearInterval(id);
  }, [now]);
  const { date, time } = santiagoParts(now ?? tick);
  const first = name.trim().split(/\s+/)[0] || "";
  const links = [
    { label: "Gmail", href: asShared("https://mail.google.com/mail/") },
    { label: "Drive casos", href: asShared(driveCasosUrl ?? "https://drive.google.com/drive/my-drive") },
    { label: "Sitio web", href: "https://origenlab.cl/" },
  ];
  return (
    <header className="relative isolate overflow-hidden rounded-2xl bg-[#141617] px-6 py-7 text-[#ececeb] sm:px-9" data-testid="hero-header">
      <div aria-hidden="true" className="absolute inset-y-0 right-0 -z-10 w-[46%] bg-[#1d2022] [clip-path:polygon(14%_0,100%_0,100%_100%,0_100%)]" />
      <div aria-hidden="true" className="crm-hero-block absolute right-36 top-0 h-5 w-20 bg-[#14b8a6]" />
      <div aria-hidden="true" className="crm-hero-block absolute bottom-0 right-16 h-7 w-11 bg-[#0f766e]" />
      <div className="flex flex-wrap items-end justify-between gap-6">
        <div className="crm-hero-text flex flex-col gap-1.5">
          <span className="font-mono text-xs tracking-[.28em] text-[#8d9295]">HOY</span>
          <h1 className="text-3xl font-extrabold tracking-tight text-white sm:text-[2.4rem]">{first ? `Hola, ${first}` : "Hola"}</h1>
          <p className="text-[15px] text-[#b9bcbd]">
            {date} · <strong className="font-semibold text-white">{time}</strong>
            {counts ? ` · ${counts.decide} por decidir · ${counts.followUps} seguimientos` : null}
          </p>
        </div>
        <nav aria-label="Accesos" className="flex flex-wrap gap-2.5">
          {links.map((l) => (
            <a key={l.label} href={l.href} target="_blank" rel="noopener noreferrer" className={LINK}>
              {l.label} <span aria-hidden="true">↗</span>
            </a>
          ))}
        </nav>
      </div>
    </header>
  );
}
