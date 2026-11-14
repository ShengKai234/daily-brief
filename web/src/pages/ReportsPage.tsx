import React, { useCallback, useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { ApiError, apiClient } from '../api/client';
import { DEFAULT_REPORT_DATE } from '../api/mockApi';
import type { Report, ReportItem, ReportSummary, UserJob } from '../api/types';
import { useAuth } from '../auth/AuthContext';

const DEFAULT_POLL_INTERVAL_MS = 5000;
const MAX_POLL_DURATION_MS = 15 * 60 * 1000;

function getPollIntervalMs(): number {
  const configured = Number(import.meta.env.VITE_JOB_POLL_INTERVAL_MS);
  if (Number.isFinite(configured) && configured > 0) {
    return configured;
  }
  return DEFAULT_POLL_INTERVAL_MS;
}

interface ItemNoteEditorProps {
  item: ReportItem;
  saving: boolean;
  onSaveMark: (itemId: string, read: boolean, note: string | null) => Promise<void>;
}

function ReportItemCard({ item, saving, onSaveMark }: ItemNoteEditorProps) {
  const serverRead = Boolean(item.mark?.read_at);
  const [optimisticRead, setOptimisticRead] = useState<boolean>(serverRead);
  const savedNote = item.mark?.note ?? '';
  const [noteDraft, setNoteDraft] = useState<string>(savedNote);
  const [editingNote, setEditingNote] = useState<boolean>(false);

  useEffect(() => {
    setOptimisticRead(Boolean(item.mark?.read_at));
  }, [item.mark?.read_at]);

  useEffect(() => {
    setNoteDraft(item.mark?.note ?? '');
  }, [item.mark?.note]);

  const handleToggleRead = async () => {
    const nextRead = !optimisticRead;
    setOptimisticRead(nextRead);
    await onSaveMark(item.id, nextRead, item.mark?.note ?? null);
  };

  const handleSaveNote = async (e: React.FormEvent) => {
    e.preventDefault();
    const cleaned = noteDraft.trim();
    await onSaveMark(item.id, optimisticRead, cleaned.length > 0 ? cleaned : null);
    setEditingNote(false);
  };

  const handleClearNote = async () => {
    setNoteDraft('');
    await onSaveMark(item.id, optimisticRead, null);
    setEditingNote(false);
  };

  return (
    <article
      className={`card ${optimisticRead ? 'item-card-read' : ''}`}
      aria-labelledby={`item-title-${item.id}`}
    >
      <div className="row-between">
        <span className="badge">#{item.rank}</span>
        <span className="text-subtle">
          <a href={item.source_url} target="_blank" rel="noopener noreferrer">
            {item.source_name}
          </a>
          {item.published_at ? ` · ${item.published_at.slice(0, 16).replace('T', ' ')} UTC` : ''}
        </span>
      </div>

      <h3 id={`item-title-${item.id}`} className="item-title">
        {item.title}
      </h3>

      <p className="text-muted">{item.summary}</p>

      {savedNote ? (
        <div className="stack">
          <span className="text-subtle">Your Note (plain text):</span>
          <p className="plain-note" data-testid={`note-display-${item.id}`}>
            {savedNote}
          </p>
        </div>
      ) : null}

      <div className="row-between">
        <label className="checkbox-label" htmlFor={`read-toggle-${item.id}`}>
          <input
            id={`read-toggle-${item.id}`}
            type="checkbox"
            checked={optimisticRead}
            disabled={saving}
            onChange={() => void handleToggleRead()}
          />
          <span>{optimisticRead ? 'Marked as read' : 'Mark as read'}</span>
        </label>

        <button
          type="button"
          className="btn"
          disabled={saving}
          onClick={() => setEditingNote((prev) => !prev)}
        >
          {editingNote ? 'Close Note Editor' : savedNote ? 'Edit Note' : 'Add Note'}
        </button>
      </div>

      {editingNote ? (
        <form className="stack" onSubmit={(e) => void handleSaveNote(e)}>
          <div className="form-group">
            <label htmlFor={`note-input-${item.id}`} className="form-label">
              Note for {item.title}
            </label>
            <textarea
              id={`note-input-${item.id}`}
              className="textarea"
              maxLength={2000}
              value={noteDraft}
              onChange={(e) => setNoteDraft(e.target.value)}
              placeholder="Write a plain-text note..."
            />
          </div>
          <div className="row">
            <button type="submit" className="btn btn-primary" disabled={saving}>
              Save Note
            </button>
            {savedNote ? (
              <button
                type="button"
                className="btn"
                disabled={saving}
                onClick={() => void handleClearNote()}
              >
                Delete Note
              </button>
            ) : null}
          </div>
        </form>
      ) : null}
    </article>
  );
}

export function ReportsPage() {
  const { token, profile } = useAuth();
  const [selectedDate, setSelectedDate] = useState<string>(DEFAULT_REPORT_DATE);
  const [summaries, setSummaries] = useState<ReportSummary[]>([]);
  const [selectedReportId, setSelectedReportId] = useState<string | null>(null);
  const [report, setReport] = useState<Report | null>(null);
  const [loadingList, setLoadingList] = useState<boolean>(true);
  const [loadingReport, setLoadingReport] = useState<boolean>(false);
  const [savingItemId, setSavingItemId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const [submittingJob, setSubmittingJob] = useState<boolean>(false);
  const [activeJob, setActiveJob] = useState<UserJob | null>(null);
  const [pollStartedAt, setPollStartedAt] = useState<number | null>(null);
  const [pollTick, setPollTick] = useState<number>(0);
  const [jobErrorCode, setJobErrorCode] = useState<string | null>(null);
  const [jobErrorMessage, setJobErrorMessage] = useState<string | null>(null);
  const preferredReportIdRef = useRef<string | null>(null);

  const isJobActive =
    submittingJob || activeJob?.status === 'queued' || activeJob?.status === 'running';

  const loadReportsForDate = useCallback(
    async (dateStr: string, preferredReportId?: string) => {
      if (!token) {
        return;
      }
      setLoadingList(true);
      setError(null);
      try {
        const res = await apiClient.listReports(token, dateStr);
        setSummaries(res.reports);
        const targetId = preferredReportId ?? preferredReportIdRef.current;
        if (targetId && res.reports.some((r) => r.id === targetId)) {
          preferredReportIdRef.current = null;
          setSelectedReportId(targetId);
        } else if (res.reports.length > 0) {
          setSelectedReportId(res.reports[0].id);
        } else {
          setSelectedReportId(null);
          setReport(null);
        }
      } catch (err) {
        if (err instanceof ApiError) {
          setError(`${err.problem.detail} (${err.problem.code})`);
        } else {
          setError(err instanceof Error ? err.message : 'Failed to load reports.');
        }
      } finally {
        setLoadingList(false);
      }
    },
    [token],
  );

  useEffect(() => {
    void loadReportsForDate(selectedDate);
  }, [selectedDate, loadReportsForDate]);

  useEffect(() => {
    if (!token || !selectedReportId) {
      return;
    }
    let cancelled = false;
    const fetchDetail = async () => {
      setLoadingReport(true);
      setError(null);
      try {
        const loaded = await apiClient.getReport(token, selectedReportId);
        if (!cancelled) {
          setReport(loaded);
        }
      } catch (err) {
        if (!cancelled) {
          if (err instanceof ApiError) {
            setError(`${err.problem.detail} (${err.problem.code})`);
          } else {
            setError(err instanceof Error ? err.message : 'Failed to load report details.');
          }
        }
      } finally {
        if (!cancelled) {
          setLoadingReport(false);
        }
      }
    };
    void fetchDetail();
    return () => {
      cancelled = true;
    };
  }, [token, selectedReportId]);

  useEffect(() => {
    if (!token || !activeJob || !pollStartedAt) {
      return;
    }
    if (activeJob.status !== 'queued' && activeJob.status !== 'running') {
      return;
    }

    let cancelled = false;
    const intervalMs = getPollIntervalMs();

    const timerId = window.setTimeout(async () => {
      if (cancelled) {
        return;
      }
      if (Date.now() - pollStartedAt >= MAX_POLL_DURATION_MS) {
        setActiveJob(null);
        setPollStartedAt(null);
        setJobErrorCode('poll_timeout');
        setJobErrorMessage('Brief generation timed out after 15 minutes.');
        return;
      }

      try {
        const updated = await apiClient.getJob(token, activeJob.id);
        if (cancelled) {
          return;
        }
        setActiveJob(updated);
        if (updated.status === 'queued' || updated.status === 'running') {
          setPollTick((prev) => prev + 1);
        } else if (updated.status === 'succeeded') {
          setPollStartedAt(null);
          if (updated.report_id) {
            preferredReportIdRef.current = updated.report_id;
            const loaded = await apiClient.getReport(token, updated.report_id);
            if (!cancelled) {
              if (loaded.report_date !== selectedDate) {
                setSelectedDate(loaded.report_date);
              } else {
                await loadReportsForDate(loaded.report_date, loaded.id);
              }
              setSelectedReportId(loaded.id);
              setReport(loaded);
            }
          } else {
            await loadReportsForDate(selectedDate);
          }
        } else if (updated.status === 'failed') {
          setPollStartedAt(null);
          const code = updated.error_code || 'worker_failed';
          setJobErrorCode(code);
          setJobErrorMessage(`Brief generation failed (${code}).`);
        }
      } catch (err) {
        if (!cancelled) {
          setActiveJob(null);
          setPollStartedAt(null);
          if (err instanceof ApiError) {
            setJobErrorCode(err.problem.code);
            setJobErrorMessage(`${err.problem.detail} (${err.problem.code})`);
          } else {
            setJobErrorMessage(err instanceof Error ? err.message : 'Failed to poll job status.');
          }
        }
      }
    }, intervalMs);

    return () => {
      cancelled = true;
      window.clearTimeout(timerId);
    };
  }, [token, activeJob, pollStartedAt, pollTick, selectedDate, loadReportsForDate]);

  const handleGenerateNow = async () => {
    if (!token || isJobActive) {
      return;
    }
    setSubmittingJob(true);
    setJobErrorCode(null);
    setJobErrorMessage(null);
    setError(null);
    try {
      const created = await apiClient.generateNow(token);
      setActiveJob(created);
      setPollStartedAt(Date.now());
      setPollTick(0);
    } catch (err) {
      setActiveJob(null);
      setPollStartedAt(null);
      if (err instanceof ApiError) {
        const code = err.problem.code;
        setJobErrorCode(code);
        if (code === 'job_in_progress') {
          setJobErrorMessage(
            'A brief generation job is already in progress. Please wait for it to finish.',
          );
        } else if (code === 'daily_limit_reached') {
          setJobErrorMessage('Daily limit reached, try tomorrow.');
        } else if (code === 'no_topics') {
          setJobErrorMessage('No enabled topic subscriptions found.');
        } else if (code === 'owner_only') {
          setJobErrorMessage(
            'On-demand generation with the subscription runner is restricted to the owner account (owner_only).',
          );
        } else {
          setJobErrorMessage(`${err.problem.detail} (${code})`);
        }
      } else {
        setJobErrorMessage(err instanceof Error ? err.message : 'Failed to start brief generation.');
      }
    } finally {
      setSubmittingJob(false);
    }
  };

  const handleSaveMark = async (itemId: string, read: boolean, note: string | null) => {
    if (!token || !report) {
      return;
    }
    setSavingItemId(itemId);
    setError(null);
    try {
      const updatedMark = await apiClient.setItemMark(token, itemId, { read, note });
      const updatedSections = report.sections.map((section) => ({
        ...section,
        items: section.items.map((item) =>
          item.id === itemId ? { ...item, mark: updatedMark } : item,
        ),
      }));
      const nextReport: Report = {
        ...report,
        sections: updatedSections,
      };
      setReport(nextReport);

      let unreadCount = 0;
      for (const sec of nextReport.sections) {
        for (const it of sec.items) {
          if (!it.mark?.read_at) {
            unreadCount += 1;
          }
        }
      }
      setSummaries((prev) =>
        prev.map((s) => (s.id === nextReport.id ? { ...s, unread_count: unreadCount } : s)),
      );
    } catch (err) {
      if (err instanceof ApiError) {
        setError(`${err.problem.detail} (${err.problem.code})`);
      } else {
        setError(err instanceof Error ? err.message : 'Failed to update item mark.');
      }
    } finally {
      setSavingItemId(null);
    }
  };

  return (
    <div className="stack-lg">
      <div className="page-header">
        <div>
          <h1 className="page-title">Daily Brief</h1>
          <p className="page-subtitle">
            Timezone: {profile?.timezone || 'Asia/Taipei'} · Date: {selectedDate}
          </p>
        </div>

        <div className="row">
          <label htmlFor="report-date-input" className="form-label">
            Report Date
          </label>
          <input
            id="report-date-input"
            type="date"
            className="input"
            value={selectedDate}
            onChange={(e) => setSelectedDate(e.target.value)}
          />
          <button
            type="button"
            className="btn btn-primary"
            disabled={isJobActive}
            aria-busy={isJobActive}
            onClick={() => void handleGenerateNow()}
          >
            {isJobActive ? 'Generating...' : 'Generate now'}
          </button>
        </div>
      </div>

      {isJobActive && activeJob ? (
        <div className="alert" role="status">
          Generating brief ({activeJob.status})... Polling every 5 seconds.
        </div>
      ) : null}

      {jobErrorMessage ? (
        <div className="alert" role="alert">
          <span>{jobErrorMessage}</span>
          {jobErrorCode === 'no_topics' ? (
            <Link to="/settings">Manage topic subscriptions</Link>
          ) : null}
        </div>
      ) : null}

      {error ? (
        <div className="alert" role="alert">
          <strong>Error:</strong> {error}
        </div>
      ) : null}

      {loadingList ? (
        <div className="card" role="status">
          <p className="text-muted">Loading reports for {selectedDate}...</p>
        </div>
      ) : summaries.length === 0 ? (
        <div className="card">
          <h2 className="section-title">No Reports for {selectedDate}</h2>
          <p className="text-muted">
            No scheduled brief was generated on this date. Select another date or configure your delivery slots.
          </p>
        </div>
      ) : (
        <>
          <section className="card" aria-label="Available editions">
            <div className="row-between">
              <div className="row">
                {summaries.map((summary) => {
                  const isSelected = summary.id === selectedReportId;
                  const editionLabel = summary.local_time
                    ? `Slot ${summary.local_time}`
                    : 'On-demand';
                  return (
                    <button
                      key={summary.id}
                      type="button"
                      className={`btn ${isSelected ? 'btn-primary' : ''}`}
                      aria-pressed={isSelected}
                      onClick={() => setSelectedReportId(summary.id)}
                    >
                      {editionLabel} · {summary.unread_count}/{summary.item_count} unread
                    </button>
                  );
                })}
              </div>
              <span className="text-subtle">
                {summaries.find((s) => s.id === selectedReportId)?.section_count ?? 0} topics
              </span>
            </div>
          </section>

          {loadingReport ? (
            <div className="card" role="status">
              <p className="text-muted">Loading report content...</p>
            </div>
          ) : report ? (
            <div className="stack-lg">
              {[...report.sections]
                .sort((a, b) => a.position - b.position)
                .map((section) => (
                  <section
                    key={section.id}
                    className="stack"
                    aria-labelledby={`section-heading-${section.id}`}
                  >
                    <div className="row-between">
                      <h2 id={`section-heading-${section.id}`} className="section-title">
                        {section.title}
                      </h2>
                      <span className="badge">
                        {section.status === 'insufficient_news'
                          ? 'Insufficient fresh news'
                          : `${section.items.length} items`}
                      </span>
                    </div>

                    {section.note ? (
                      <div className="card-muted">
                        <p className="text-muted">{section.note}</p>
                      </div>
                    ) : null}

                    {section.items.length > 0 ? (
                      <div className="stack">
                        {[...section.items]
                          .sort((a, b) => a.rank - b.rank)
                          .map((item) => (
                            <ReportItemCard
                              key={item.id}
                              item={item}
                              saving={savingItemId === item.id}
                              onSaveMark={handleSaveMark}
                            />
                          ))}
                      </div>
                    ) : null}
                  </section>
                ))}
            </div>
          ) : null}
        </>
      )}
    </div>
  );
}
