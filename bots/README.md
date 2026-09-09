# Running a bot

**Writing a new bot: give [`bots/BOT_CONTRACT.md`](BOT_CONTRACT.md) to whoever
writes it.** It is self-contained — the loop in order, every endpoint with its
JSON, the strategy signature, the env vars, the kill rules a bot honours
locally, and the acceptance tests it has to pass. `trade-bot new <name>`
scaffolds a strategy module from `bots/template/strategy_template.py` and an
env file from `bots/template/bot.env.example`.

This file is the operator's side: how to create a bot, run it, and schedule it.

A bot is a separate process. It reads its config from the app, pushes what it
did back to the app, and is the only thing in this repo allowed to talk to an
exchange. The app never places an order.

## 1. Create the bot in the UI

**Bots → New bot.** Name, account, strategy, host (`local` if the app's
supervisor should launch it, `remote` for a VPS), schedule.

The token is shown **once**, on creation and on rotate. Copy it now; the app
only keeps its sha256.

Assign a preset version too (**Bots → Presets**). Without one the bot has no
pairs and finishes every run as `skipped`.

## 2. Write the env file

```bash
mkdir -p data/bots/<slug>
cp bots/template/bot.env.example data/bots/<slug>/.env
chmod 600 data/bots/<slug>/.env
$EDITOR data/bots/<slug>/.env      # BOT_TOKEN, exchange keys
```

(`trade-bot new <name>` writes that file for you, already `chmod 600`.)

`data/` is gitignored. Nothing in the repo *trades* with these keys except the
bot process. The supervisor does read the file — that is how a local bot's
child process gets its environment — but it neither logs nor stores what it
read, and the app cannot place an order with it: it never imports the exchange
wrapper, and a test enforces that.

Keys: read + trade, **no withdrawal**, IP-restricted to the machine the bot
runs on. Give each bot its own venue sub-account where the venue offers one —
on spot the bot reads its position off the base-currency balance, so a coin
you bought by hand in the same account looks to it like a position it opened
and shows up as a reconciliation mismatch.

## 3. Run it locally

```bash
uv run trade-bot --bot <slug> --dry-run
```

A dry run does everything except order: it reconciles, applies commands,
computes signals and sizes, files the planned trade in the journal, and
finishes the run as `dry_run`. Read it in the UI under **Bots → the bot →
Runs**.

No keys yet? `EXCHANGE_ID=fake uv run trade-bot --bot <slug> --dry-run`
smoke-tests the loop against a flat synthetic market — no signals, no orders,
but every call to the app is real. `fake` is refused without `--dry-run`.

Drop `--dry-run` when the demo account is set up and the preset is the one you
mean:

```bash
uv run trade-bot --bot <slug>
```

Exit code `0` for a run that finished `ok`, `dry_run` or `skipped`, `1` for
anything else.

## 4. Let the app schedule it (host = local)

A `local` bot is launched by the app's supervisor on its own schedule; start
the app with `trade-ledger serve` and leave it running. **Run now** in the UI
fires one off immediately. Logs land in `data/bots/<slug>/runs/<id>.log` and
are linked from the run.

## 5. Or a VPS (host = remote)

```bash
sudo install -d -m 700 /etc/trade-bot
sudo install -m 600 data/bots/<slug>/.env /etc/trade-bot/<slug>.env
sudo cp bots/systemd/trade-bot@.* /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now trade-bot@<slug>.timer
```

The timer fires at `00:05, 04:05, …` UTC — five minutes after each 4-hour bar
closes. Check it with `systemctl list-timers 'trade-bot@*'` and
`journalctl -u trade-bot@<slug>.service -n 50`.

**Keep the timer and the bot's `schedule_every_s` in step.** The app derives
its heartbeat deadline from `schedule_every_s + grace_s`; a timer that fires
less often than the bot's registry setting says will trip kill rule K5 (no
heartbeat) and mark the bot `stale` — every four hours, in the shipped units,
so `schedule_every_s` must be `14400`. Change one, change the other.

`TRADE_LEDGER_URL` in the env file must point at the app over the network, and
that endpoint must be TLS — the bot token travels on every request.

**Exposing the app is the whole security decision.** Every route except the
bot-token push endpoints is unauthenticated: the app was written to bind
`127.0.0.1` and be the only thing on the machine. The moment a VPS can reach
it, anything else that can reach it owns the journal, the tax data and the
control buttons. So put it behind TLS *and* an access control that is not the
bot token — a VPN, an SSH tunnel, or a reverse proxy with its own
authentication and an IP allow-list — and treat the bot token as a bot's
identity, never as the door. And keep the timer's `OnCalendar` in step with the
bot's `schedule_every_s`: a timer that fires less often than the registry says
trips K5 every cycle.

## What the bot refuses to do

- **No app, no order.** Every entry is journalled before the order exists, and
  the trade's `external_ref` is the order's `clientOrderId`. If the app is
  unreachable the client raises before anything reaches the venue.
- **No retry after an order.** A timeout once an order is out is *unknown*,
  not *failed*. The bot logs it, finishes the run, and lets the next run's
  reconciliation find out what really happened. It never sends the same order
  twice: a re-run of the same bar computes the same journal ref, finds the
  trade it already planned and reuses it.
- **No trading on a mismatch.** If the app's open trades and the venue's
  positions disagree, the run stops with `error`. `on_mismatch` in the
  preset's params decides how: `halt` (the default) stops and leaves
  everything alone for you to look at; `flat` cancels every resting order and
  closes every position first. Either way the app is told, and kill rule K4
  raises a critical alert.
- **No trading after a commanded flat.** A `flat` command cancels every
  resting order, closes every position, **closes the bot's open journal trades
  at the flatten's own fill price**, pushes state and ends the run there. It
  does not go on to compute signals: the positions it would trade on no longer
  exist, and an exit order against one of them would open a naked short.
- **No entries while paused.** A `pause` or `flat` command, or
  `paused_entries` on the bot, skips entries — exits and stops still run. A
  disabled bot finishes `skipped` without touching the venue.
- **No position without a stop.** The stop is placed immediately after the
  entry fill, before the journal is even updated, and `stop_present` on the
  pushed state is what kill rule K4 watches. If the venue refuses the stop,
  the position is closed again on the spot and the plan is cancelled — the bot
  would rather be flat than unprotected, and a cancelled plan is what stops the
  next run of the same bar from entering all over again.

## Sizing, and what it currently assumes

Size is `risk / stop distance`, capped by `max_position_pct` and by
`leverage_cap`, rounded **down** to the venue's lot step. Below the minimum
order size the entry is skipped with a warning event rather than shrunk.

The equity behind that risk is the venue's EUR balance when every pair settles
in EUR. Otherwise — a USDT-quoted pair, the usual case — the bot has no FX
rate (that lives in the app) and falls back on the bot's `stage_capital_eur`,
treating 1 USDT as 1 EUR for the stop distance. Sizes are therefore off by the
EUR/USD rate on those pairs; in demo that is noise, before real money it is
not. The state push says which base was used (`extra.sizing_base_source`).
