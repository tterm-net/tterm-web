// How long ago, in words: "away for 3 minutes", "last seen 2 hours ago".

/** "less than a minute", "a minute", "14 minutes", "an hour", "3 hours",
 * "a day", "5 days": the time since `ms`, rounded down. */
export function since(ms: number | null | undefined): string {
  if (!ms) return "";
  const minutes = Math.floor((Date.now() - ms) / 60000);
  if (minutes < 1) return "less than a minute";
  if (minutes < 60) return minutes === 1 ? "a minute" : `${minutes} minutes`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return hours === 1 ? "an hour" : `${hours} hours`;
  const days = Math.floor(hours / 24);
  return days === 1 ? "a day" : `${days} days`;
}

/** "last seen 14 minutes ago", or nothing if it never was. */
export function lastSeen(ms: number | null | undefined): string {
  if (!ms) return "";
  const s = since(ms);
  return s === "less than a minute" ? "last seen just now" : `last seen ${s} ago`;
}

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep",
  "Oct", "Nov", "Dec"];

/** "10:02", as the feed shows times. */
export function clock(ms: number): string {
  const d = new Date(ms);
  return `${String(d.getHours()).padStart(2, "0")}:` +
    `${String(d.getMinutes()).padStart(2, "0")}`;
}

/** "12 Oct", and "12 Oct 2025" in another year. */
export function day(ms: number): string {
  const d = new Date(ms);
  const year = d.getFullYear() === new Date().getFullYear() ? "" : ` ${d.getFullYear()}`;
  return `${d.getDate()} ${MONTHS[d.getMonth()]}${year}`;
}

function sameDay(a: Date, b: Date): boolean {
  return a.toDateString() === b.toDateString();
}

/** Calendar days from `ms` to today: 0 today, 1 yesterday. */
function daysBack(ms: number): number {
  const then = new Date(ms);
  const today = new Date();
  then.setHours(12, 0, 0, 0);
  today.setHours(12, 0, 0, 0);
  return Math.round((today.getTime() - then.getTime()) / 86_400_000);
}

/** When, in a few words, as lists of notes say it: "just now", "5 min
 * ago", "2 h ago", "yesterday", "3 days ago", "last week", "2 weeks ago",
 * "on 12 Oct". */
export function ago(ms: number | null | undefined): string {
  if (!ms) return "";
  const minutes = Math.floor((Date.now() - ms) / 60000);
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.floor(minutes / 60);
  const days = daysBack(ms);
  if (days === 0 || hours < 6) return `${hours} h ago`;
  if (days === 1) return "yesterday";
  if (days < 7) return `${days} days ago`;
  if (days < 14) return "last week";
  if (days < 56) return `${Math.floor(days / 7)} weeks ago`;
  return `on ${day(ms)}`;
}

/** The day, in a few words: "today", "yesterday", "on 12 Oct". */
export function onDay(ms: number | null | undefined): string {
  if (!ms) return "";
  const days = daysBack(ms);
  return days === 0 ? "today" : days === 1 ? "yesterday" : `on ${day(ms)}`;
}

/** A time today, a day before: "10:02", "12 Oct". */
export function stamp(ms: number): string {
  return sameDay(new Date(ms), new Date()) ? clock(ms) : day(ms);
}
