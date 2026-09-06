import { eur, r, signClass } from "../fmt";

/** Money and R values. `sign` turns on the only green/red in the app. */
export function MoneyCell({
  value,
  unit = "eur",
  sign = false,
}: {
  value: string | number | null | undefined;
  unit?: "eur" | "r";
  sign?: boolean;
}) {
  return (
    <span className={sign ? signClass(value) : undefined}>
      {unit === "r" ? r(value) : eur(value)}
    </span>
  );
}
