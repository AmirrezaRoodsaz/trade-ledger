import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { Layout } from "./components/Layout";
import { Account } from "./pages/Account";
import { Analytics } from "./pages/Analytics";
import { BotDetail } from "./pages/BotDetail";
import { Bots } from "./pages/Bots";
import { Dashboard } from "./pages/Dashboard";
import { Journal } from "./pages/Journal";
import { Portfolio } from "./pages/Portfolio";
import { Presets } from "./pages/Presets";
import { Reports } from "./pages/Reports";
import { Settings } from "./pages/Settings";
import { Steuer } from "./pages/Steuer";

export function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route element={<Layout />}>
          <Route index element={<Dashboard />} />
          <Route path="accounts/:id" element={<Account />} />
          <Route path="journal" element={<Journal />} />
          <Route path="analytics" element={<Analytics />} />
          <Route path="portfolio" element={<Portfolio />} />
          <Route path="steuer" element={<Steuer />} />
          <Route path="reports" element={<Reports />} />
          <Route path="bots" element={<Bots />} />
          {/* the literal path first — "presets" would otherwise read as a slug */}
          <Route path="bots/presets" element={<Presets />} />
          <Route path="bots/:slug" element={<BotDetail />} />
          <Route path="settings" element={<Settings />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Route>
      </Routes>
    </BrowserRouter>
  );
}
