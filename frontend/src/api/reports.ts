/** `POST /api/reports/weekly`, `GET /api/reports` — the weekly PDF.
 *
 * The reports router lands in its own task; until then every call 404s and the
 * page says so instead of showing an error.
 */

import { get, post } from "./client";
import type { ModeFilter } from "./types";

export interface ReportRef {
  path: string;
  url: string;
  name: string;
}

export interface ReportFile {
  name: string;
  size: number;
  created: string;
}

export const listReports = () => get<ReportFile[]>("/reports");

export const generateWeekly = (weekEnd: string, mode: ModeFilter) =>
  post<ReportRef>("/reports/weekly", { week_end: weekEnd, mode });

export const reportUrl = (name: string) => `/api/reports/${encodeURIComponent(name)}`;
