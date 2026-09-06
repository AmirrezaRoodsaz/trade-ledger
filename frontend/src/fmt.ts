/** Every number, money value and date in the UI goes through this module.
 * German formatting throughout: 1.234,56 €, 06.09.2026, +2,50 R.
 */

export const DASH = "—";

type Num = string | number | null | undefined;

const cache = new Map<number, Intl.NumberFormat>();

function formatter(digits: number): Intl.NumberFormat {
  let existing = cache.get(digits);
  if (!existing) {
    existing = new Intl.NumberFormat("de-DE", {
      minimumFractionDigits: digits,
      maximumFractionDigits: digits,
    });
    cache.set(digits, existing);
  }
  return existing;
}

/** Backend money arrives as a string ("1234.56"); null means "not computed". */
export function toNumber(value: Num): number | null {
  if (value === null || value === undefined || value === "") return null;
  const parsed = typeof value === "number" ? value : Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

export function num(value: Num, digits = 2): string {
  const parsed = toNumber(value);
  return parsed === null ? DASH : formatter(digits).format(parsed);
}

export function eur(value: Num, digits = 2): string {
  const parsed = toNumber(value);
  return parsed === null ? DASH : `${formatter(digits).format(parsed)} €`;
}

/** R multiples always carry their sign: +2,50 R / -1,00 R. */
export function r(value: Num, digits = 2): string {
  const parsed = toNumber(value);
  if (parsed === null) return DASH;
  const sign = parsed > 0 ? "+" : parsed < 0 ? "-" : "";
  return `${sign}${formatter(digits).format(Math.abs(parsed))} R`;
}

/** A ratio in 0..1 shown as a percentage: 0.55 → 55,0 %. */
export function pct(value: Num, digits = 1): string {
  const parsed = toNumber(value);
  return parsed === null ? DASH : `${formatter(digits).format(parsed * 100)} %`;
}

export function date(iso: string | null | undefined): string {
  if (!iso) return DASH;
  // A date-only string is a calendar date, not an instant: reorder it here
  // rather than let `new Date` drag it a day back in a negative-offset zone.
  const dateOnly = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso);
  if (dateOnly) return `${dateOnly[3]}.${dateOnly[2]}.${dateOnly[1]}`;
  const parsed = new Date(iso);
  if (Number.isNaN(parsed.getTime())) return DASH;
  return parsed.toLocaleDateString("de-DE", {
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
  });
}

export function dateTime(iso: string | null | undefined): string {
  if (!iso) return DASH;
  const parsed = new Date(iso);
  if (Number.isNaN(parsed.getTime())) return DASH;
  return `${date(iso)} ${parsed.toLocaleTimeString("de-DE", {
    hour: "2-digit",
    minute: "2-digit",
  })}`;
}

/** ISO date (YYYY-MM-DD) for query params. */
export function isoDate(value: Date): string {
  return value.toISOString().slice(0, 10);
}

/** Sign class for P&L colouring — green/red are only ever used for this. */
export function signClass(value: Num): string {
  const parsed = toNumber(value);
  if (parsed === null || parsed === 0) return "text-ink";
  return parsed > 0 ? "text-pos" : "text-neg";
}
