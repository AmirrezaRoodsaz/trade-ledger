`npm install` — install dependencies (Node 22+).
`npm run dev` — dev server on :5173, proxying `/api` to the backend on :8642.
`npm run build` — type-check (`tsc --noEmit`) and build into `frontend/dist`, which the backend serves.

Pages: Dashboard, Account, Journal, Analytics, Portfolio, Steuer, Reports,
Bots (`/bots` fleet, `/bots/:slug` detail, `/bots/presets`) and Settings.
