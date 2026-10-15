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
