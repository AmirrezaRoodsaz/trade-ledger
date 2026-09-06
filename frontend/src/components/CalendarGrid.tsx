import type { Calendar } from "../api/trades";
import { num, r, signClass } from "../fmt";

const WEEKDAYS = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"];
const MONTHS = [
  "Januar",
  "Februar",
  "März",
  "April",
  "Mai",
  "Juni",
  "Juli",
  "August",
  "September",
  "Oktober",
  "November",
  "Dezember",
];

const pad = (value: number) => String(value).padStart(2, "0");

export function monthKey(year: number, month: number, day: number): string {
  return `${year}-${pad(month)}-${pad(day)}`;
}

/** Closed trades of one month, one cell per day: how many, and how much R.
 * Green/red only ever means the sign of the result.
 */
export function CalendarGrid({
  year,
  month,
  days,
  onShift,
  onSelectDay,
  selectedDay,
}: {
  year: number;
  month: number;
  days: Calendar;
  onShift: (months: number) => void;
  onSelectDay?: (isoDate: string) => void;
  selectedDay?: string | null;
}) {
  // Day 0 of the next month is the last day of this one.
  const dayCount = new Date(Date.UTC(year, month, 0)).getUTCDate();
  const firstWeekday = (new Date(Date.UTC(year, month - 1, 1)).getUTCDay() + 6) % 7;
  const cells = [
    ...Array.from({ length: firstWeekday }, () => null),
    ...Array.from({ length: dayCount }, (_unused, index) => index + 1),
  ];

  return (
    <>
      <div className="mb-3 flex items-center gap-2">
        <button className="btn" aria-label="Previous month" onClick={() => onShift(-1)}>
          ‹
        </button>
        <span className="min-w-40 text-center">
          {MONTHS[month - 1]} {year}
        </span>
        <button className="btn" aria-label="Next month" onClick={() => onShift(1)}>
          ›
        </button>
      </div>
      <div className="grid grid-cols-7 gap-1">
        {WEEKDAYS.map((weekday) => (
          <div key={weekday} className="label text-center">
            {weekday}
          </div>
        ))}
        {cells.map((day, index) => {
          if (day === null) return <div key={`blank-${index}`} />;
          const key = monthKey(year, month, day);
          const entry = days[key];
          const selected = selectedDay === key;
          return (
            <button
              key={key}
              type="button"
              onClick={() => onSelectDay?.(key)}
              className={`h-16 rounded border p-1 text-left align-top ${
                selected ? "border-accent" : "border-line"
              } ${onSelectDay === undefined ? "cursor-default" : "hover:bg-surface2"}`}
            >
              <span className="text-[11px] text-muted">{day}</span>
              {entry !== undefined && (
                <span className="block leading-tight">
                  <span className="block text-[11px] text-muted">
                    {num(entry.count, 0)} {entry.count === 1 ? "trade" : "trades"}
                  </span>
                  <span className={`block ${signClass(entry.r)}`}>{r(entry.r)}</span>
                </span>
              )}
            </button>
          );
        })}
      </div>
    </>
  );
}
