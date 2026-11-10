import { useEffect, useState } from 'react'
import {
  Activity, ArrowRight, ArrowUpRight, BellRing, CalendarDays, Check,
  CheckCheck, ChevronDown, CircleAlert, Clock3, HardHat, History,
  LoaderCircle, MapPin, Plus, RefreshCw, ShieldCheck, Sun, Users,
} from 'lucide-react'
import { request } from './api'
import type { DashboardScenario, DemoWorkspace, ScenarioDetail } from './types'

type Props = {
  onNew: () => void
  onOpen: (id: string) => void
  onInterventions: () => void
  onAudit: () => void
}

const label = (value: string) => value.replaceAll('_', ' ').replaceAll('.', ' ').replaceAll('-', ' ')
const localTime = (value: string, timezone: string) => new Intl.DateTimeFormat('en-GB', { hour: '2-digit', minute: '2-digit', timeZone: timezone }).format(new Date(value))
const localDate = (value: string, timezone: string) => new Intl.DateTimeFormat('en-GB', { day: 'numeric', month: 'short', timeZone: timezone }).format(new Date(value))

function StatePill({ value }: { value: string }) {
  const normalized = value.toUpperCase()
  const tone = /PUBLISHED|VERIFIED|OPTIMAL/.test(normalized) ? 'teal' : /FAIL|INFEASIBLE|INVALID|HOLD|STOP|UNAVAILABLE|REVIEW_REQUIRED/.test(normalized) ? 'coral' : /REVIEW|READY|COMPLETE|ACK/.test(normalized) ? 'amber' : 'slate'
  return <span className={`state-pill ${tone}`}><i />{label(value)}</span>
}

export default function Dashboard({ onNew, onOpen, onInterventions, onAudit }: Props) {
  const [workspace, setWorkspace] = useState<DemoWorkspace | null>(null)
  const [selectedId, setSelectedId] = useState('')
  const [detail, setDetail] = useState<ScenarioDetail | null>(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)
  const [filter, setFilter] = useState('all')

  async function load() {
    setLoading(true)
    setError('')
    try {
      const value = await request<DemoWorkspace>('/demo/workspace')
      setWorkspace(value)
      setSelectedId(current => value.scenarios.some(scenario => scenario.id === current) ? current : value.active_scenario_id || value.scenarios[0]?.id || '')
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'The operations workspace could not be loaded.')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { void load() }, [])
  useEffect(() => {
    if (!selectedId) return
    let live = true
    setDetail(null)
    void request<ScenarioDetail>(`/scenarios/${selectedId}`).then(value => {
      if (live) setDetail(value)
    }).catch(reason => { if (live) setError(reason instanceof Error ? reason.message : 'The selected shift could not be loaded.') })
    return () => { live = false }
  }, [selectedId])

  const selected = workspace?.scenarios.find(scenario => scenario.id === selectedId)
  const currentDetail = detail?.id === selectedId ? detail : null
  const timezone = currentDetail?.scenario.timezone || workspace?.worksite.timezone || 'Asia/Dubai'
  const metrics = workspace?.metrics
  const verification = metrics && metrics.total_interventions ? Math.round(metrics.verified_interventions / metrics.total_interventions * 100) : 0
  const displayedScenarios = workspace?.scenarios.filter(scenario => filter === 'all' || (filter === 'published' ? scenario.status === 'PUBLISHED' : scenario.status !== 'PUBLISHED' && scenario.status !== 'WITHDRAWN')) || []
  const sourceCurve = selectedId === workspace?.active_scenario_id ? workspace.curve : currentDetail?.scenario.observations || []
  const zone = sourceCurve[0]?.zone_id
  const curve = sourceCurve.filter(point => point.zone_id === zone).sort((a, b) => new Date(a.observed_at).getTime() - new Date(b.observed_at).getTime())

  return <div className="operations-dashboard">
    <div className="operations-heading">
      <div><div className="eyebrow"><span className="replay-dot" />HEAT OPERATIONS / SYNTHETIC REPLAY</div><h1>{workspace?.worksite.name || 'Your operations workspace'}</h1><p><MapPin size={15} />{workspace?.worksite.location || 'Loading worksite context…'}<span className="heading-separator">/</span>{workspace?.worksite.replay_label || 'Fabricated workers, measured workflow.'}</p></div>
      <div className="operations-heading-actions"><button className="button secondary icon-only" aria-label="Refresh workspace" onClick={() => void load()} disabled={loading}><RefreshCw size={18} className={loading ? 'spin' : ''} /></button><button className="button primary" onClick={onNew}><Plus size={18} />New scenario</button></div>
    </div>

    {error && <div className="error-box" role="alert"><CircleAlert size={20} /><div>{error}<button className="text-action" onClick={() => void load()}>Try again</button></div></div>}
    {!workspace && !error ? <div className="workspace-loading"><LoaderCircle className="spin" size={28} /><h2>Bringing your operations into view</h2><p>Loading actual plans, environmental evidence and recorded actions.</p></div> : !workspace ? <div className="panel workspace-unavailable"><h2>The workspace is unavailable.</h2><p>No placeholder statistics are displayed. Retry the connection or create a scenario to continue planning.</p><button className="button primary" onClick={onNew}><Plus size={18} />New scenario</button></div> : <>
      <div className="dashboard-context-line"><span>Workspace totals</span><span><ShieldCheck size={14} /> All data fabricated. Every workflow action is recorded.</span></div>
      <div className="operations-metrics">
        <Metric icon={<Users size={22} />} name="Workers in the workspace" value={metrics!.workers} detail={`${workspace.workers.filter(worker => worker.acclimatized === false).length} new or returning workers`} />
        <Metric icon={<ShieldCheck size={22} />} name="Protected recovery" value={metrics!.protected_recovery_minutes} unit="min" detail="Reserved in published demo plans" tone="teal" />
        <Metric icon={<CheckCheck size={22} />} name="Implementation verified" value={verification} unit="%" detail={`${metrics!.verified_interventions} of ${metrics!.total_interventions} interventions verified`} progress={verification} />
        <Metric icon={<BellRing size={22} />} name="Items needing attention" value={metrics!.attention_items} detail="Human follow-up remains open" tone="coral" onClick={onInterventions} />
      </div>

      <div className="shift-control-bar"><div><CalendarDays size={18} /><span>Inspect a shift</span></div><label><span className="sr-only">Selected shift</span><select aria-label="Selected shift" value={selectedId} onChange={event => setSelectedId(event.target.value)}>{workspace.scenarios.map(scenario => <option key={scenario.id} value={scenario.id}>{scenario.name} · {localDate(scenario.starts_at, timezone)}</option>)}</select><ChevronDown size={16} /></label>{selected && <span className="shift-window">{localTime(selected.starts_at, timezone)}–{localTime(selected.ends_at, timezone)}<span>{timezone}</span></span>}</div>

      <div className="dashboard-primary-grid">
        <section className="panel environment-panel"><div className="panel-heading"><div><div className="eyebrow">ENVIRONMENT & WORK WINDOW</div><h2>The heat has a pattern.<br className="mobile-only" /> Your plan should respond.</h2></div><span className="source-tag"><span />Simulated WBGT</span></div><HeatCurve curve={curve} selected={selected} detail={currentDetail} timezone={timezone} /><div className="environment-footnote"><span><i className="curve-legend-dot" /> {zone ? label(zone) : 'Worksite evidence'}</span><span><i className="restriction-legend-dot" />Midday restriction from the selected policy</span><button onClick={() => selectedId && onOpen(selectedId)}>Inspect evidence<ArrowUpRight size={15} /></button></div></section>
        <aside className="attention-panel"><div className="attention-panel-heading"><span className="attention-eyebrow"><CircleAlert size={16} /> HUMAN FOLLOW-UP</span><h2>Keep the action<br />loop moving.</h2><p>Make the next protective step visible.</p></div><div className="attention-items">{workspace.attention.slice(0, 4).map(item => <button className="attention-item" key={item.id} onClick={() => item.scenario_id && !/HOLD|ACK|VERIF|INTERVENT/i.test(item.kind) ? onOpen(item.scenario_id) : onInterventions()}><span className={`attention-item-icon ${/HOLD|STOP|FAIL/i.test(item.kind) ? 'coral' : 'amber'}`}>{/HOLD|STOP/i.test(item.kind) ? <CircleAlert size={18} /> : /VERIF/i.test(item.kind) ? <ShieldCheck size={18} /> : <Clock3 size={18} />}</span><span><strong>{item.title}</strong><small>{item.detail}</small></span><ArrowUpRight size={15} /></button>)}{workspace.attention.length === 0 && <div className="attention-complete"><Check size={22} /><strong>No open follow-up items</strong><p>Recorded actions are up to date.</p></div>}</div><button className="attention-footer" onClick={onInterventions}>Open the intervention log<ArrowRight size={17} /></button></aside>
      </div>

      <section className="operations-board"><div className="section-toolbar"><div><div className="eyebrow">THE OPERATIONS BOARD</div><h2>A complete view of the planning loop.</h2></div><div className="segmented-control">{[['all', 'All plans'], ['published', 'Published'], ['attention', 'Needs action']].map(([value, title]) => <button key={value} className={filter === value ? 'active' : ''} onClick={() => setFilter(value)}>{title}</button>)}</div></div><div className="scenario-board">{displayedScenarios.map((scenario, index) => <ScenarioCard key={scenario.id} scenario={scenario} timezone={timezone} index={index} selected={selectedId === scenario.id} onSelect={() => setSelectedId(scenario.id)} onOpen={() => onOpen(scenario.id)} />)}{displayedScenarios.length === 0 && <div className="panel board-empty"><h3>No plans in this view</h3><p>Select another filter or create a scenario.</p></div>}</div></section>

      <div className="dashboard-secondary-grid"><section className="panel selected-plan-panel"><div className="panel-heading"><div><div className="eyebrow">SELECTED SHIFT</div><h2>Work, then protected recovery.</h2><p>{selected?.name || 'Select a scenario on the operations board.'}</p></div>{selected && <button className="text-action" onClick={() => onOpen(selected.id)}>Open plan<ArrowUpRight size={15} /></button>}</div><PlanPreview detail={currentDetail} timezone={timezone} hasSelection={Boolean(selected)} /></section><section className="panel activity-panel"><div className="panel-heading"><div><div className="eyebrow">RECORDED, NOT ASSUMED</div><h2>Recent activity</h2></div><button className="icon-button" aria-label="Open audit trail" onClick={onAudit}><ArrowUpRight size={19} /></button></div><div className="dashboard-activity">{workspace.activity.slice(0, 5).map(event => <button key={event.id} onClick={() => event.scenario_id ? onOpen(event.scenario_id) : onAudit()}><span className="activity-mark">{/verified|published|approved|review/i.test(event.action) ? <Check size={16} /> : <History size={16} />}</span><span><strong>{label(event.action)}</strong><small>{event.actor_label}</small></span><time>{new Date(event.created_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}</time></button>)}{workspace.activity.length === 0 && <p className="dashboard-empty-copy">Workflow events appear here as actions are recorded.</p>}</div></section></div>

      <section className="panel roster-panel"><div className="panel-heading"><div><div className="eyebrow">THE PEOPLE BEHIND THE PLAN</div><h2>Team readiness</h2></div><span className="muted">{workspace.workers.length} synthetic worker profiles</span></div><div className="table-scroll"><table><thead><tr><th>Worker</th><th>Qualification</th><th>Acclimatization</th><th>Planned work</th><th>Recovery</th><th>Availability</th></tr></thead><tbody>{workspace.workers.map((worker, index) => <tr key={worker.id}><td><div className="roster-person"><span className={`roster-avatar avatar-${index % 4}`}>{worker.display_name.split(' ').map(part => part[0]).slice(0, 2).join('')}</span><span><strong>{worker.display_name}</strong><small>{worker.id}</small></span></div></td><td>{worker.skills.map(label).join(', ') || '—'}</td><td><span className={`readiness-label ${worker.acclimatized === null ? 'unknown' : worker.acclimatized ? 'experienced' : 'returning'}`}><i />{worker.acclimatized === null ? 'Not recorded' : worker.acclimatized ? 'Acclimatized' : 'New / returning'}</span></td><td>{worker.work_minutes} min</td><td><span className="recovery-value">{worker.recovery_minutes} min</span></td><td><StatePill value={worker.status} /></td></tr>)}</tbody></table></div></section>
    </>}
  </div>
}

function Metric({ icon, name, value, unit, detail, tone = '', progress, onClick }: { icon: React.ReactNode; name: string; value: number; unit?: string; detail: string; tone?: string; progress?: number; onClick?: () => void }) {
  return <div className={`operation-metric ${tone}`}><div className="operation-metric-header"><span>{name}</span>{icon}</div><div className="operation-metric-value"><strong>{value.toLocaleString()}</strong>{unit && <span>{unit}</span>}{onClick && <button aria-label="View attention items" onClick={onClick}><ArrowUpRight size={21} /></button>}</div>{progress !== undefined && <div className="metric-progress"><span style={{ width: `${Math.min(100, Math.max(0, progress))}%` }} /></div>}<p>{detail}</p></div>
}

function ScenarioCard({ scenario, timezone, index, selected, onSelect, onOpen }: { scenario: DashboardScenario; timezone: string; index: number; selected: boolean; onSelect: () => void; onOpen: () => void }) {
  const problem = scenario.violation_count !== null && scenario.violation_count > 0
  return <article className={`scenario-operation-card ${selected ? 'selected' : ''}`}><div className="scenario-card-top"><span className="scenario-card-index">{String(index + 1).padStart(2, '0')} / DEMO SHIFT</span><StatePill value={scenario.status} /></div><button className="scenario-card-title" onClick={onOpen}>{scenario.name}</button><p className="scenario-card-window"><CalendarDays size={14} />{localDate(scenario.starts_at, timezone)}<span>·</span>{localTime(scenario.starts_at, timezone)}–{localTime(scenario.ends_at, timezone)}</p><div className="scenario-card-resources"><span><Users size={16} />{scenario.worker_count} workers</span><span><HardHat size={16} />{scenario.task_count} packages</span></div><div className="scenario-card-result">{scenario.completed_tasks === null ? <><Clock3 size={15} /><span>{scenario.status === 'REVIEW_REQUIRED' ? 'No admissible proposal — review inputs' : 'Awaiting a planning run'}</span></> : problem ? <><CircleAlert size={15} /><span>{scenario.violation_count} encoded constraint {scenario.violation_count === 1 ? 'finding' : 'findings'}</span></> : <><ShieldCheck size={15} /><span>{scenario.completed_tasks} assigned{scenario.deferred_tasks ? ` · ${scenario.deferred_tasks} explicitly deferred` : ' · checks passed'}</span></>}</div><div className="scenario-card-footer"><button onClick={onSelect} className={selected ? 'is-selected' : ''}>{selected ? <Check size={14} /> : <Activity size={14} />}{selected ? 'In view' : 'Preview shift'}</button><button aria-label={`Open ${scenario.name}`} onClick={onOpen}>Open plan<ArrowUpRight size={15} /></button></div></article>
}

function HeatCurve({ curve, selected, detail, timezone }: { curve: DemoWorkspace['curve']; selected?: DashboardScenario; detail: ScenarioDetail | null; timezone: string }) {
  if (!curve.length || !selected) return <div className="dashboard-chart-empty"><Sun size={32} /><strong>No WBGT observations for this shift</strong><p>Missing evidence stays visible. A weather estimate does not replace worksite WBGT.</p></div>
  const start = new Date(selected.starts_at).getTime(), end = new Date(selected.ends_at).getTime()
  const low = Math.floor(Math.min(...curve.map(point => point.wbgt_c)) - 1)
  const high = Math.ceil(Math.max(...curve.map(point => point.wbgt_c)) + 1)
  const x = (instant: number) => 48 + Math.max(0, Math.min(1, (instant - start) / (end - start))) * 894
  const y = (value: number) => 202 - ((value - low) / Math.max(1, high - low)) * 162
  const points = curve.map(point => `${x(new Date(point.observed_at).getTime())},${y(point.wbgt_c)}`).join(' ')
  const peak = Math.max(...curve.map(point => point.wbgt_c))
  const bands: [number, number][] = []
  if (detail) {
    const policy = detail.scenario.policy
    let bandStart: number | null = null
    const format = new Intl.DateTimeFormat('en-CA', { timeZone: timezone, year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hourCycle: 'h23' })
    for (let instant = start; instant <= end; instant += 60_000) {
      const parts = Object.fromEntries(format.formatToParts(instant).map(part => [part.type, part.value]))
      const day = `${parts.year}-${parts.month}-${parts.day}`, hour = `${parts.hour}:${parts.minute}`
      const restricted = instant < end && day >= policy.restricted_season_start && day <= policy.restricted_season_end && hour >= policy.restricted_start.slice(0, 5) && hour < policy.restricted_end.slice(0, 5)
      if (restricted && bandStart === null) bandStart = instant
      else if (!restricted && bandStart !== null) { bands.push([bandStart, instant]); bandStart = null }
    }
  }
  return <div className="operations-curve"><div className="curve-summary"><div><strong>{peak.toFixed(1)}<span>°C</span></strong><span>Peak simulated WBGT</span></div><div className="curve-restriction-note"><span><i />OUTDOOR RESTRICTION</span><strong>{detail ? `${detail.scenario.policy.restricted_start.slice(0, 5)}–${detail.scenario.policy.restricted_end.slice(0, 5)}` : 'Loading policy…'}</strong></div></div><svg viewBox="0 0 980 250" role="img" aria-label={`WBGT observations for ${selected.name}; peak ${peak.toFixed(1)} degrees Celsius, simulated`}><defs><linearGradient id="operations-wbgt-area" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stopColor="#cf7946" stopOpacity=".20" /><stop offset="100%" stopColor="#cf7946" stopOpacity=".015" /></linearGradient><pattern id="restriction-hatch" width="7" height="7" patternTransform="rotate(135)" patternUnits="userSpaceOnUse"><line x1="0" x2="0" y1="0" y2="7" stroke="#e7d6ba" strokeWidth="2" /></pattern></defs>{bands.map(([left, right], index) => <g key={index}><rect x={x(left)} y="20" width={x(right) - x(left)} height="184" fill="#eee3d1" opacity=".55" /><rect x={x(left)} y="20" width={x(right) - x(left)} height="184" fill="url(#restriction-hatch)" opacity=".65" /></g>)}{[0, 1, 2, 3].map(index => { const value = low + (high - low) * index / 3; return <g key={index}><line x1="48" x2="942" y1={y(value)} y2={y(value)} stroke="#e4e2d8" strokeDasharray="4 6" /><text x="27" y={y(value) + 4} textAnchor="end">{Math.round(value)}°</text></g> })}<polygon points={`${x(new Date(curve[0].observed_at).getTime())},204 ${points} ${x(new Date(curve[curve.length - 1].observed_at).getTime())},204`} fill="url(#operations-wbgt-area)" /><polyline points={points} fill="none" stroke="#c2784e" strokeWidth="3.5" strokeLinejoin="round" strokeLinecap="round" />{curve.map(point => <circle key={`${point.observed_at}-${point.zone_id}`} cx={x(new Date(point.observed_at).getTime())} cy={y(point.wbgt_c)} r="4.2" stroke="#c2784e" fill="#fffdf8" strokeWidth="2"><title>{localTime(point.observed_at, timezone)} · {point.wbgt_c.toFixed(1)} °C · {point.source}</title></circle>)}{[0, 1, 2, 3, 4, 5, 6].map(index => { const instant = start + (end - start) * index / 6; return <text key={index} x={x(instant)} y="238" textAnchor="middle">{localTime(new Date(instant).toISOString(), timezone)}</text> })}</svg></div>
}

function PlanPreview({ detail, timezone, hasSelection }: { detail: ScenarioDetail | null; timezone: string; hasSelection: boolean }) {
  if (!hasSelection) return <div className="dashboard-preview-empty"><CalendarDays size={26} /><h3>No shift selected yet.</h3><p>Create a scenario to begin planning, or select an existing shift on the operations board.</p></div>
  if (!detail) return <div className="dashboard-preview-empty"><LoaderCircle size={24} className="spin" /><p>Loading the selected shift…</p></div>
  const schedule = detail.latest_run?.result?.schedule
  if (!schedule) return <div className="dashboard-preview-empty"><Clock3 size={26} /><h3>This shift has no generated proposal.</h3><p>Open it to inspect the inputs and start a planning run.</p></div>
  const start = new Date(detail.scenario.starts_at).getTime(), end = new Date(detail.scenario.ends_at).getTime()
  const percent = (value: string) => (new Date(value).getTime() - start) / (end - start) * 100
  const workers = detail.scenario.workers.slice(0, 4)
  return <><div className="preview-timeline"><div className="preview-axis"><span>WORKER / SKILL</span><div>{[0, 1, 2, 3, 4].map(index => <span key={index} style={{ left: `${index * 25}%` }}>{localTime(new Date(start + (end - start) * index / 4).toISOString(), timezone)}</span>)}</div></div>{workers.map(worker => <div key={worker.id} className="preview-worker"><div><strong>{worker.id}</strong><span>{label(worker.skills[0] || '')}</span></div><div className="preview-worker-track">{[0, 1, 2, 3, 4].map(index => <i className="preview-gridline" key={index} style={{ left: `${index * 25}%` }} />)}{schedule.assignments.filter(item => item.worker_id === worker.id).map(item => <span className="preview-work" key={item.task_id} style={{ left: `${percent(item.starts_at)}%`, width: `${percent(item.ends_at) - percent(item.starts_at)}%` }} title={`${item.task_id} ${localTime(item.starts_at, timezone)}–${localTime(item.ends_at, timezone)}`} />)}{schedule.recoveries.filter(item => item.worker_id === worker.id).map(item => <span className="preview-recovery" key={item.task_id} style={{ left: `${percent(item.starts_at)}%`, width: `${percent(item.ends_at) - percent(item.starts_at)}%` }} title={`Protected recovery ${localTime(item.starts_at, timezone)}–${localTime(item.ends_at, timezone)}`} />)}</div></div>)}</div><div className="preview-legend"><span><i />Work package</span><span><i />Protected recovery</span><small>Preview · first {workers.length} workers</small></div></>
}
