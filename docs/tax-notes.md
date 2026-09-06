# Tax notes

Every legal fact the tax module relies on, with the date it was checked and a
flag where it came from a secondary source.

> **Berechnung — mit Steuerberater prüfen.** This file documents what the code
> assumes. It is not tax advice, and it is not a substitute for reading the law
> or the official forms.

**Checked 2026-09-06.** **Formstand: VZ 2025.**

**VERIFY, in general:** the paragraph numbers, Randziffern, rates and form line
numbers below were taken from **secondary sources** — commentary, tax-software
documentation and summaries — not from the Gesetzblatt, the BMF letters
themselves or the official form PDFs. They are good enough to compute with and
not good enough to file with unchecked. Where a fact is especially load-bearing
it is marked **VERIFY** again inline.

---

## Crypto: the BMF letter of 06.03.2025

The BMF letter of **6 March 2025** on the income tax treatment of Kryptowerte
is the basis for how the engine classifies crypto. What the code takes from it:

- Crypto held in a spot account or a private wallet is a **private asset**, so a
  disposal is a privates Veräußerungsgeschäft under **§ 23 EStG**.
- **FIFO is applied per wallet** — each address or exchange account is its own
  pool. `tax/fifo.py` keys pools on `(tax_wallet, instrument)` for exactly this
  reason, which is also why `accounts.tax_wallet` exists as a separate field
  from the account name.
- Staking rewards and airdrops received **for a service** are sonstige
  Leistungen under **§ 22 Nr. 3 EStG**.
- Crypto derivatives, perpetuals and CFDs are **Termingeschäfte** under
  **§ 20 Abs. 2 EStG**, not § 23.

**VERIFY:** the Randziffern usually quoted for each of these points come from
secondary sources. Do not cite an Rn. number from this repo without checking
the letter.

## § 23 EStG — private Veräußerungsgeschäfte

- **Twelve-month holding period.** A disposal is tax-free once the asset was
  held **more than** one year. `tax/fifo.py` therefore compares strictly:
  `sale_date > acquired + 1 year`. A sale exactly one year later is still
  taxable.
- **Freigrenze 1.000 €** from VZ 2024 onwards (raised from 600 €). It is a
  Freigrenze, not a Freibetrag: **all-or-nothing**. At 999 € nothing is taxed;
  at 1.000 € the whole amount is. `year_summary.P23_FREIGRENZE`.
- **Acquisition fees** are Anschaffungsnebenkosten and raise the cost basis.
  **Disposal fees** are Werbungskosten and are deducted from the proceeds. The
  summary keeps them on a separate line because Anlage SO wants them there.
- A transfer between the taxpayer's own wallets does **not** restart the
  holding period; the lot carries its acquisition date across.

## § 22 Nr. 3 EStG — sonstige Leistungen

- Staking rewards and airdrops received for a service.
- **Freigrenze 256 €**, likewise **all-or-nothing**.
  `year_summary.P22_FREIGRENZE`.
- An airdrop with **no market value on receipt** is not a taxable Leistung; the
  engine classifies it as `none`.
- **Known gap:** `werbungskosten` under § 22 is always 0 in this code, because
  the ledger has no expense row that can be attached to a Leistung.

## § 20 EStG — Kapitalerträge

- Separate loss pots: **share losses** may only be offset against share gains
  (§ 20 Abs. 6 Satz 4). The summary reports `aktien_losses` separately from
  `other_losses` (Termin losses plus fund sale losses) for this reason.
- **Sparerpauschbetrag 1.000 €** (single). The engine reports it but does not
  apply it — the Finanzamt does.
- No Werbungskosten are deductible under § 20 Abs. 9, only the
  Sparerpauschbetrag. Transaction fees are netted into the gain itself
  (§ 20 Abs. 4), unlike § 23 where they get their own form line.
- **JStG 2024 repealed the Termingeschäft loss cap** (the 20.000 €/year
  restriction of § 20 Abs. 6 Satz 5 EStG) **from VZ 2025 onwards**. The engine
  applies no cap. **VERIFY** — this was politically contested and the effective
  date matters.

## InvStG — funds and ETFs

- Reported on **Anlage KAP-INV**, not Anlage KAP.
- **Teilfreistellung** by fund type: Aktienfonds 30 %, Mischfonds 15 %,
  Immobilienfonds 60 %, Auslands-Immobilienfonds 80 %, sonstige 0 %
  (`year_summary.TEILFREISTELLUNG`). The engine reports **gross** figures — the
  form takes gross and the Finanzamt applies the Teilfreistellung.
- **Vorabpauschale** (§ 18 InvStG): the Basisertrag is **70 % of the Basiszins**
  on the value of the fund at the beginning of the year; the Vorabpauschale is
  that Basisertrag **capped at the actual increase in value** over the year and
  **reduced by the distributions** already received. Never negative.
- For a fund acquired during the year the Basisertrag is reduced by **one
  twelfth for each month before the month of acquisition** (§ 18 Abs. 2 InvStG).

### Basiszins

| Year | Basiszins | Source |
|---|---|---|
| 2024 | 2,29 % | BMF |
| 2025 | 2,53 % | BMF letter of 10.01.2025 |
| 2026 | 3,20 % | BMF letter of 13.01.2026 |

**VERIFY:** these rates *and the letter dates* come from secondary sources. A
`Setting` row `basiszins_<year>` overrides the table without a code change, so
correcting one is a UI edit. A year with no Basiszins yields a Vorabpauschale of
0 **and a warning** rather than a silent zero.

## Form line numbers — VZ 2025

**VERIFY: every number below came from secondary sources describing the 2025
forms, not from the official PDFs.** Check them against the paper before
filing. They live in `tax/forms.py`, one `FORMS[year]` list, so a correction is
a data edit.

**Anlage SO**

| Zeile | Field |
|---|---|
| 14 | Sonstige Leistungen (§ 22 Nr. 3 EStG) — checkbox |
| 15 | Einnahmen |
| 19 | Werbungskosten |
| 20 | Gewinn / Verlust |
| 45 | Veräußerungsgeschäfte mit virtuellen Währungen und sonstigen Token — checkbox |
| 46 | Bezeichnung des Wirtschaftsguts |
| 47 | Zeitpunkt der Anschaffung / Veräußerung |
| 48 | Veräußerungspreis |
| 49 | Anschaffungskosten |
| 50 | Werbungskosten |
| 51 | Gewinn / Verlust |

**Anlage KAP**

| Zeile | Field |
|---|---|
| 19 | Ausländische Kapitalerträge |
| 20 | darin enthaltene Gewinne aus Aktienveräußerungen |
| 22 | darin enthaltene Verluste ohne Aktienverluste |
| 23 | darin enthaltene Verluste aus Aktienveräußerungen |
| 41 | Anrechenbare ausländische Steuern |

**Anlage KAP-INV** — four blocks, each one line per fund type in the order
Aktien, Misch, Immo, Immo-Ausland, sonstige:

| Zeilen | Block |
|---|---|
| 4–8 | Ausschüttungen |
| 9–13 | Vorabpauschalen |
| 14–18 | Gewinne aus der Veräußerung |
| 19–23 | Verluste aus der Veräußerung |

A year with no mapping returns an empty line list and the note
`no line mapping for VZ <year>` rather than guessing at another year's layout.

## DAC8 / KStTG — from 2026

From **2026**, crypto service providers report their users' transactions to the
tax authorities under **DAC8**, implemented in Germany by the
**Kryptowerte-Steuertransparenzgesetz (KStTG)**. The practical consequence for
this app: the Finanzamt will hold a per-venue view of gross proceeds and
acquisitions, so the year summary produces a **venue reconciliation table**
(`summary.venues`) with exactly those figures per account — gross proceeds,
gross acquisitions, disposal count, deposits, withdrawals — so a mismatch can be
found before it becomes a letter.

**VERIFY:** the name, the implementing act and the start date come from
secondary sources.

## Export templates

`tax/exports.py` writes Blockpit and CoinTracking import CSVs. Their column
names and order were checked 2026-09-06 against **secondary sources**.
**VERIFY** against the tool's current template before trusting an import.

## What the engine deliberately does not do

- It does not apply the Teilfreistellung, the Sparerpauschbetrag or any tax
  rate. It reports the inputs; the Finanzamt computes the tax.
- It does not decide the Freigrenze outcome for you beyond the arithmetic — it
  reports `net`, `freigrenze` and `exceeded`.
- It does not file, submit or pre-fill anything.
- It warns instead of guessing. A missing close, a missing Basiszins, an
  unlinked transfer, a disposal with no regime and a pending FX value each
  produce a warning string on the year summary, surfaced in the UI and in the
  CLI output.
