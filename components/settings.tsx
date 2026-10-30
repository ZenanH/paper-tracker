'use client';
import { useEffect, useState } from 'react';
import { api, APIError } from './client-api';
import type { Notice } from './toasts';

type SettingsData = { baseURL: string; model: string; hasKey: boolean; filterPrompt: string; configVersion: number; installedAt: number; deploymentMonthStart: number; hour: number; minute: number };
export function Settings({ notify, changed, confirm }: { notify: (n: Omit<Notice, 'id'>) => void; changed: () => void; confirm: (message: string, action: () => Promise<void>) => void }) {
  const [data, setData] = useState<SettingsData | null>(null), [key, setKey] = useState(''), [busy, setBusy] = useState(false), [error, setError] = useState(''), [testing, setTesting] = useState(false);
  const [email, setEmail] = useState(''), [emailBusy, setEmailBusy] = useState(false);
  useEffect(() => { api<{ email: string }>('settings/crossref').then(r => setEmail(r.email)).catch(e => notify({ message: e.message, tone: 'error' })); }, []);
  useEffect(() => { api<SettingsData>('settings').then(setData).catch(e => setError(e.message)); }, []);
  async function save(confirmed = false) {
    if (!data) return; setBusy(true); setError('');
    try { const result = await api<SettingsData>('settings', { body: { baseURL: data.baseURL, model: data.model, apiKey: key, filterPrompt: data.filterPrompt, confirmReprocess: confirmed } }); setData(result); setKey(''); notify({ message: 'AI settings saved. The worker will use the new configuration.' }); changed(); }
    catch (e) { if (e instanceof APIError && e.code === 'reprocess_confirmation_required') confirm(e.message, () => save(true)); else setError((e as Error).message); }
    finally { setBusy(false); }
  }
  if (!data) return <p className="muted">{error || 'Loading settings…'}</p>;
  return <div className="settings-stack">
    <section className="settings-section glass-lite"><h2>AI connection</h2><p className="section-description">Title filtering and translation.</p><form onSubmit={e => { e.preventDefault(); void save(); }}>
      <label className="field">API base URL <input value={data.baseURL} onChange={e => setData({ ...data, baseURL: e.target.value })} placeholder="https://api.openai.com/v1" type="url" maxLength={1000}/><small>OpenAI-compatible base URL.</small></label>
      <div className="field-row"><label className="field">API Key <input type="password" autoComplete="new-password" value={key} onChange={e => setKey(e.target.value)} placeholder={data.hasKey ? 'Saved securely · Leave blank to keep' : 'Enter your own API key'} maxLength={4096}/></label><label className="field">Model <input value={data.model} onChange={e => setData({ ...data, model: e.target.value })} placeholder="Enter a model supported by your provider" maxLength={200}/></label></div>
      <label className="field">Filter prompt <textarea value={data.filterPrompt} onChange={e => setData({ ...data, filterPrompt: e.target.value })} rows={5} maxLength={6000}/><small>Enable filtering per journal.</small></label>
      {error && <p className="inline-error" role="alert">{error}</p>}
      <div className="form-actions"><button className="button primary" disabled={busy}>{busy ? 'Saving…' : 'Save'}</button><button type="button" className="button secondary" disabled={testing || !data.hasKey} onClick={async () => { setTesting(true); setError(''); try { await api('settings/test', { body: {} }); notify({ message: 'Connection successful. The selected model is available.' }); } catch (e) { setError((e as Error).message); } finally { setTesting(false); } }}>{testing ? 'Testing…' : 'Test connection'}</button>{data.hasKey && <button type="button" className="button secondary danger" onClick={() => confirm('Delete the saved AI key? New AI tasks will pause. Existing results are retained.', async () => { const result = await api<SettingsData>('settings', { body: { ...data, deleteKey: true, confirmReprocess: true } }); setData(result); setKey(''); changed(); notify({ message: 'AI key deleted. Existing results retained.' }); })}>Delete saved key</button>}</div><p className="data-note">Connection tests use API credit.</p>

    </form></section>
    <section className="settings-section glass-lite"><h2>Crossref</h2><form onSubmit={async e => { e.preventDefault(); setEmailBusy(true); try { const result = await api<{ email: string }>('settings/crossref', { body: { email: email.trim() } }); setEmail(result.email); notify({ message: 'Crossref email saved.' }); } catch (e) { notify({ message: (e as Error).message, tone: 'error' }); } finally { setEmailBusy(false); } }}><label className="field">Email <input type="email" autoComplete="email" value={email} onChange={e => setEmail(e.target.value)} maxLength={254} placeholder="you@example.com"/><small>Optional · Sent only to Crossref.</small></label><button className="button secondary" disabled={emailBusy}>{emailBusy ? 'Saving…' : 'Save'}</button></form></section>
    <section className="settings-section glass-lite"><h2>Automatic sync</h2><p className="section-description">Daily · Beijing time (UTC+8)</p><form onSubmit={async e => { e.preventDefault(); try { await api('settings/schedule', { body: { hour: data.hour, minute: data.minute } }); notify({ message: 'Sync schedule updated.' }); changed(); } catch (e) { notify({ message: (e as Error).message, tone: 'error' }); } }}><div className="schedule-row"><label className="field">Daily <input aria-label="Daily sync time (Beijing)" type="time" value={`${String(data.hour).padStart(2, '0')}:${String(data.minute).padStart(2, '0')}`} onChange={e => { const [hour, minute] = e.target.value.split(':').map(Number); setData({ ...data, hour, minute }); }}/></label><button className="button secondary">Save</button></div></form><div className="installation-note"><span>Initialized</span><strong>{new Date(data.installedAt).toLocaleString('en-US', { timeZone: 'Asia/Shanghai', hour12: false })}</strong><span>Earliest fetch date</span><strong>{new Date(data.deploymentMonthStart).toLocaleDateString('sv-SE', { timeZone: 'Asia/Shanghai' })}</strong></div></section>
  </div>;
}
