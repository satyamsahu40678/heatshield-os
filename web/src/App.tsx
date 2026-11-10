import { useCallback, useEffect, useRef, useState } from 'react'
import type { ButtonHTMLAttributes, FormEvent, ReactNode } from 'react'
import {
  Activity, ArrowDownToLine, ArrowLeft, ArrowRight, Bell, Check, CheckCircle2,
  ChevronRight, CircleHelp, ClipboardCheck, Clock3, FileJson2, Flame,
  History, LayoutDashboard, LoaderCircle, LogOut, RefreshCw,
  ShieldCheck, ShieldHalf, SlidersHorizontal, ThermometerSun, Users, X,
} from 'lucide-react'
import { getSession, request, setSession } from './api'
import Dashboard from './Dashboard'
import type {
  AuditEvent, Comparison, ComparisonRow, Finding, Intervention, Run, Scenario,
  ScenarioDetail, ScenarioSummary, Schedule, Session, Validation, WorkerSchedule,
} from './types'

const ROLE_LABELS = { admin: 'Administrator', safety_officer: 'Safety officer', supervisor: 'Supervisor', worker: 'Worker' }
const DEMO_PASSWORD = 'HeatShieldDemo2026!'
type Screen = 'overview' | 'new' | 'scenario' | 'interventions' | 'audit' | 'worker' | 'symptoms'

function message(error: unknown) { return error instanceof Error ? error.message : 'Something went wrong. Please try again.' }
function friendly(value: string) { return value.replaceAll('_', ' ').replaceAll('-', ' ').replace(/\b\w/g, char => char.toUpperCase()) }
function time(value: string, timezone = 'Asia/Dubai') { return new Intl.DateTimeFormat('en-GB', { hour: '2-digit', minute: '2-digit', timeZone: timezone }).format(new Date(value)) }
function date(value: string, timezone = 'Asia/Dubai') { return new Intl.DateTimeFormat('en-GB', { day: 'numeric', month: 'short', year: 'numeric', timeZone: timezone }).format(new Date(value)) }
function stamp(value?: string | null) { return value ? new Date(value).toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' }) : '—' }
function items<T>(value: T[] | { items?: T[]; scenarios?: T[] }): T[] { return Array.isArray(value) ? value : value.items || value.scenarios || [] }
function useAction() {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  async function perform(task: () => Promise<void>) { setBusy(true); setError(''); try { await task() } catch (reason) { setError(message(reason)) } finally { setBusy(false) } }
  return { busy, error, setError, perform }
}
function Button({ children, variant = 'primary', loading = false, ...props }: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: string; loading?: boolean }) {
  return <button className={`button ${variant}`} {...props} disabled={props.disabled || loading}>{loading && <LoaderCircle size={16} className="spin" />}{children}</button>
}
function Badge({ children, tone = 'neutral' }: { children: ReactNode; tone?: string }) { return <span className={`badge ${tone}`}>{children}</span> }
function Status({ value }: { value: string }) {
  const normalized = value.toUpperCase()
  const tone = /PUBLISHED|VERIFIED|COMPLETED|OPTIMAL/.test(normalized) ? 'teal' : /FAIL|INVALID|INFEASIBLE|STOP/.test(normalized) ? 'red' : /RUNNING|QUEUED|REVIEW|ACKNOWLEDGED/.test(normalized) ? 'amber' : 'neutral'
  return <Badge tone={tone}>{friendly(value)}</Badge>
}
function ErrorBox({ children }: { children?: ReactNode }) { return children ? <div role="alert" className="error-box"><CircleHelp size={18} /><span>{children}</span></div> : null }
function Loading({ text = 'Loading your workspace…' }: { text?: string }) { return <div className="loading-state"><LoaderCircle className="spin" size={24} /><span>{text}</span></div> }
function Empty({ icon, title, children, action }: { icon?: ReactNode; title: string; children: ReactNode; action?: ReactNode }) { return <div className="empty-state"><div className="empty-icon">{icon || <ClipboardCheck size={27} />}</div><h3>{title}</h3><p>{children}</p>{action}</div> }
function Logo({ compact = false }: { compact?: boolean }) { return <div className="logo"><span className="logo-mark"><ShieldHalf size={25} strokeWidth={1.8} /></span><span>HeatShield <small>OS</small>{!compact && <em>Protection, put into practice.</em>}</span></div> }

function Login({ onSession, initialError }: { onSession: (value: Session) => void; initialError: string }) {
  const [email, setEmail] = useState('safety@example.test')
  const [password, setPassword] = useState(DEMO_PASSWORD)
  const action = useAction()
  function submit(event: FormEvent) { event.preventDefault(); void action.perform(async () => { const value = await request<Session>('/auth/login', 'POST', { email, password }); setSession(value); onSession(value) }) }
  return <div className="login-layout">
    <section className="login-story"><Logo /><div className="login-story-content"><div className="eyebrow light">OCCUPATIONAL HEAT PREVENTION</div><h1>A warning is only<br />the beginning.</h1><p>Turn heat evidence into a workable plan.<br />Then track the protection through to completion.</p><div className="story-steps"><span><ThermometerSun size={19} /> Understand exposure</span><span><SlidersHorizontal size={19} /> Plan protective action</span><span><ShieldCheck size={19} /> Verify it happened</span></div></div><div className="story-footer"><span className="live-dot" /> Built for accountable decisions. Designed around workers.</div><div className="orbit orbit-one" /><div className="orbit orbit-two" /></section>
    <main className="login-main"><div className="login-form"><Badge tone="amber">Synthetic demo</Badge><h2>Welcome to your<br />planning workspace.</h2><p className="muted">Sign in to explore the complete review and intervention workflow using fabricated data.</p><ErrorBox>{action.error || initialError}</ErrorBox><form onSubmit={submit}><label>Email<input type="email" value={email} onChange={event => setEmail(event.target.value)} required autoComplete="username" /></label><label>Password<input type="password" value={password} onChange={event => setPassword(event.target.value)} required autoComplete="current-password" /></label><Button type="submit" loading={action.busy}>Sign in <ArrowRight size={17} /></Button></form><div className="demo-account-heading"><span>EXPLORE A DEMO ROLE</span><div /></div><div className="demo-accounts">{([['safety', 'Safety officer'], ['supervisor', 'Supervisor'], ['worker', 'Worker'], ['admin', 'Administrator']] as const).map(([account, label]) => <button key={account} type="button" aria-label={`Use ${label.toLowerCase()} demo`} onClick={() => { setEmail(`${account}@example.test`); setPassword(DEMO_PASSWORD); action.setError('') }}><span>{label}</span><ChevronRight size={16} /></button>)}</div><p className="login-note"><ShieldCheck size={17} /> Demonstration only. Demo review never authorizes real work. All policy thresholds and worker records are synthetic.</p></div></main>
  </div>
}

export default function App() {
  const [session, updateSession] = useState(getSession)
  const [screen, setScreen] = useState<Screen>(getSession()?.user.role === 'worker' ? 'worker' : 'overview')
  const [selectedId, setSelectedId] = useState('')
  const [initialDetail, setInitialDetail] = useState<ScenarioDetail | null>(null)
  const [sessionError, setSessionError] = useState('')
  useEffect(() => {
    const expired = () => { updateSession(null); setSessionError('Your session expired. Sign in again to continue.'); setSelectedId('') }
    window.addEventListener('heatshield:session-expired', expired)
    return () => window.removeEventListener('heatshield:session-expired', expired)
  }, [])
  useEffect(() => { if (session) void request('/auth/me').catch(() => {}) }, [session?.access_token])
  function signedIn(value: Session) { updateSession(value); setScreen(value.user.role === 'worker' ? 'worker' : 'overview'); setSessionError('') }
  function navigate(next: Screen) { setScreen(next) }
  function openScenario(id: string, detail: ScenarioDetail | null = null) { setSelectedId(id); setInitialDetail(detail); navigate('scenario') }
  async function logout() { try { await request('/auth/logout', 'POST', {}) } catch { /* Local sign-out must also work if the connection is lost. */ } finally { setSession(null); updateSession(null); setSelectedId(''); setSessionError('') } }
  if (!session) return <Login onSession={signedIn} initialError={sessionError} />
  const worker = session.user.role === 'worker'
  const navigation = worker ? [{ id: 'worker', label: 'My schedule', icon: <ClipboardCheck size={19} /> }, { id: 'symptoms', label: 'Report symptoms', icon: <Activity size={19} /> }] : [{ id: 'overview', label: 'Overview', icon: <LayoutDashboard size={19} /> }, { id: 'interventions', label: 'Interventions', icon: <ShieldCheck size={19} /> }, { id: 'audit', label: 'Audit', icon: <History size={19} /> }]
  return <div className={`app-shell operations-shell ${worker ? 'worker-shell' : ''}`}>
    <header className="command-header">
      <Logo compact />
      <nav className="command-navigation" aria-label="Main navigation">{navigation.map(item => <button key={item.id} className={screen === item.id || (item.id === 'overview' && (screen === 'new' || screen === 'scenario')) ? 'active' : ''} onClick={() => navigate(item.id as Screen)}>{item.icon}{item.label}</button>)}</nav>
      <div className="command-account"><Badge tone="amber"><span className="status-dot" />Synthetic demo</Badge><div className="command-user"><div className="avatar">{session.user.display_name?.split(' ').map(part => part[0]).slice(0, 2).join('') || 'HS'}</div><div><strong>{session.user.display_name || session.user.email.split('@')[0]}</strong><span>{ROLE_LABELS[session.user.role]}</span></div></div><button className="command-logout" aria-label="Sign out" title="Sign out" onClick={() => void logout()}><LogOut size={19} /></button></div>
    </header>
    <div className="command-subbar"><div className="breadcrumbs"><span>Operations workspace</span><ChevronRight size={14} /><strong>{screen === 'scenario' ? 'Scenario planning' : screen === 'new' ? 'New scenario' : navigation.find(item => item.id === screen)?.label}</strong></div><span><ShieldCheck size={14} /> Review prototype. Every action stays accountable.</span></div>
    <div className="main-column">
      <main className="page-content">
        {screen === 'overview' && !worker && <Dashboard onNew={() => navigate('new')} onOpen={openScenario} onInterventions={() => navigate('interventions')} onAudit={() => navigate('audit')} />}
        {screen === 'new' && !worker && <ScenarioEditor onBack={() => navigate('overview')} onSaved={detail => openScenario(detail.id, detail)} />}
        {screen === 'scenario' && !worker && <ScenarioPage key={selectedId} id={selectedId} initialDetail={initialDetail} role={session.user.role} onBack={() => navigate('overview')} />}
        {screen === 'interventions' && !worker && <InterventionsPage role={session.user.role} />}
        {screen === 'audit' && !worker && <AuditPage />}
        {screen === 'worker' && worker && <WorkerPage onSymptoms={() => navigate('symptoms')} />}
        {screen === 'symptoms' && worker && <SymptomsPage onBack={() => navigate('worker')} />}
      </main><footer className="app-footer"><span><ShieldCheck size={14} /> Synthetic planning & intervention prototype</span><span>Demo review does not authorize real work.</span></footer>
    </div>
  </div>
}

function Stat({ icon, label, value, detail, tone = '' }: { icon: ReactNode; label: string; value: ReactNode; detail: string; tone?: string }) { return <div className={`stat-card ${tone}`}><div className="stat-top"><span>{label}</span><span className="stat-icon">{icon}</span></div><strong>{value}</strong><p>{detail}</p></div> }

function ScenarioEditor({ onBack, onSaved, existing }: { onBack: () => void; onSaved: (detail: ScenarioDetail) => void; existing?: ScenarioDetail }) {
  const [name, setName] = useState(existing?.name || 'Abu Dhabi · East yard')
  const [json, setJson] = useState(existing ? JSON.stringify(existing.scenario, null, 2) : '')
  const action = useAction()
  const fileInput = useRef<HTMLInputElement>(null)
  async function fixture() { await action.perform(async () => setJson(JSON.stringify(await request<Scenario>('/demo/scenario'), null, 2))) }
  function save(event: FormEvent) { event.preventDefault(); void action.perform(async () => {
    let scenario: Scenario
    try { scenario = JSON.parse(json) as Scenario } catch { throw new Error('Scenario JSON is not valid. Load a demo fixture or check the imported document.') }
    const detail = existing ? await request<ScenarioDetail>(`/scenarios/${existing.id}`, 'PUT', { expected_revision: existing.revision, name, scenario }) : await request<ScenarioDetail>('/scenarios', 'POST', { name, scenario })
    onSaved(detail)
  }) }
  return <><button className="back-link" onClick={onBack}><ArrowLeft size={16} />Back to {existing ? 'scenario' : 'overview'}</button><div className="page-heading"><div><div className="eyebrow">SCENARIO INPUTS</div><h1>{existing ? 'Revise this scenario' : 'Build your planning scenario'}</h1><p>Start with fabricated data. Every input is preserved in an immutable revision.</p></div><Badge tone="amber">Synthetic only</Badge></div><ErrorBox>{action.error}</ErrorBox><form className="editor-layout" onSubmit={save}><section className="panel editor-main"><div className="panel-heading"><div><h2>Scenario details</h2><p>Use the fixture or import a compatible scenario JSON file.</p></div></div><div className="form-body"><label>Scenario name<input value={name} onChange={event => setName(event.target.value)} minLength={1} maxLength={160} required /></label><div className="editor-tools"><Button type="button" variant="secondary" loading={action.busy} onClick={() => void fixture()}><RefreshCw size={15} />Load demo fixture</Button><Button type="button" variant="ghost" onClick={() => fileInput.current?.click()}><FileJson2 size={16} />Import JSON</Button><input ref={fileInput} type="file" accept="application/json,.json" hidden onChange={event => { const file = event.target.files?.[0]; if (file) void action.perform(async () => setJson(await file.text())) }} /></div><label>Scenario JSON<textarea className="json-editor" value={json} onChange={event => setJson(event.target.value)} required spellCheck={false} placeholder="Load the demo fixture to see workers, tasks, observations and policy…" /></label><div className="form-actions"><Button type="button" variant="secondary" onClick={onBack}>Cancel</Button><Button type="submit" loading={action.busy} disabled={!json.trim()}>{existing ? 'Save new revision' : 'Create scenario'}<ArrowRight size={16} /></Button></div></div></section><aside className="editor-context"><div className="context-icon"><ShieldCheck size={26} /></div><h2>Make the inputs explicit.</h2><p>The planning core needs task windows, qualified workers, worksite evidence, and a versioned demo policy.</p><ul><li><Check size={16} />Single-worker atomic tasks</li><li><Check size={16} />15-minute candidate starts</li><li><Check size={16} />Zone-specific WBGT provenance</li><li><Check size={16} />Reserved recovery & cooling</li></ul><div className="small-notice">Changing inputs removes the previous demo review. Generate and review a new plan for the new revision.</div></aside></form></>
}

function ScenarioPage({ id, initialDetail, role, onBack }: { id: string; initialDetail: ScenarioDetail | null; role: string; onBack: () => void }) {
  const [detail, setDetail] = useState<ScenarioDetail | null>(initialDetail)
  const [tab, setTab] = useState('Timeline')
  const [editing, setEditing] = useState(false)
  const [withdrawOpen, setWithdrawOpen] = useState(false)
  const [withdrawNote, setWithdrawNote] = useState('')
  const action = useAction()
  const refresh = useCallback(async () => setDetail(await request<ScenarioDetail>(`/scenarios/${id}`)), [id])
  useEffect(() => { void action.perform(refresh) }, [id])
  const run = detail?.latest_run
  useEffect(() => {
    if (!run || !['QUEUED', 'RUNNING'].includes(run.status)) return
    let live = true
    const timer = window.setInterval(() => { void request<Run>(`/optimization-runs/${run.id}`).then(async value => {
      if (!live) return
      setDetail(current => current && { ...current, latest_run: value })
      if (value.status === 'COMPLETED' || value.status === 'FAILED') await refresh()
    }).catch(error => { if (live) action.setError(message(error)) }) }, 1000)
    return () => { live = false; window.clearInterval(timer) }
  }, [run?.id, run?.status, refresh])
  if (!detail) return <><ErrorBox>{action.error}</ErrorBox><Loading text="Loading the scenario…" /></>
  if (editing) return <ScenarioEditor existing={detail} onBack={() => setEditing(false)} onSaved={value => { setDetail(value); setEditing(false); setTab('Timeline') }} />
  const { scenario } = detail
  const result = run?.result
  const report = result?.validation
  const inProgress = run && ['QUEUED', 'RUNNING'].includes(run.status)
  const canReview = ['admin', 'safety_officer'].includes(role)
  async function optimize() { await action.perform(async () => {
    const value = await request<Run>(`/scenarios/${id}/optimize`, 'POST', { expected_revision: detail!.revision }, true)
    setDetail(current => current && { ...current, latest_run: value, approval: null, published_at: null, status: 'QUEUED' })
  }) }
  async function approve(kind: 'approve' | 'publish') { await action.perform(async () => {
    setDetail(await request<ScenarioDetail>(`/scenarios/${id}/${kind}`, 'POST', { expected_revision: detail!.revision, run_id: run!.id }, true))
  }) }
  async function withdraw(event: FormEvent) { event.preventDefault(); await action.perform(async () => {
    setDetail(await request<ScenarioDetail>(`/scenarios/${id}/withdraw`, 'POST', { expected_revision: detail!.revision, note: withdrawNote }, true))
    setWithdrawOpen(false); setWithdrawNote('')
  }) }
  const withdrawn = detail.status.toUpperCase() === 'WITHDRAWN'
  const reviewed = Boolean(detail.approval)
  const published = Boolean(detail.published_at)
  return <><button className="back-link" onClick={onBack}><ArrowLeft size={16} />All scenarios</button><div className="page-heading detail-heading"><div><div className="eyebrow">SCENARIO PLANNING <span> / REVISION {detail.revision}</span></div><h1>{detail.name}</h1><p>{date(scenario.starts_at, scenario.timezone)} · {time(scenario.starts_at, scenario.timezone)}–{time(scenario.ends_at, scenario.timezone)} · {scenario.timezone}</p></div><div className="heading-actions"><Button variant="secondary" onClick={() => setEditing(true)} disabled={Boolean(inProgress)}><SlidersHorizontal size={16} />Edit inputs</Button><Button onClick={() => void optimize()} loading={action.busy || Boolean(inProgress)}><Activity size={17} />Generate plan</Button></div></div><ErrorBox>{action.error}</ErrorBox>
    <div className="workflow-strip">{[{ label: 'Inputs captured', done: true }, { label: 'Plan generated', done: Boolean(result?.schedule) }, { label: 'Demo review', done: reviewed }, { label: 'Published demo', done: published }].map((step, index) => <div key={step.label} className={step.done ? 'done' : ''}><span>{step.done ? <Check size={14} /> : `0${index + 1}`}</span><strong>{step.label}</strong>{index < 3 && <ChevronRight size={16} />}</div>)}</div>
    <div className="scenario-meta"><span><Users size={17} /><strong>{scenario.workers.length}</strong> workers</span><span><ClipboardCheck size={17} /><strong>{scenario.tasks.length}</strong> work packages</span><span><ShieldCheck size={17} /><strong>{scenario.cooling_capacity}</strong> cooling places</span><span><ThermometerSun size={17} /><strong>{scenario.observations.length}</strong> observations</span><Badge tone="amber">Synthetic evidence</Badge></div>
    {inProgress && <div className="job-banner" role="status"><LoaderCircle size={22} className="spin" /><div><strong>{run.status === 'QUEUED' ? 'Your plan is queued' : 'Finding a feasible plan…'}</strong><p>Checking qualification, recovery, evidence and policy constraints. This job survives a page refresh.</p></div><Status value={run.status} /></div>}
    {run?.status === 'FAILED' && <ErrorBox>{run.error || 'The planning job failed. You can retry Generate plan.'}</ErrorBox>}
    {result && !result.schedule && <div className="warning-banner"><Flame size={22} /><div><strong>{result.status === 'INFEASIBLE' ? 'No plan meets the current encoded constraints.' : `Planning result: ${friendly(result.status)}`}</strong><p>{result.diagnostics.join(' ') || 'Review the input evidence, task windows, qualifications and cooling capacity before trying again.'}</p></div></div>}
    <div className="detail-tabs" aria-label="Scenario sections">{['Timeline', 'Evidence', 'Comparison', 'Scenario JSON', 'Audit'].map(value => <button key={value} aria-pressed={tab === value} className={tab === value ? 'active' : ''} onClick={() => setTab(value)}>{value}</button>)}</div>
    {tab === 'Timeline' && <><div className="planning-grid"><section className="panel timeline-panel"><div className="panel-heading"><div><h2>Work & recovery timeline</h2><p>Local site time · {scenario.timezone} · 15-minute start grid</p></div>{report && <Badge tone={report.valid ? 'teal' : 'red'}>{report.valid ? 'Checks passed' : 'Checks failed'}</Badge>}</div>{result?.schedule ? <Timeline scenario={scenario} schedule={result.schedule} /> : <Empty title="A plan starts with the constraints" icon={<SlidersHorizontal size={28} />}>Generate a plan to see task assignments, protected recovery and explicit deferrals.</Empty>}<div className="timeline-legend"><span><i className="work-key" />Work package</span><span><i className="recovery-key" />Protected recovery</span><span><i className="restricted-key" />Midday restriction</span></div></section><aside className="panel review-panel"><div className="panel-heading"><div><h2>Review & publish</h2><p>Human accountability stays explicit.</p></div></div><div className="review-body"><div className={`review-symbol ${report?.valid ? 'passed' : ''}`}><ShieldCheck size={30} /></div><h3>{withdrawn ? 'Demo plan withdrawn' : published ? 'Demo plan published' : reviewed ? 'Demo review recorded' : report?.valid ? 'Ready for a demo review' : 'Generate a checked proposal'}</h3><p>{withdrawn ? 'This plan is hidden from worker schedules. Its demo review and audit history are retained. Republishing requires fresh server checks.' : published ? 'The assigned workers can view their own published tasks and acknowledge recovery instructions.' : 'Passing encoded constraints is a software check. A demo review does not authorize real work.'}</p>{report && <div className="review-facts"><span>Constraint violations<strong>{report.violations.length}</strong></span><span>Tasks assigned<strong>{report.completed_tasks}</strong></span><span>Tasks deferred<strong>{report.deferred_tasks}</strong></span><span>Solver state<strong>{result?.status}</strong></span></div>}{canReview ? <><Button variant="secondary" disabled={!report?.valid || reviewed || Boolean(inProgress)} loading={action.busy} onClick={() => void approve('approve')}><ClipboardCheck size={16} />Record demo review</Button><Button disabled={!reviewed || published || Boolean(inProgress)} loading={action.busy} onClick={() => void approve('publish')}><ArrowRight size={16} />Publish demo</Button></> : <div className="small-notice">A safety officer or administrator records demo review and publishes the checked revision.</div>}<span className="tiny-note">Operational approval is always disabled.</span>{canReview && published && <div className="withdraw-control">{withdrawOpen ? <form onSubmit={event => void withdraw(event)}><label>Withdrawal note<textarea value={withdrawNote} onChange={event => setWithdrawNote(event.target.value)} required maxLength={2000} rows={3} placeholder="Explain why this published demo plan is being withdrawn…" /></label><p>Workers will no longer see this plan or its instructions. History is retained.</p><Button type="submit" variant="danger" disabled={!withdrawNote.trim()} loading={action.busy}>Confirm withdrawal</Button><button type="button" className="withdraw-cancel" onClick={() => setWithdrawOpen(false)}>Cancel</button></form> : <Button variant="ghost" onClick={() => setWithdrawOpen(true)}><X size={14} />Withdraw demo</Button>}</div>}</div></aside></div>{report && <Findings report={report} />}{result?.schedule?.deferred_task_ids.length ? <div className="panel deferred-panel"><h3>Explicitly deferred work</h3><p>These tasks remain visible and were not silently removed from the plan.</p><div>{result.schedule.deferred_task_ids.map(id => <Badge key={id} tone="amber">{id}</Badge>)}</div></div> : null}</>}
    {tab === 'Evidence' && <Evidence scenario={scenario} />}
    {tab === 'Comparison' && <ComparisonView comparison={run?.comparison || null} />}
    {tab === 'Scenario JSON' && <section className="panel"><div className="panel-heading"><div><h2>Immutable input snapshot</h2><p>Revision {detail.revision} · policy {scenario.policy.id} / {scenario.policy.version}</p></div><Button variant="secondary" onClick={() => download(`${scenario.id}.json`, scenario)}><ArrowDownToLine size={15} />Export JSON</Button></div><div className="hash-row"><span>Scenario SHA-256</span><code>{detail.scenario_hash}</code></div><div className="hash-row"><span>Policy SHA-256</span><code>{detail.policy_hash}</code></div><pre className="json-preview">{JSON.stringify(scenario, null, 2)}</pre></section>}
    {tab === 'Audit' && <AuditTrail scenarioId={id} />}
  </>
}

function Timeline({ scenario, schedule }: { scenario: Scenario; schedule: Schedule }) {
  const begin = new Date(scenario.starts_at).getTime()
  const end = new Date(scenario.ends_at).getTime()
  const fraction = (value: string) => ((new Date(value).getTime() - begin) / (end - begin)) * 100
  const ticks = Array.from({ length: 7 }, (_, index) => new Date(begin + ((end - begin) * index) / 6).toISOString())
  const bans = restrictionBands(scenario)
  return <div className="timeline-scroll"><div className="timeline-chart"><div className="timeline-axis"><span>WORKER / SKILL</span><div>{ticks.map((tick, index) => <span key={tick} style={{ left: `${index * 100 / 6}%` }}>{time(tick, scenario.timezone)}</span>)}</div></div>{scenario.workers.map(worker => <div className="timeline-row" key={worker.id}><div className="timeline-worker"><strong>{worker.id}</strong><span>{worker.skills.map(friendly).join(', ')}{!worker.acclimatized && <em> · new / returning</em>}</span></div><div className="timeline-track">{ticks.map((tick, index) => <i key={tick} className="grid-line" style={{ left: `${index * 100 / 6}%` }} />)}{bans.map((ban, index) => <div key={index} className="restriction-band" style={{ left: `${ban[0]}%`, width: `${ban[1] - ban[0]}%` }} />)}{schedule.assignments.filter(item => item.worker_id === worker.id).map(item => <div key={item.task_id} className="timeline-block work" style={{ left: `${fraction(item.starts_at)}%`, width: `${fraction(item.ends_at) - fraction(item.starts_at)}%` }} title={`${item.task_id} · ${time(item.starts_at, scenario.timezone)}–${time(item.ends_at, scenario.timezone)}`} aria-label={`${worker.id} ${item.task_id} work ${time(item.starts_at, scenario.timezone)}`}><span>{item.task_id.replace('task-', 'T')}</span></div>)}{schedule.recoveries.filter(item => item.worker_id === worker.id).map(item => <div key={item.task_id} className="timeline-block recovery" style={{ left: `${fraction(item.starts_at)}%`, width: `${fraction(item.ends_at) - fraction(item.starts_at)}%` }} title={`Protected recovery · ${time(item.starts_at, scenario.timezone)}–${time(item.ends_at, scenario.timezone)}`} aria-label={`${worker.id} protected recovery`} />)}</div></div>)}</div></div>
}
function restrictionBands(scenario: Scenario): [number, number][] {
  const start = new Date(scenario.starts_at).getTime(), end = new Date(scenario.ends_at).getTime()
  const bands: [number, number][] = []
  let bandStart: number | null = null
  const format = new Intl.DateTimeFormat('en-CA', { timeZone: scenario.timezone, year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hourCycle: 'h23' })
  for (let instant = start; instant <= end; instant += 60000) {
    const parts = Object.fromEntries(format.formatToParts(instant).map(part => [part.type, part.value]))
    const localDate = `${parts.year}-${parts.month}-${parts.day}`, localTime = `${parts.hour}:${parts.minute}`
    const restricted = instant < end && localDate >= scenario.policy.restricted_season_start && localDate <= scenario.policy.restricted_season_end && localTime >= scenario.policy.restricted_start.slice(0, 5) && localTime < scenario.policy.restricted_end.slice(0, 5)
    if (restricted && bandStart === null) bandStart = instant
    else if (!restricted && bandStart !== null) {
      bands.push([((bandStart - start) / (end - start)) * 100, ((instant - start) / (end - start)) * 100])
      bandStart = null
    }
  }
  return bands
}

function Findings({ report }: { report: Validation }) { return <section className="panel findings-panel"><div className="panel-heading"><div><h2>Independent validation</h2><p>The validator reconstructs eligibility from the original inputs and proposed assignments.</p></div><Badge tone={report.valid ? 'teal' : 'red'}>{report.valid ? 'Checks passed' : `${report.violations.length} violations`}</Badge></div><div className="finding-list">{[...report.violations, ...report.review_findings].map((finding, index) => <FindingRow key={`${finding.code}-${index}`} finding={finding} violation={index < report.violations.length} />)}{report.violations.length === 0 && <div className="finding-row"><span className="finding-icon teal"><Check size={17} /></span><div><strong>Encoded constraints passed</strong><p>Qualification, timing, evidence, recovery and resource checks passed for this demo revision.</p></div></div>}</div></section> }
function FindingRow({ finding, violation }: { finding: Finding; violation: boolean }) { return <div className="finding-row"><span className={`finding-icon ${violation ? 'red' : 'amber'}`}>{violation ? <X size={17} /> : <CircleHelp size={17} />}</span><div><strong>{friendly(finding.code)}{finding.task_id && <code>{finding.task_id}</code>}</strong><p>{finding.message}</p></div></div> }

function Evidence({ scenario }: { scenario: Scenario }) {
  const references = scenario.policy.source_urls.flatMap(value => { try { const url = new URL(value); return ['https:', 'http:'].includes(url.protocol) ? [{ href: url.href, label: url.hostname }] : [] } catch { return [] } })
  const [zone, setZone] = useState(scenario.observations[0]?.zone_id || '')
  const zones = [...new Set(scenario.observations.map(observation => observation.zone_id))]
  const values = scenario.observations.filter(observation => observation.zone_id === zone).sort((a, b) => new Date(a.observed_at).getTime() - new Date(b.observed_at).getTime())
  const min = Math.min(...values.map(item => item.wbgt_c)), max = Math.max(...values.map(item => item.wbgt_c))
  return <><div className="evidence-grid"><section className="panel"><div className="panel-heading"><div><h2>Environmental evidence</h2><p>WBGT observations in °C · {scenario.timezone}</p></div><label className="zone-picker"><span>Observation zone</span><select aria-label="Observation zone" value={zone} onChange={event => setZone(event.target.value)}>{zones.map(value => <option key={value} value={value}>{value}</option>)}</select></label></div>{values.length ? <div className="chart-wrapper"><div className="chart-caption"><strong>{min.toFixed(1)}–{max.toFixed(1)}<small> °C WBGT</small></strong><span>Fabricated scenario observations</span></div><svg className="evidence-chart" viewBox="0 0 680 180" role="img" aria-label="WBGT observations plotted from scenario data"><defs><linearGradient id="heat-fill" x1="0" x2="0" y1="0" y2="1"><stop offset="0%" stopColor="#e9a83d" stopOpacity=".26" /><stop offset="100%" stopColor="#e9a83d" stopOpacity="0" /></linearGradient></defs>{[0, 1, 2, 3].map(index => <line key={index} x1="25" x2="665" y1={25 + index * 42} y2={25 + index * 42} stroke="#e5ebe9" strokeDasharray="4 4" />)}<polygon points={`25,160 ${values.map((item, index) => `${25 + index * 640 / Math.max(1, values.length - 1)},${145 - (item.wbgt_c - min) * 110 / Math.max(2, max - min)}`).join(' ')} 665,160`} fill="url(#heat-fill)" /><polyline points={values.map((item, index) => `${25 + index * 640 / Math.max(1, values.length - 1)},${145 - (item.wbgt_c - min) * 110 / Math.max(2, max - min)}`).join(' ')} fill="none" stroke="#d39428" strokeWidth="3" strokeLinejoin="round" />{values.map((item, index) => <circle key={item.id} cx={25 + index * 640 / Math.max(1, values.length - 1)} cy={145 - (item.wbgt_c - min) * 110 / Math.max(2, max - min)} r="3.5" fill="#fff" stroke="#d39428" strokeWidth="2"><title>{time(item.observed_at, scenario.timezone)} · {item.wbgt_c}°C · {item.source}</title></circle>)}</svg><div className="chart-time"><span>{time(values[0].observed_at, scenario.timezone)}</span><span>{time(values[values.length - 1].observed_at, scenario.timezone)}</span></div></div> : <Empty title="No observations in this scenario">Heat-exposed tasks need qualifying evidence for their full interval.</Empty>}</section><aside className="panel policy-panel"><div className="panel-heading"><div><h2>Policy context</h2><p>{scenario.policy.id}</p></div></div><div className="policy-body"><span className="policy-version">VERSION {scenario.policy.version}</span><h3>Demo rules, explicit limits.</h3><p>These are unreviewed software-test thresholds. They do not establish medical fitness or authorize real work.</p><dl><div><dt>Restricted window</dt><dd>{scenario.policy.restricted_start.slice(0, 5)}–{scenario.policy.restricted_end.slice(0, 5)}</dd></div><div><dt>Protected recovery</dt><dd>{scenario.policy.recovery_minutes} min / heat task</dd></div><div><dt>Maximum evidence age</dt><dd>{scenario.policy.max_observation_age_minutes} min</dd></div></dl>{references.map(url => <a href={url.href} target="_blank" rel="noreferrer" key={url.href}>View {url.label} reference <ArrowRight size={13} /></a>)}</div></aside></div><section className="panel"><div className="panel-heading"><div><h2>Observation provenance</h2><p>Source labels, coverage and quality remain visible.</p></div></div><div className="table-scroll"><table><thead><tr><th>Observation / zone</th><th>Effective interval</th><th>WBGT</th><th>Source</th><th>Quality</th><th>Calibration</th></tr></thead><tbody>{values.map(item => <tr key={item.id}><td><strong>{item.id}</strong><small className="table-subtext">{item.zone_id}</small></td><td>{time(item.valid_from, scenario.timezone)}–{time(item.valid_until, scenario.timezone)}</td><td><strong>{item.wbgt_c.toFixed(1)} °C</strong></td><td><Badge tone={item.source === 'simulated' ? 'amber' : 'neutral'}>{friendly(item.source)}</Badge></td><td>{friendly(item.quality)}</td><td className="muted">{item.calibration_reference || 'Not provided'}</td></tr>)}</tbody></table></div></section></>
}

function ComparisonView({ comparison }: { comparison: Comparison | null }) {
  if (!comparison) return <section className="panel"><Empty title="Comparison follows a completed run" icon={<Activity size={27} />}>Generate a plan to compare the original fixed roster, the greedy baseline and CP-SAT on the same inputs.</Empty></section>
  const rows = [comparison.fixed || comparison.b0_fixed, comparison.greedy || comparison.b1_greedy, comparison.optimized || comparison.b2_cp_sat] as (ComparisonRow | undefined)[]
  const labels = ['Fixed roster', 'Greedy baseline', 'CP-SAT proposal']
  return <><div className="comparison-intro"><div><h2>Three plans. The same evidence.</h2><p>Compare actual outputs against the same independent validator.</p></div><Badge tone="amber">Software-test proxies</Badge></div><div className="comparison-grid">{rows.map((row, index) => <section className={`panel comparison-card ${index === 2 ? 'featured' : ''}`} key={labels[index]}><div className="comparison-label">B{index}{index === 2 && <Badge tone="teal">Proposed plan</Badge>}</div><h3>{labels[index]}</h3><p>{index === 0 ? 'Unchecked reference. Never a fallback.' : index === 1 ? 'Earliest feasible assignments.' : 'Completion, exposure proxy, then workload spread.'}</p>{row ? <><Status value={row.status} /><div className="comparison-metrics"><div><span>Constraint violations</span><strong className={row.validation?.violations.length ? 'text-red' : 'text-teal'}>{row.validation?.violations.length ?? '—'}</strong></div><div><span>Tasks assigned</span><strong>{row.validation?.completed_tasks ?? '—'}</strong></div><div><span>Tasks deferred</span><strong>{row.validation?.deferred_tasks ?? '—'}</strong></div><div><span>Exposure proxy</span><strong>{row.validation?.exposure_proxy.toLocaleString() ?? '—'}</strong></div><div><span>Workload spread</span><strong>{row.validation?.workload_spread_minutes ?? '—'} <small>min</small></strong></div><div><span>Runtime</span><strong>{row.elapsed_seconds?.toFixed(3) ?? '—'} <small>sec</small></strong></div></div></> : <p>No result available.</p>}</section>)}</div><div className="small-notice comparison-note"><CircleHelp size={18} />Exposure and workload spread are planning proxies. They do not measure injuries, physiology, preserved wages or validated fairness. CP-SAT improvements are reported only when supported by these actual results.</div></>
}

interface WorkerHold { worker_id: string; created_at: string; active: boolean; owner_role: string; medical_clearance: false }
function InterventionsPage({ role }: { role: string }) {
  const [records, setRecords] = useState<Intervention[] | null>(null)
  const [holds, setHolds] = useState<WorkerHold[]>([])
  const [filter, setFilter] = useState('all')
  const action = useAction()
  const load = useCallback(async () => {
    const [interventions, alerts] = await Promise.all([
      request<{items: Intervention[]}>('/interventions'),
      request<{items: WorkerHold[]}>('/workers/holds'),
    ])
    setRecords(items(interventions)); setHolds(alerts.items.filter(hold => hold.active))
  }, [])
  useEffect(() => { void action.perform(load) }, [])
  const filtered = records?.filter(record => filter === 'all' || (filter === 'pending' ? !record.verified_at && record.status.toUpperCase() !== 'VERIFIED' : record.status.toUpperCase() === 'VERIFIED'))
  return <><div className="page-heading"><div><div className="eyebrow">CLOSE THE ACTION LOOP</div><h1>Protection needs follow-through.</h1><p>Receipt and implementation are different. Keep the evidence for both.</p></div><Button variant="secondary" onClick={() => void action.perform(load)}><RefreshCw size={16} />Refresh</Button></div><ErrorBox>{action.error}</ErrorBox>{holds.length > 0 && <section className="hold-section"><div className="stop-work-banner"><Activity size={24} /><div><strong>{holds.length} active stop-work {holds.length === 1 ? 'hold' : 'holds'} · Human response required</strong><p>Contact the designated on-site responder. A safety officer owns the follow-up. Private health reports are not shown here.</p></div></div>{holds.map(hold => <HoldCard key={hold.worker_id} hold={hold} canRelease={['admin', 'safety_officer'].includes(role)} onUpdated={load} />)}</section>}<div className="stat-grid"><Stat label="Interventions issued" value={records?.length ?? '—'} icon={<Bell size={20} />} detail="Created from published demo plans" /><Stat label="Acknowledged by workers" value={records?.filter(record => record.acknowledged_at || ['ACKNOWLEDGED', 'VERIFIED'].includes(record.status.toUpperCase())).length ?? '—'} icon={<CheckCircle2 size={20} />} detail="Receipt recorded separately" tone="amber" /><Stat label="Implementation verified" value={records?.filter(record => record.verified_at || record.status.toUpperCase() === 'VERIFIED').length ?? '—'} icon={<ShieldCheck size={20} />} detail="Confirmed by a supervisor" tone="teal" /></div><div className="section-toolbar"><h2>Intervention log</h2><div className="segmented-control">{[['all', 'All'], ['pending', 'Pending'], ['verified', 'Verified']].map(([value, label]) => <button key={value} className={filter === value ? 'active' : ''} onClick={() => setFilter(value)}>{label}</button>)}</div></div>{!records ? <Loading /> : !filtered?.length ? <section className="panel"><Empty title="No interventions to show">Publish a reviewed demo plan to issue protected recovery instructions to assigned workers.</Empty></section> : <div className="intervention-grid">{filtered.map(record => <InterventionCard key={record.id} record={record} onUpdated={load} />)}</div>}</>
}
function HoldCard({ hold, canRelease, onUpdated }: { hold: WorkerHold; canRelease: boolean; onUpdated: () => Promise<void> }) {
  const [note, setNote] = useState('')
  const action = useAction()
  async function release() { await action.perform(async () => { await request(`/workers/${hold.worker_id}/release-hold`, 'POST', { note }, true); await onUpdated() }) }
  return <article className="panel hold-card" data-testid={`hold-${hold.worker_id}`}><div><strong>{hold.worker_id}</strong><p>Stop-work hold recorded {stamp(hold.created_at)}. Demo release is not medical clearance.</p></div>{canRelease ? <div className="hold-actions"><label>Demo release note<input value={note} onChange={event => setNote(event.target.value)} placeholder="Record the human follow-up in this simulation" maxLength={2000} /></label><Button variant="secondary" loading={action.busy} disabled={note.trim().length < 10} onClick={() => void release()}>Release demo hold</Button></div> : <Badge tone="red">Safety officer follow-up</Badge>}<ErrorBox>{action.error}</ErrorBox></article>
}
function InterventionCard({ record, onUpdated, worker = false, language = 'en' }: { record: Intervention; onUpdated: () => Promise<void>; worker?: boolean; language?: string }) {
  const [note, setNote] = useState('')
  const action = useAction()
  const acknowledged = Boolean(record.acknowledged_at) || ['ACKNOWLEDGED', 'VERIFIED'].includes(record.status.toUpperCase())
  const verified = Boolean(record.verified_at) || record.status.toUpperCase() === 'VERIFIED'
  const startsAt = record.payload?.starts_at || record.starts_at
  const endsAt = record.payload?.ends_at || record.ends_at
  const timezone = record.payload?.timezone || 'Asia/Dubai'
  let instruction = record.payload?.messages?.[language] || record.instructions || record.message || 'SIMULATION: Take the reserved protected recovery in the designated cooling area. Tell your supervisor if it is unavailable.'
  if (startsAt) instruction = instruction.replaceAll(startsAt, time(startsAt, timezone))
  if (endsAt) instruction = instruction.replaceAll(endsAt, time(endsAt, timezone))
  const verifiedAt = record.verified_at || record.events?.find(event => event.type === 'VERIFIED')?.created_at
  async function command() { await action.perform(async () => { await request(`/interventions/${record.id}/${worker ? 'acknowledge' : 'verify'}`, 'POST', worker ? {} : { note }, true); await onUpdated() }) }
  return <article className="panel intervention-card" data-testid={`intervention-${record.id}`} aria-label={`Intervention ${record.id}`}><div className="intervention-top"><span className="intervention-icon"><ShieldCheck size={21} /></span><Status value={verified ? 'Verified' : acknowledged ? 'Acknowledged' : record.status} /></div><h3>{friendly(record.kind || 'Protected recovery')}</h3><p className="intervention-instruction" lang={language} dir={language === 'ar' ? 'rtl' : 'ltr'}>{instruction}</p><div className="intervention-details"><span>Worker<strong>{record.worker_id}</strong></span><span>Task<strong>{record.task_id || '—'}</strong></span>{startsAt && <span>{timezone}<strong>{time(startsAt, timezone)}{endsAt && `–${time(endsAt, timezone)}`}</strong></span>}</div><div className="intervention-progress"><span className={acknowledged ? 'complete' : ''}>{acknowledged ? <CheckCircle2 size={15} /> : <Clock3 size={15} />}Worker receipt</span><span className={verified ? 'complete' : ''}>{verified ? <ShieldCheck size={15} /> : <Clock3 size={15} />}Implementation</span></div><ErrorBox>{action.error}</ErrorBox>{worker ? <Button variant={acknowledged ? 'secondary' : 'primary'} disabled={acknowledged} loading={action.busy} onClick={() => void command()}><Check size={16} />{acknowledged ? 'Acknowledged' : 'Acknowledge'}</Button> : !verified ? <><label>Verification note<textarea value={note} onChange={event => setNote(event.target.value)} placeholder="Record what you observed in this demonstration…" rows={2} maxLength={2000} /></label><Button variant="secondary" disabled={!acknowledged || !note.trim()} loading={action.busy} onClick={() => void command()}><ShieldCheck size={16} />Verify intervention</Button>{!acknowledged && <span className="tiny-note">Worker acknowledgement is required before verification.</span>}</> : <p className="verified-note"><ShieldCheck size={16} />{verifiedAt ? `Confirmed · ${stamp(verifiedAt)}` : 'Implementation confirmed'}</p>}</article>
}

function WorkerPage({ onSymptoms }: { onSymptoms: () => void }) {
  const [schedule, setSchedule] = useState<WorkerSchedule | null>(null)
  const action = useAction()
  const [language, setLanguage] = useState('en')
  const load = useCallback(async () => setSchedule(await request<WorkerSchedule>('/workers/me/schedule')), [])
  useEffect(() => { void action.perform(load) }, [])
  const intro = { en: 'Know your next task. Protect your recovery.', hi: 'अपना अगला काम जानें। विश्राम का समय सुरक्षित रखें।', ar: 'اعرف مهمتك التالية. حافظ على وقت التعافي.' }[language]
  return <>
    <div className="page-heading"><div><div className="eyebrow">YOUR WORKDAY</div><h1>My schedule</h1><p lang={language} dir={language === 'ar' ? 'rtl' : 'ltr'}>{intro}</p></div><label className="language-picker"><span>Language</span><select aria-label="Language" value={language} onChange={event => setLanguage(event.target.value)}><option value="en">English</option><option value="hi">हिन्दी</option><option value="ar">العربية</option></select></label></div>
    <ErrorBox>{action.error}</ErrorBox>
    {schedule?.stop_work && <div className="stop-work-banner" role="alert"><Activity size={25} /><div><strong>Stop work · Human response required</strong><p>Your report triggered a stop-work state. Contact the trained on-site responder and follow the site emergency procedure. This demo has not contacted emergency services.</p></div></div>}
    <div className="worker-callout"><span className="worker-callout-icon"><ShieldCheck size={28} /></span><div><strong>Your recovery is part of the plan.</strong><p>Acknowledge an instruction when received. A supervisor separately verifies that the protective action happened.</p></div><Button variant="secondary" onClick={onSymptoms}><Activity size={16} />Report symptoms</Button></div>
    {!schedule ? <Loading text="Loading your published assignments…" /> : !schedule.scenarios.length ? <section className="panel"><Empty title="No published assignments yet" icon={<Clock3 size={27} />}>A safety officer needs to review and publish a demo plan before your own assignments appear here.</Empty></section> : schedule.scenarios.map(item => {
      const timezone = item.timezone || 'Asia/Dubai'
      const events = [...item.assignments.map(assignment => ({ ...assignment, recovery: false })), ...item.recoveries.map(recovery => ({ ...recovery, recovery: true }))].sort((a, b) => new Date(a.starts_at).getTime() - new Date(b.starts_at).getTime())
      return <section className="worker-scenario" aria-label={item.name} key={item.scenario_id}>
        <div className="section-toolbar"><div><h2>{item.name}</h2><p className="muted">Revision {item.revision} · Published demo · Your assignments only</p></div><Badge tone="teal">{item.assignments.length} tasks</Badge></div>
        <div className="panel worker-task-list">{events.map(task => <div className={`worker-task ${task.recovery ? 'rest' : ''}`} key={`${task.recovery ? 'rest' : 'work'}-${task.task_id}`}>
          <div className="worker-task-time"><strong>{time(task.starts_at, timezone)}</strong><span>{time(task.ends_at, timezone)}</span></div><div className="worker-task-line"><i /></div>
          <div><span className="eyebrow">{task.recovery ? 'PROTECTED RECOVERY' : 'WORK PACKAGE'}</span><h3>{task.recovery ? 'Cooling & recovery' : task.task_id}</h3><p>{task.recovery ? `Reserved after ${task.task_id}` : `${date(task.starts_at, timezone)} · ${timezone}`}</p></div>
          {task.recovery ? <ShieldCheck size={21} /> : <Badge>{Math.round((new Date(task.ends_at).getTime() - new Date(task.starts_at).getTime()) / 60000)} min</Badge>}
        </div>)}</div>
        {item.interventions.length > 0 && <><h3 className="worker-instructions-title">Your instructions</h3><div className="intervention-grid">{item.interventions.map(record => <InterventionCard key={record.id} record={record} worker language={language} onUpdated={load} />)}</div></>}
      </section>
    })}
  </>
}

function SymptomsPage({ onBack }: { onBack: () => void }) {
  const [severity, setSeverity] = useState('mild')
  const [symptoms, setSymptoms] = useState<string[]>([])
  const [result, setResult] = useState<{ stop_work: boolean; urgent: boolean; instructions: string | string[]; dispatch_performed: false } | null>(null)
  const action = useAction()
  function submit(event: FormEvent) { event.preventDefault(); void action.perform(async () => setResult(await request('/workers/me/symptoms', 'POST', { severity, symptoms }))) }
  return <><button className="back-link" onClick={onBack}><ArrowLeft size={16} />My schedule</button><div className="page-heading"><div><div className="eyebrow">PRIVATE WORKER REPORT</div><h1>Tell us how you are feeling.</h1><p>Reports stay separate from the operational planning inputs.</p></div></div><div className="symptom-layout"><section className="panel"><div className="form-body"><div className="small-notice">Synthetic demo. If there is a real emergency, contact the site responder or local emergency services directly.</div><ErrorBox>{action.error}</ErrorBox>{result ? <div className={result.urgent ? 'symptom-result urgent' : 'symptom-result'}><Activity size={35} /><h2>{result.stop_work ? 'Stop work and seek human assistance.' : 'Your report has been recorded.'}</h2>{(Array.isArray(result.instructions) ? result.instructions : [result.instructions]).map((instruction, index) => <p key={index}>{instruction}</p>)}<strong>No emergency dispatch was performed.</strong><Button variant="secondary" onClick={onBack}>Return to my schedule</Button></div> : <form onSubmit={submit}><fieldset><legend>Symptoms in this demonstration</legend>{['Dizziness', 'Headache', 'Nausea', 'Weakness', 'Confusion', 'Fainting'].map(value => <label className="check-label" key={value}><input type="checkbox" checked={symptoms.includes(value)} onChange={event => setSymptoms(current => event.target.checked ? [...current, value] : current.filter(item => item !== value))} />{value}</label>)}</fieldset><label>Reported severity<select aria-label="Reported severity" value={severity} onChange={event => setSeverity(event.target.value)}><option value="mild">Mild symptoms</option><option value="severe">Severe symptoms / urgent help</option></select></label><p className="muted">Severe reports trigger a stop-work flag and instructions to contact a trained human responder. The demo never calls or dispatches emergency services.</p><Button type="submit" loading={action.busy} disabled={!symptoms.length}>Submit private report<ArrowRight size={16} /></Button></form>}</div></section><aside className="editor-context"><div className="context-icon"><Activity size={25} /></div><h2>Human help comes first.</h2><p>This prototype does not diagnose illness or assess fitness for work. Do not wait for an app response during a real emergency.</p><div className="small-notice">UAE ambulance: <strong>998</strong>. Calling is a human action; no call is placed by this prototype.</div></aside></div></>
}

function AuditPage() {
  const [scenarios, setScenarios] = useState<ScenarioSummary[]>([])
  const [id, setId] = useState('')
  const action = useAction()
  useEffect(() => { void action.perform(async () => { const value = items(await request<ScenarioSummary[] | {items: ScenarioSummary[]}>('/scenarios')); setScenarios(value); if (value.length) setId(value[0].id) }) }, [])
  return <><div className="page-heading"><div><div className="eyebrow">DECISION ACCOUNTABILITY</div><h1>Audit trail</h1><p>Follow scenario revisions, human reviews and intervention events.</p></div>{scenarios.length > 0 && <label className="audit-picker"><span>Scenario</span><select aria-label="Scenario" value={id} onChange={event => setId(event.target.value)}>{scenarios.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label>}</div><ErrorBox>{action.error}</ErrorBox>{id ? <AuditTrail scenarioId={id} key={id} /> : <section className="panel"><Empty title="The trail starts with an action" icon={<History size={28} />}>Create a scenario to begin recording immutable workflow events.</Empty></section>}</>
}
function AuditTrail({ scenarioId }: { scenarioId: string }) {
  const [events, setEvents] = useState<AuditEvent[] | null>(null)
  const action = useAction()
  useEffect(() => { void action.perform(async () => { const value = await request<{events: AuditEvent[]}>(`/scenarios/${scenarioId}/audit`); setEvents(value.events) }) }, [scenarioId])
  return <section className="panel audit-panel"><div className="panel-heading"><div><h2>Recorded events</h2><p>Actor identity and revision changes are recorded server-side.</p></div><Badge>{events?.length ?? '—'} events</Badge></div><ErrorBox>{action.error}</ErrorBox>{!events ? <Loading /> : events.length === 0 ? <Empty title="No recorded events yet">Events appear as this scenario moves through planning, review and intervention.</Empty> : <div className="audit-list">{events.map((event, index) => <div className="audit-event" key={event.id || index}><div className="audit-event-mark"><History size={17} /></div><div className="audit-event-content"><div><strong>{event.event_type || event.action || 'Workflow event'}</strong><time>{stamp(event.created_at || event.timestamp)}</time></div><p>{event.actor_email || event.actor_id || 'System actor'}</p><details><summary>Event details</summary><pre>{JSON.stringify(event.payload || event, null, 2)}</pre></details></div></div>)}</div>}</section>
}
function download(name: string, data: unknown) { const url = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' })); const anchor = document.createElement('a'); anchor.href = url; anchor.download = name; anchor.click(); URL.revokeObjectURL(url) }
