/**
 * Returns the YYYY-MM-DD calendar date in the given IANA timezone.
 * Falls back to UTC if the timezone identifier is invalid.
 */
export function getTodayInTimezone(
  timeZone: string = 'Asia/Taipei',
  now: Date = new Date(),
): string {
  let formatter: Intl.DateTimeFormat;
  try {
    formatter = new Intl.DateTimeFormat('en-CA', {
      timeZone: timeZone || 'Asia/Taipei',
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
    });
  } catch {
    formatter = new Intl.DateTimeFormat('en-CA', {
      timeZone: 'UTC',
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
    });
  }
  const parts = formatter.formatToParts(now);
  const year = parts.find((p) => p.type === 'year')?.value ?? '1970';
  const month = parts.find((p) => p.type === 'month')?.value ?? '01';
  const day = parts.find((p) => p.type === 'day')?.value ?? '01';
  return `${year}-${month}-${day}`;
}

/**
 * Selects the newest report (maximum `generated_at`) from a list of reports.
 * The API returns a day's reports ordered by `generated_at` ascending.
 */
export function getNewestReport<T extends { generated_at: string }>(
  reports: readonly T[],
): T | null {
  if (reports.length === 0) {
    return null;
  }
  return reports.reduce((newest, candidate) => {
    const newestTime = Date.parse(newest.generated_at);
    const candidateTime = Date.parse(candidate.generated_at);
    if (Number.isFinite(newestTime) && Number.isFinite(candidateTime)) {
      return candidateTime >= newestTime ? candidate : newest;
    }
    return candidate.generated_at >= newest.generated_at ? candidate : newest;
  });
}
