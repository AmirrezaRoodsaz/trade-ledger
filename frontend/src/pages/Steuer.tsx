import { useState } from "react";
import { useApi } from "../api/client";
import {
  EXPORT_FORMATS,
  taxAnlage,
  taxDisposals,
  taxExportUrl,
  taxLots,
  taxSummary,
  taxYears,
  type AnlageResponse,
  type Disposal,
  type Lot,
  type TaxSummary,
} from "../api/tax";
import type { ModeFilter } from "../api/types";
import { AnlagePanel } from "../components/AnlagePanel";
import { Card } from "../components/Card";
import { DataTable } from "../components/DataTable";
import { DisposalsTable } from "../components/DisposalsTable";
import { EmptyState } from "../components/EmptyState";
import { PageHeader } from "../components/Layout";
import { LotsTable } from "../components/LotsTable";
import { MoneyCell } from "../components/MoneyCell";
import { TaxMeter } from "../components/TaxMeter";
import { DASH, eur, num, toNumber } from "../fmt";

// Tax is about real money, so `live` is the default here — as on the backend.
const MODES: ModeFilter[] = ["live", "paper", "demo", "all"];

const TABS = [
  { key: "p23", label: "§ 23" },
  { key: "p20", label: "§ 20" },
  { key: "inv", label: "KAP-INV" },
  { key: "eoy", label: "Jahresendbestände" },
  { key: "venues", label: "Venues" },
  { key: "anlage", label: "Anlage" },
  { key: "warnings", label: "Warnings" },
] as const;

type TabKey = (typeof TABS)[number]["key"];

interface TaxData {
  summary: TaxSummary;
  disposals: Disposal[];
  lots: Lot[];
  anlage: AnlageResponse;
}

async function load(year: number, mode: ModeFilter): Promise<TaxData> {
  const [summary, disposals, lots, anlage] = await Promise.all([
    taxSummary(year, mode),
    taxDisposals(year, mode),
    taxLots(year, mode),
    taxAnlage(year, mode),
  ]);
  return { summary, disposals: disposals.disposals, lots: lots.lots, anlage };
}

function sum(...values: (string | null | undefined)[]): number {
  return values.reduce<number>((total, value) => total + (toNumber(value) ?? 0), 0);
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex justify-between gap-4 border-b border-line py-1 last:border-0">
      <span className="text-muted">{label}</span>
      <span>{value}</span>
    </div>
  );
}

export function Steuer() {
  const currentYear = new Date().getFullYear();
  const [mode, setMode] = useState<ModeFilter>("live");
  const [year, setYear] = useState(currentYear);
  const [tab, setTab] = useState<TabKey>("p23");

  const years = useApi(() => taxYears(mode), [mode]);
  const state = useApi(() => load(year, mode), [year, mode]);

  // The current year is always offerable, even before it has a transaction.
  const yearOptions = [...new Set([currentYear, year, ...(years.data?.years ?? [])])].sort(
    (a, b) => b - a,
  );

  const data = state.data;
  const summary = data?.summary ?? null;
  // `/disposals` returns every regime; the § 23 table shows only its own.
  const p23Disposals = (data?.disposals ?? []).filter((row) => row.regime === "p23");

  return (
    <>
      <PageHeader title="Steuer" subtitle={`Steuerjahr ${year}`} />

      <div className="mb-4 flex flex-wrap items-end gap-3">
        <label className="block">
          <span className="label">Jahr</span>
          <select
            className="field"
            value={year}
            onChange={(event) => setYear(Number(event.target.value))}
          >
            {yearOptions.map((option) => (
              <option key={option} value={option}>
                {option}
              </option>
            ))}
          </select>
        </label>
        <label className="block">
          <span className="label">Mode</span>
          <select
            className="field"
            value={mode}
            onChange={(event) => setMode(event.target.value as ModeFilter)}
          >
            {MODES.map((option) => (
              <option key={option} value={option}>
                {option}
              </option>
            ))}
          </select>
        </label>
        <div className="ml-auto flex flex-wrap items-center gap-2">
          <span className="label">CSV</span>
          {EXPORT_FORMATS.map((format) => (
            <a
              key={format}
              className="btn"
              href={taxExportUrl(year, mode, format)}
              download={`tax-${year}-${format}.csv`}
            >
              {format}
            </a>
          ))}
        </div>
      </div>

      {summary !== null && (
        <p className="mb-4 rounded border border-line bg-surface2 px-3 py-2">
          {summary.disclaimer} · {summary.form_status}
        </p>
      )}

      {state.error !== null && <p className="text-neg">{state.error}</p>}
      {state.error === null && summary === null && (
        <p className="text-muted">{state.loading ? "Loading…" : DASH}</p>
      )}

      {summary !== null && data !== null && (
        <>
          <Card title="Freigrenzen und Freibeträge">
            <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
              <TaxMeter
                label="§ 23 Freigrenze"
                value={summary.p23.net}
                limit={summary.p23.freigrenze}
                warning={
                  summary.p23.exceeded
                    ? `Freigrenze überschritten — der gesamte Gewinn von ${eur(
                        summary.p23.net,
                      )} ist steuerpflichtig, nicht nur der Teil über ${eur(summary.p23.freigrenze)}.`
                    : undefined
                }
              />
              <TaxMeter
                label="§ 22 Nr. 3 Freigrenze"
                value={summary.p22.income}
                limit={summary.p22.freigrenze}
                warning={
                  summary.p22.exceeded
                    ? `Freigrenze überschritten — die gesamten Einnahmen von ${eur(
                        summary.p22.income,
                      )} sind steuerpflichtig, nicht nur der Teil über ${eur(
                        summary.p22.freigrenze,
                      )}.`
                    : undefined
                }
              />
              <TaxMeter
                label="Sparerpauschbetrag"
                value={String(
                  sum(
                    summary.p20.dividends,
                    summary.p20.interest,
                    summary.p20.aktien_gains,
                    summary.p20.termin_gains,
                  ) -
                    sum(summary.p20.aktien_losses, summary.p20.termin_losses),
                )}
                limit={summary.p20.sparerpauschbetrag}
              />
            </div>
          </Card>

          <div
            role="tablist"
            aria-label="Steuer-Ansichten"
            className="mb-4 mt-4 flex flex-wrap gap-1 border-b border-line"
          >
            {TABS.map((entry) => (
              <button
                key={entry.key}
                type="button"
                role="tab"
                aria-selected={tab === entry.key}
                className={`rounded-t border-b-2 px-3 py-1 ${
                  tab === entry.key
                    ? "border-accent text-ink"
                    : "border-transparent text-muted hover:text-ink"
                }`}
                onClick={() => setTab(entry.key)}
              >
                {entry.label}
                {entry.key === "warnings" && summary.warnings.length > 0
                  ? ` (${summary.warnings.length})`
                  : ""}
              </button>
            ))}
          </div>

          {tab === "p23" && (
            <>
              <Card
                title="Veräußerungen (§ 23 EStG)"
                right={
                  <span className="text-muted">
                    {`${num(data.disposals.length - p23Disposals.length, 0)} disposals under other regimes`}
                  </span>
                }
              >
                <DisposalsTable disposals={p23Disposals} />
              </Card>
              <Card
                title="Offene Bestände — Spekulationsfrist"
                className="mt-4"
                right={<span className="text-muted">countdown applies to § 23 assets</span>}
              >
                <LotsTable lots={data.lots} />
              </Card>
            </>
          )}

          {tab === "p20" && (
            <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
              <Card title="Aktien und Termingeschäfte">
                <Row label="Aktiengewinne" value={eur(summary.p20.aktien_gains)} />
                <Row label="Aktienverluste" value={eur(summary.p20.aktien_losses)} />
                <Row label="Gewinne Termingeschäfte" value={eur(summary.p20.termin_gains)} />
                <Row label="Verluste Termingeschäfte" value={eur(summary.p20.termin_losses)} />
                <Row label="Verluste ohne Aktienverluste" value={eur(summary.p20.other_losses)} />
              </Card>
              <Card title="Erträge und Steuern">
                <Row label="Dividenden" value={eur(summary.p20.dividends)} />
                <Row label="Zinsen" value={eur(summary.p20.interest)} />
                <Row label="Sonstige (netto)" value={eur(summary.p20.sonstige_net)} />
                <Row label="Anrechenbare Quellensteuer" value={eur(summary.p20.withholding_tax)} />
                <Row label="Ausländische Kapitalerträge" value={eur(summary.p20.total_foreign)} />
              </Card>
            </div>
          )}

          {tab === "inv" && (
            <Card title="Investmentfonds (Anlage KAP-INV)">
              <DataTable
                rows={summary.inv}
                rowKey={(row) => row.symbol}
                empty="No funds held in this year."
                columns={[
                  { key: "symbol", header: "Fonds", render: (row) => row.symbol },
                  { key: "fund_type", header: "Fondsart", render: (row) => row.fund_type },
                  {
                    key: "tfs",
                    header: "Teilfreistellung",
                    align: "right",
                    render: (row) => `${num(row.teilfreistellung_pct, 0)} %`,
                  },
                  {
                    key: "distributions",
                    header: "Ausschüttungen",
                    align: "right",
                    render: (row) => eur(row.distributions),
                  },
                  {
                    key: "vorab",
                    header: "Vorabpauschale",
                    align: "right",
                    render: (row) => eur(row.vorabpauschale),
                  },
                  {
                    key: "gain",
                    header: "Veräußerungsgewinn",
                    align: "right",
                    render: (row) => eur(row.sale_gain),
                  },
                  {
                    key: "loss",
                    header: "Veräußerungsverlust",
                    align: "right",
                    render: (row) => eur(row.sale_loss),
                  },
                ]}
              />
            </Card>
          )}

          {tab === "eoy" && (
            <Card title={`Jahresendbestände 31.12.${year}`}>
              <DataTable
                rows={summary.eoy_holdings}
                rowKey={(row, index) => `${row.wallet}-${row.symbol}-${index}`}
                empty="Nothing held at year end."
                columns={[
                  { key: "wallet", header: "Wallet", render: (row) => row.wallet },
                  { key: "symbol", header: "Instrument", render: (row) => row.symbol },
                  {
                    key: "quantity",
                    header: "Menge",
                    align: "right",
                    render: (row) => num(row.quantity, 8),
                  },
                  {
                    key: "value",
                    header: "Wert",
                    align: "right",
                    render: (row) => <MoneyCell value={row.value_eur} />,
                  },
                ]}
              />
            </Card>
          )}

          {tab === "venues" && (
            <Card
              title="Venues — DAC8 Abgleich"
              right={<span className="text-muted">what each venue would report</span>}
            >
              <DataTable
                rows={summary.venues}
                rowKey={(row) => row.account}
                empty="No venue activity in this year."
                columns={[
                  { key: "account", header: "Konto", render: (row) => row.account },
                  {
                    key: "proceeds",
                    header: "Bruttoerlöse",
                    align: "right",
                    render: (row) => eur(row.gross_proceeds),
                  },
                  {
                    key: "acquisitions",
                    header: "Anschaffungen",
                    align: "right",
                    render: (row) => eur(row.gross_acquisitions),
                  },
                  {
                    key: "count",
                    header: "Veräußerungen",
                    align: "right",
                    render: (row) => num(row.disposals_count, 0),
                  },
                  {
                    key: "deposits",
                    header: "Einzahlungen",
                    align: "right",
                    render: (row) => eur(row.deposits),
                  },
                  {
                    key: "withdrawals",
                    header: "Auszahlungen",
                    align: "right",
                    render: (row) => eur(row.withdrawals),
                  },
                ]}
              />
            </Card>
          )}

          {tab === "anlage" && (
            <Card title={`Anlage-Zeilen VZ ${data.anlage.vz}`}>
              <AnlagePanel lines={data.anlage.lines} note={data.anlage.note} />
            </Card>
          )}

          {tab === "warnings" && (
            <Card title="Warnings">
              {summary.warnings.length === 0 ? (
                <EmptyState>Nothing flagged for this year.</EmptyState>
              ) : (
                <ul className="list-disc pl-5">
                  {summary.warnings.map((warning) => (
                    <li key={warning} className="py-0.5">
                      {warning}
                    </li>
                  ))}
                </ul>
              )}
            </Card>
          )}
        </>
      )}
    </>
  );
}
