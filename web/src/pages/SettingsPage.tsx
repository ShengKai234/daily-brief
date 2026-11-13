import React, { useCallback, useEffect, useState } from 'react';
import { ApiError, apiClient } from '../api/client';
import type { AgentSettings, Subscription, Topic } from '../api/types';
import { useAuth } from '../auth/AuthContext';

const HALF_HOUR_OPTIONS: string[] = Array.from({ length: 48 }, (_, index) => {
  const hours = String(Math.floor(index / 2)).padStart(2, '0');
  const minutes = index % 2 === 0 ? '00' : '30';
  return `${hours}:${minutes}`;
});

interface EditableSlot {
  local_time: string;
  enabled: boolean;
}

export function SettingsPage() {
  const { token } = useAuth();
  const [subscriptions, setSubscriptions] = useState<Subscription[]>([]);
  const [topics, setTopics] = useState<Topic[]>([]);
  const [slots, setSlots] = useState<EditableSlot[]>([]);
  const [slotTimezone, setSlotTimezone] = useState<string>('Asia/Taipei');
  const [agentSettings, setAgentSettings] = useState<AgentSettings | null>(null);

  const [newTopicTitle, setNewTopicTitle] = useState<string>('');
  const [newItemCount, setNewItemCount] = useState<number>(3);
  const [editingTopicId, setEditingTopicId] = useState<string | null>(null);
  const [editingTopicTitle, setEditingTopicTitle] = useState<string>('');

  const [loading, setLoading] = useState<boolean>(true);
  const [busy, setBusy] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);
  const [statusMessage, setStatusMessage] = useState<string | null>(null);

  const loadAll = useCallback(async () => {
    if (!token) {
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const [subsRes, topicsRes, slotsRes, agentRes] = await Promise.all([
        apiClient.listSubscriptions(token),
        apiClient.listTopics(token),
        apiClient.getDeliverySlots(token),
        apiClient.getAgentSettings(token),
      ]);
      setSubscriptions(subsRes.subscriptions);
      setTopics(topicsRes.topics);
      setSlots(
        slotsRes.slots.map((s) => ({
          local_time: s.local_time,
          enabled: s.enabled,
        })),
      );
      setSlotTimezone(slotsRes.timezone);
      setAgentSettings(agentRes);
    } catch (err) {
      if (err instanceof ApiError) {
        setError(`${err.problem.detail} (${err.problem.code})`);
      } else {
        setError(err instanceof Error ? err.message : 'Failed to load settings.');
      }
    } finally {
      setLoading(false);
    }
  }, [token]);

  useEffect(() => {
    void loadAll();
  }, [loadAll]);

  const handleAddSubscription = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!token) {
      return;
    }
    const title = newTopicTitle.trim();
    if (!title) {
      return;
    }
    setBusy(true);
    setError(null);
    setStatusMessage(null);
    try {
      await apiClient.createSubscription(token, {
        title,
        item_count: newItemCount,
        enabled: true,
      });
      setNewTopicTitle('');
      setNewItemCount(3);
      await loadAll();
      setStatusMessage(`Subscribed to "${title}".`);
    } catch (err) {
      if (err instanceof ApiError) {
        setError(`${err.problem.detail} (${err.problem.code})`);
      } else {
        setError(err instanceof Error ? err.message : 'Failed to add subscription.');
      }
    } finally {
      setBusy(false);
    }
  };

  const handleUpdateSubscription = async (
    sub: Subscription,
    patch: { item_count?: number; enabled?: boolean; position?: number },
  ) => {
    if (!token) {
      return;
    }
    setBusy(true);
    setError(null);
    setStatusMessage(null);
    try {
      await apiClient.updateSubscription(token, sub.id, patch);
      await loadAll();
      setStatusMessage(`Updated "${sub.topic.title}".`);
    } catch (err) {
      if (err instanceof ApiError) {
        setError(`${err.problem.detail} (${err.problem.code})`);
      } else {
        setError(err instanceof Error ? err.message : 'Failed to update subscription.');
      }
    } finally {
      setBusy(false);
    }
  };

  const handleDeleteSubscription = async (sub: Subscription) => {
    if (!token) {
      return;
    }
    setBusy(true);
    setError(null);
    setStatusMessage(null);
    try {
      await apiClient.deleteSubscription(token, sub.id);
      await loadAll();
      setStatusMessage(`Unsubscribed from "${sub.topic.title}".`);
    } catch (err) {
      if (err instanceof ApiError) {
        setError(`${err.problem.detail} (${err.problem.code})`);
      } else {
        setError(err instanceof Error ? err.message : 'Failed to remove subscription.');
      }
    } finally {
      setBusy(false);
    }
  };

  const handleSaveTopicTitle = async (topicId: string) => {
    if (!token) {
      return;
    }
    const trimmed = editingTopicTitle.trim();
    if (!trimmed) {
      return;
    }
    setBusy(true);
    setError(null);
    setStatusMessage(null);
    try {
      await apiClient.updateTopic(token, topicId, { title: trimmed });
      setEditingTopicId(null);
      setEditingTopicTitle('');
      await loadAll();
      setStatusMessage(`Renamed topic to "${trimmed}".`);
    } catch (err) {
      if (err instanceof ApiError) {
        setError(`${err.problem.detail} (${err.problem.code})`);
      } else {
        setError(err instanceof Error ? err.message : 'Failed to rename topic.');
      }
    } finally {
      setBusy(false);
    }
  };

  const handleDeleteTopic = async (topic: Topic) => {
    if (!token) {
      return;
    }
    setBusy(true);
    setError(null);
    setStatusMessage(null);
    try {
      await apiClient.deleteTopic(token, topic.id);
      await loadAll();
      setStatusMessage(`Deleted topic "${topic.title}".`);
    } catch (err) {
      if (err instanceof ApiError) {
        setError(`${err.problem.detail} (${err.problem.code})`);
      } else {
        setError(err instanceof Error ? err.message : 'Failed to delete topic.');
      }
    } finally {
      setBusy(false);
    }
  };

  const handleAddSlotRow = () => {
    if (slots.length >= 3) {
      return;
    }
    const candidateTimes = ['07:00', '12:30', '19:00', '08:00', '21:00'];
    const used = new Set(slots.map((s) => s.local_time));
    const nextTime = candidateTimes.find((t) => !used.has(t)) || '08:00';
    setSlots((prev) => [...prev, { local_time: nextTime, enabled: true }]);
  };

  const handleRemoveSlotRow = (index: number) => {
    setSlots((prev) => prev.filter((_, idx) => idx !== index));
  };

  const handleSaveSlots = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!token) {
      return;
    }
    setBusy(true);
    setError(null);
    setStatusMessage(null);
    try {
      const updated = await apiClient.replaceDeliverySlots(token, { slots });
      setSlots(
        updated.slots.map((s) => ({
          local_time: s.local_time,
          enabled: s.enabled,
        })),
      );
      setSlotTimezone(updated.timezone);
      setStatusMessage('Delivery slots saved.');
    } catch (err) {
      if (err instanceof ApiError) {
        setError(`${err.problem.detail} (${err.problem.code})`);
      } else {
        setError(err instanceof Error ? err.message : 'Failed to save delivery slots.');
      }
    } finally {
      setBusy(false);
    }
  };

  const unsubscribedTopics = topics.filter(
    (t) => !subscriptions.some((s) => s.topic_id === t.id),
  );

  return (
    <div className="stack-lg">
      <div className="page-header">
        <div>
          <h1 className="page-title">Topics &amp; Delivery Slots</h1>
          <p className="page-subtitle">
            Configure subscribed topics, item counts per topic (1–10), display order, and up to 3 daily delivery times.
          </p>
        </div>
      </div>

      {error ? (
        <div className="alert" role="alert">
          <strong>Error:</strong> {error}
        </div>
      ) : null}

      {statusMessage ? (
        <div className="alert" role="status">
          {statusMessage}
        </div>
      ) : null}

      {loading ? (
        <div className="card" role="status">
          <p className="text-muted">Loading settings...</p>
        </div>
      ) : (
        <>
          <section className="card" aria-labelledby="add-topic-heading">
            <h2 id="add-topic-heading" className="section-title">
              Add Topic Subscription
            </h2>
            <form className="stack" onSubmit={(e) => void handleAddSubscription(e)}>
              <div className="form-group">
                <label htmlFor="new-topic-title" className="form-label">
                  Topic Title
                </label>
                <input
                  id="new-topic-title"
                  type="text"
                  className="input"
                  maxLength={200}
                  placeholder="e.g. Semiconductor Supply Chain"
                  value={newTopicTitle}
                  onChange={(e) => setNewTopicTitle(e.target.value)}
                  required
                />
              </div>

              <div className="form-group">
                <label htmlFor="new-topic-count" className="form-label">
                  Items per Report (1–10)
                </label>
                <input
                  id="new-topic-count"
                  type="number"
                  min={1}
                  max={10}
                  className="input"
                  value={newItemCount}
                  onChange={(e) => setNewItemCount(Number(e.target.value))}
                  required
                />
              </div>

              <div>
                <button type="submit" className="btn btn-primary" disabled={busy || !newTopicTitle.trim()}>
                  Subscribe Topic
                </button>
              </div>
            </form>
          </section>

          <section className="card" aria-labelledby="subscriptions-heading">
            <h2 id="subscriptions-heading" className="section-title">
              Subscribed Topics ({subscriptions.length})
            </h2>

            {subscriptions.length === 0 ? (
              <p className="text-muted">No active topic subscriptions yet. Add one above.</p>
            ) : (
              <ul className="list-reset stack">
                {subscriptions.map((sub, idx) => {
                  const isEditingThisTopic = editingTopicId === sub.topic_id;
                  return (
                    <li key={sub.id} className="card-muted">
                      <div className="row-between">
                        <div className="row">
                          <span className="badge">#{idx + 1}</span>
                          {isEditingThisTopic ? (
                            <div className="row">
                              <label htmlFor={`rename-topic-${sub.id}`} className="form-label">
                                Rename Topic
                              </label>
                              <input
                                id={`rename-topic-${sub.id}`}
                                type="text"
                                className="input"
                                value={editingTopicTitle}
                                onChange={(e) => setEditingTopicTitle(e.target.value)}
                              />
                              <button
                                type="button"
                                className="btn btn-primary"
                                disabled={busy}
                                onClick={() => void handleSaveTopicTitle(sub.topic_id)}
                              >
                                Save Title
                              </button>
                              <button
                                type="button"
                                className="btn"
                                disabled={busy}
                                onClick={() => setEditingTopicId(null)}
                              >
                                Cancel
                              </button>
                            </div>
                          ) : (
                            <strong>{sub.topic.title}</strong>
                          )}
                        </div>

                        <div className="row">
                          {!isEditingThisTopic ? (
                            <button
                              type="button"
                              className="btn"
                              disabled={busy}
                              onClick={() => {
                                setEditingTopicId(sub.topic_id);
                                setEditingTopicTitle(sub.topic.title);
                              }}
                            >
                              Rename
                            </button>
                          ) : null}
                          <button
                            type="button"
                            className="btn"
                            disabled={busy || idx === 0}
                            aria-label={`Move ${sub.topic.title} up`}
                            onClick={() =>
                              void handleUpdateSubscription(sub, { position: Math.max(0, idx - 1) })
                            }
                          >
                            Move Up
                          </button>
                          <button
                            type="button"
                            className="btn"
                            disabled={busy || idx === subscriptions.length - 1}
                            aria-label={`Move ${sub.topic.title} down`}
                            onClick={() =>
                              void handleUpdateSubscription(sub, { position: idx + 1 })
                            }
                          >
                            Move Down
                          </button>
                        </div>
                      </div>

                      <div className="row-between">
                        <div className="row">
                          <label htmlFor={`item-count-${sub.id}`} className="form-label">
                            Items for {sub.topic.title}
                          </label>
                          <select
                            id={`item-count-${sub.id}`}
                            className="select"
                            disabled={busy}
                            value={sub.item_count}
                            onChange={(e) =>
                              void handleUpdateSubscription(sub, {
                                item_count: Number(e.target.value),
                              })
                            }
                          >
                            {[1, 2, 3, 4, 5, 6, 7, 8, 9, 10].map((n) => (
                              <option key={n} value={n}>
                                {n}
                              </option>
                            ))}
                          </select>

                          <label htmlFor={`enabled-${sub.id}`} className="checkbox-label">
                            <input
                              id={`enabled-${sub.id}`}
                              type="checkbox"
                              checked={sub.enabled}
                              disabled={busy}
                              onChange={(e) =>
                                void handleUpdateSubscription(sub, { enabled: e.target.checked })
                              }
                            />
                            <span>Enabled</span>
                          </label>
                        </div>

                        <button
                          type="button"
                          className="btn"
                          disabled={busy}
                          aria-label={`Remove ${sub.topic.title}`}
                          onClick={() => void handleDeleteSubscription(sub)}
                        >
                          Remove
                        </button>
                      </div>
                    </li>
                  );
                })}
              </ul>
            )}

            {unsubscribedTopics.length > 0 ? (
              <div className="stack">
                <hr className="divider" />
                <h3 className="item-title">Unsubscribed Topics</h3>
                <ul className="list-reset stack">
                  {unsubscribedTopics.map((topic) => (
                    <li key={topic.id} className="row-between">
                      <span>{topic.title}</span>
                      <div className="row">
                        <button
                          type="button"
                          className="btn"
                          disabled={busy}
                          onClick={() =>
                            void (
                              token &&
                              apiClient
                                .createSubscription(token, {
                                  topic_id: topic.id,
                                  item_count: 3,
                                  enabled: true,
                                })
                                .then(() => loadAll())
                            )
                          }
                        >
                          Re-subscribe
                        </button>
                        <button
                          type="button"
                          className="btn"
                          disabled={busy}
                          onClick={() => void handleDeleteTopic(topic)}
                        >
                          Delete Topic
                        </button>
                      </div>
                    </li>
                  ))}
                </ul>
              </div>
            ) : null}
          </section>

          <section className="card" aria-labelledby="delivery-slots-heading">
            <div className="row-between">
              <div>
                <h2 id="delivery-slots-heading" className="section-title">
                  Delivery Slots ({slots.length}/3)
                </h2>
                <p className="text-muted">
                  Interpreted in {slotTimezone} (30-minute granularity, maximum 3 slots per day).
                </p>
              </div>
              <button
                type="button"
                className="btn"
                disabled={busy || slots.length >= 3}
                onClick={handleAddSlotRow}
              >
                Add Slot
              </button>
            </div>

            <form className="stack" onSubmit={(e) => void handleSaveSlots(e)}>
              {slots.length === 0 ? (
                <p className="text-muted">No delivery slots configured. Add up to 3 daily slots.</p>
              ) : (
                <ul className="list-reset stack">
                  {slots.map((slot, index) => (
                    <li key={index} className="card-muted row-between">
                      <div className="row">
                        <span className="badge">Slot {index + 1}</span>
                        <label htmlFor={`slot-time-${index}`} className="form-label">
                          Slot {index + 1} Time
                        </label>
                        <select
                          id={`slot-time-${index}`}
                          className="select"
                          value={slot.local_time}
                          onChange={(e) =>
                            setSlots((prev) =>
                              prev.map((item, idx) =>
                                idx === index ? { ...item, local_time: e.target.value } : item,
                              ),
                            )
                          }
                        >
                          {HALF_HOUR_OPTIONS.map((t) => (
                            <option key={t} value={t}>
                              {t}
                            </option>
                          ))}
                        </select>

                        <label htmlFor={`slot-enabled-${index}`} className="checkbox-label">
                          <input
                            id={`slot-enabled-${index}`}
                            type="checkbox"
                            checked={slot.enabled}
                            onChange={(e) =>
                              setSlots((prev) =>
                                prev.map((item, idx) =>
                                  idx === index ? { ...item, enabled: e.target.checked } : item,
                                ),
                              )
                            }
                          />
                          <span>Slot {index + 1} Enabled</span>
                        </label>
                      </div>

                      <button
                        type="button"
                        className="btn"
                        aria-label={`Remove Slot ${index + 1}`}
                        onClick={() => handleRemoveSlotRow(index)}
                      >
                        Remove Slot
                      </button>
                    </li>
                  ))}
                </ul>
              )}

              <div>
                <button type="submit" className="btn btn-primary" disabled={busy}>
                  Save Delivery Slots
                </button>
              </div>
            </form>
          </section>

          {agentSettings ? (
            <section className="card" aria-labelledby="agent-settings-heading">
              <h2 id="agent-settings-heading" className="section-title">
                Agent Settings (Read-Only in POC)
              </h2>
              <p className="text-muted">
                Runner: <strong className="text-mono">{agentSettings.runner}</strong> · Model:{' '}
                <strong className="text-mono">{agentSettings.model}</strong> · Daily run limit:{' '}
                <strong>{agentSettings.daily_run_limit}</strong>
              </p>
            </section>
          ) : null}
        </>
      )}
    </div>
  );
}
