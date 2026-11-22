import { useCallback, useEffect, useRef, useState } from 'react'
import { Link, useParams, useSearchParams } from 'react-router-dom'

import {
  api,
  formatDuration,
  type DemoHighlight,
  type DemoMatch,
  type DemoParseJob,
  type DemoRosterPlayer,
} from '../api'
import { ErrorNote } from '../components/ErrorNote'

export function DemoReview() {
  const { demoId } = useParams()
  const [search, setSearch] = useSearchParams()
  const [file, setFile] = useState<File | null>(null)
  const [uploadPercent, setUploadPercent] = useState<number | null>(null)
  const [job, setJob] = useState<DemoParseJob | null>(null)
  const [match, setMatch] = useState<DemoMatch | null>(null)
  const [players, setPlayers] = useState<DemoRosterPlayer[]>([])
  const [playerSearch, setPlayerSearch] = useState('')
  const [rounds, setRounds] = useState<Record<string, unknown>[]>([])
  const [selected, setSelected] = useState('')
  const [highlights, setHighlights] = useState<DemoHighlight[]>([])
  const [highlightTotal, setHighlightTotal] = useState(0)
  const [warnings, setWarnings] = useState<string[]>([])
  const [tag, setTag] = useState<string[]>([])
  const [minimumScore, setMinimumScore] = useState(0)
  const [sort, setSort] = useState('score')
  const [roundFilter, setRoundFilter] = useState<number | undefined>()
  const [openHighlight, setOpenHighlight] = useState<string | null>(null)
  const [error, setError] = useState<Error | null>(null)
  const [busy, setBusy] = useState(false)
  const uploadAbort = useRef<AbortController | null>(null)

  const loadResults = useCallback(
    async (
      playerId: string,
      selectedTag = tag,
      selectedRound = roundFilter,
      offset = 0,
      append = false,
    ) => {
      if (!demoId || !playerId) return
      try {
        const [analysis, timeline] = await Promise.all([
          api.getDemoAnalysis(demoId, playerId, {
            tag: selectedTag.length ? selectedTag.join(',') : undefined,
            round_number: selectedRound,
            minimum_score: minimumScore,
            sort,
            offset,
            limit: 100,
          }),
          api.getDemoRounds(demoId, playerId),
        ])
        setHighlights((current) => append ? [...current, ...analysis.highlights] : analysis.highlights)
        setHighlightTotal(analysis.total)
        setWarnings([...new Set([...analysis.warnings, ...timeline.warnings])])
        setRounds(timeline.rounds)
      } catch (err) {
        setError(err as Error)
      }
    },
    [demoId, minimumScore, roundFilter, sort, tag],
  )

  useEffect(() => {
    const jobId = search.get('job')
    if (!demoId) return
    const loadReadyDemo = async () => {
      const [parsed, roster] = await Promise.all([
        api.getDemoMatch(demoId),
        api.getDemoPlayers(demoId),
      ])
      setMatch(parsed)
      setPlayers(roster.players)
      setWarnings([...new Set([...parsed.warnings, ...roster.warnings])])
      const first = roster.players.find((player) => player.selectable)
      if (first) setSelected((current) => current || first.player_id)
    }
    if (!jobId) {
      if (!match) {
        void api.getDemo(demoId).then((demo) => {
          if (demo.parse_status === 'parsed') void loadReadyDemo()
          else if (demo.parse_status === 'failed') setError(new Error('Demo parsing failed.'))
        }).catch((err) => setError(err as Error))
      }
      return
    }
    let cancelled = false
    let timer: number | undefined
    const poll = async () => {
      try {
        const status = await api.getDemoParseJob(jobId)
        if (cancelled) return
        setJob(status)
        if (status.status === 'done') {
          await loadReadyDemo()
          if (cancelled) return
          setSearch((previous) => {
            previous.delete('job')
            return previous
          }, { replace: true })
        } else if (status.status === 'failed' || status.status === 'cancelled') {
          setError(new Error(status.error || `Demo parsing ${status.status}.`))
        } else {
          timer = window.setTimeout(poll, 1000)
        }
      } catch (err) {
        if (!cancelled) setError(err as Error)
      }
    }
    void poll()
    return () => {
      cancelled = true
      if (timer) window.clearTimeout(timer)
    }
  }, [demoId, match, search, setSearch])

  useEffect(() => {
    void api.getSettings().then((settings) => setTag(settings.cs2_demo.default_visible_tags)).catch(() => undefined)
  }, [])

  useEffect(() => {
    if (selected) void loadResults(selected)
  }, [selected, loadResults])

  const upload = async () => {
    if (!file) return
    setBusy(true)
    setError(null)
    const controller = new AbortController()
    uploadAbort.current = controller
    try {
      const demo = await api.uploadDemo(file, (loaded, total) => {
        setUploadPercent(Math.round((loaded / total) * 100))
      }, controller.signal)
      const parseJob = await api.startDemoParse(demo.id)
      window.location.assign(`/demos/${demo.id}?job=${parseJob.id}`)
    } catch (err) {
      if (!controller.signal.aborted) setError(err as Error)
      setBusy(false)
    } finally {
      uploadAbort.current = null
    }
  }

  const visible = (player: DemoRosterPlayer) => {
    setSelected(player.player_id)
    setRoundFilter(undefined)
  }

  if (!demoId) {
    return (
      <div className="max-w-3xl pt-14">
        <p className="eyebrow">Local match analysis · no video required</p>
        <h1 className="mt-4 font-display text-5xl text-ink-100">CS2 demo</h1>
        <p className="mt-4 max-w-xl text-sm leading-relaxed text-ink-400">
          Upload a local Counter-Strike 2 <code>.dem</code> file to inspect the match roster and
          analyze supported player events. Parsing runs locally and may take a while for large demos.
        </p>
        <div
          className="mt-10 border border-dashed border-ink-700 p-8"
          onDragOver={(event) => event.preventDefault()}
          onDrop={(event) => {
            event.preventDefault()
            setFile(event.dataTransfer.files[0] ?? null)
          }}
        >
          <label className="block">
            <span className="eyebrow">Choose or drop a CS2 demo</span>
            <input
              className="field mt-3"
              type="file"
              accept=".dem,application/octet-stream"
              onChange={(event) => setFile(event.target.files?.[0] ?? null)}
            />
          </label>
          {file && (
            <p className="mt-3 text-xs text-ink-400">
              {file.name} · {(file.size / 1024 / 1024).toFixed(1)} MiB
            </p>
          )}
          {uploadPercent !== null && (
            <div className="mt-4" aria-live="polite">
              <div className="h-1 bg-ink-800">
                <div className="h-1 bg-sodium-500" style={{ width: `${uploadPercent}%` }} />
              </div>
              <p className="mt-2 text-xs text-ink-400">Uploading · {uploadPercent}%</p>
            </div>
          )}
          <button className="btn btn-primary mt-5" disabled={!file && !busy} onClick={() => busy ? uploadAbort.current?.abort() : void upload()}>
            {busy ? 'Cancel upload' : 'Upload and parse'}
          </button>
        </div>
        {error && <div className="mt-6"><ErrorNote error={error} onDismiss={() => setError(null)} /></div>}
      </div>
    )
  }

  const exportUrl = (format: 'json' | 'csv' | 'timestamps') =>
    selected ? api.demoExportUrl(demoId, selected, format) : '#'

  return (
    <div className="pt-12">
      <div className="flex flex-wrap items-end justify-between gap-5 border-b border-ink-800 pb-6">
        <div>
          <Link className="eyebrow text-sodium-500" to="/demos">CS2 demos</Link>
          <h1 className="mt-3 font-display text-4xl text-ink-100">
            {match?.header.map_name ?? 'Preparing match'}
          </h1>
          {match && <p className="mt-2 text-xs text-ink-500">{match.round_count} rounds · {match.duration_s === null ? 'duration unavailable' : formatDuration(match.duration_s)} · {match.event_counts.player_death ?? 0} death events · type {match.match_type}</p>}
          {match && Object.keys(match.match_score).length > 0 && <p className="mt-2 text-xs text-ink-400">Score by team number: {Object.entries(match.match_score).map(([team, score]) => `T${team} ${score}`).join(' · ')}</p>}
        </div>
        {selected && (
          <div className="flex gap-2 text-xs">
            <a className="btn btn-secondary" href={exportUrl('json')}>JSON</a>
            <a className="btn btn-secondary" href={exportUrl('csv')}>CSV</a>
            <a className="btn btn-secondary" href={exportUrl('timestamps')}>Timestamps</a>
          </div>
        )}
      </div>

      {job && job.status !== 'done' && (
        <section className="mt-8 border-l-2 border-sodium-500 pl-4" aria-live="polite">
          <div className="flex items-center justify-between gap-4"><p className="text-sm text-ink-100">{job.status === 'failed' ? 'Parsing failed' : job.status === 'cancelled' ? 'Parsing cancelled' : `Parsing · ${job.current_stage}`}</p>{(job.status === 'queued' || job.status === 'running') && <button className="text-xs text-ink-400 hover:text-ink-100" onClick={() => void api.cancelDemoParse(job.id).then(setJob).catch((err) => setError(err as Error))}>Cancel parse</button>}</div>
          <p className="mt-1 text-xs text-ink-500">Progress is reported by actual stage; the parser does not expose a reliable percentage.</p>
          {job.error && <p className="mt-2 text-sm text-signal-bad">{job.error}</p>}
        </section>
      )}

      {error && <div className="mt-6"><ErrorNote error={error} onDismiss={() => setError(null)} /></div>}

      {warnings.length > 0 && (
        <section className="mt-8 border border-amber-900/70 bg-amber-950/20 p-4">
          <h2 className="eyebrow text-amber-300">Data quality warnings</h2>
          <ul className="mt-2 list-disc space-y-1 pl-5 text-xs text-amber-100/80">
            {warnings.map((warning, index) => <li key={`${index}-${warning}`}>{warning}</li>)}
          </ul>
        </section>
      )}

      {match && (
        <>
          <section className="mt-8">
            <div className="flex flex-wrap items-end justify-between gap-4"><h2 className="eyebrow">Players · select a player to analyze</h2><label className="text-xs text-ink-500">Search roster<input className="field mt-1 py-2" value={playerSearch} onChange={(event) => setPlayerSearch(event.target.value)} placeholder="Name or SteamID" /></label></div>
            <div className="mt-3 overflow-x-auto border-y border-ink-800">
              <table className="w-full text-left text-sm">
                <thead className="text-xs text-ink-500"><tr><th className="py-3">Player</th><th>Team</th><th>K</th><th>D</th><th>A</th><th>ADR</th><th /></tr></thead>
                <tbody>
                  {players.filter((player) => `${player.name ?? ''} ${player.steamid ?? ''}`.toLowerCase().includes(playerSearch.toLowerCase())).map((player) => (
                    <tr key={player.player_id} className="border-t border-ink-850">
                      <td className="py-3 text-ink-100" title={player.name_history.join(' · ')}>{player.name || 'Unknown name'}{player.steamid && <span className="ml-2 text-[10px] text-ink-600">{player.steamid}</span>}</td>
                      <td className="text-ink-400">{player.team_number ?? '—'}</td>
                      <td className="numeric text-ink-200">{player.kills}</td><td className="numeric text-ink-200">{player.deaths}</td><td className="numeric text-ink-200">{player.assists}</td><td className="text-ink-600">{player.adr ?? '—'}</td>
                      <td className="text-right"><button disabled={!player.selectable} onClick={() => visible(player)} className="text-xs text-sodium-500 disabled:text-ink-700">{selected === player.player_id ? 'Selected' : player.selectable ? 'Analyze' : 'No stable ID'}</button></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>

          {selected && (
            <div className="mt-12 grid gap-10 xl:grid-cols-[minmax(0,1fr)_300px]">
              <section>
                <div className="flex flex-wrap items-end gap-4 border-b border-ink-800 pb-4">
                  <div className="mr-auto"><h2 className="eyebrow">Highlights</h2><p className="mt-1 text-xs text-ink-500">All detected candidates are retained; score only controls order.</p></div>
                  <div className="text-xs text-ink-500"><span className="eyebrow">Tags</span><div className="mt-1 flex items-center gap-2"><button className="text-sodium-500" onClick={() => setTag([])}>All</button><select className="field py-2" multiple size={3} value={tag} onChange={(event) => setTag(Array.from(event.currentTarget.selectedOptions, (option) => option.value))}>{['multi_kill', '2K', '3K', '4K', '5K', 'ace', 'clutch_1v1', 'clutch_1v2', 'clutch_1v3', 'clutch_1v4', 'clutch_1v5', 'clutch_won', 'clutch_lost', 'clutch_unknown', 'opening_kill', 'round_winning_elimination', 'special_kills', 'headshot', 'headshot_streak', 'wallbang', 'no_scope', 'through_smoke', 'blind_kill', 'knife_kill', 'awp_multi_kill', 'fast_multi_kill'].map((item) => <option key={item}>{item}</option>)}</select></div></div>
                  <label className="text-xs text-ink-500">Minimum score <input className="field mt-1 w-24 py-2" type="number" min="0" value={minimumScore} onChange={(event) => setMinimumScore(Number(event.target.value))} /></label>
                  <label className="text-xs text-ink-500">Sort <select className="field mt-1 py-2" value={sort} onChange={(event) => setSort(event.target.value)}><option value="score">Highest score</option><option value="chronological">Chronological</option><option value="kills">Kill count</option><option value="duration">Duration</option></select></label>
                </div>
                <div className="mt-5 space-y-3">
          {highlights.length === 0 && <p className="py-12 text-center text-sm text-ink-500">No highlights match these filters.</p>}
                  {highlights.map((item) => <HighlightCard key={item.highlight_id} highlight={item} initialOpen={openHighlight === item.highlight_id} />)}
                  {highlights.length < highlightTotal && <button className="btn btn-secondary w-full" onClick={() => void loadResults(selected, tag, roundFilter, highlights.length, true)}>Load more · {highlightTotal - highlights.length} remaining</button>}
                </div>
              </section>
              <aside>
                <h2 className="eyebrow border-b border-ink-800 pb-4">Round timeline</h2>
                <div className="mt-3 max-h-[70vh] space-y-1 overflow-y-auto">
                  {rounds.map((round) => {
                    const number = Number(round.number)
                    const count = Number(round.kill_count)
                    return <button key={String(round.round_id)} onClick={() => { const nextRound = roundFilter === number ? undefined : number; setRoundFilter(nextRound); setOpenHighlight(highlights.find((item) => item.round_number === number)?.highlight_id ?? null) }} className={`flex w-full items-center justify-between border-l-2 px-3 py-2 text-left text-xs ${roundFilter === number ? 'border-sodium-500 bg-ink-850' : 'border-ink-800 hover:bg-ink-850/50'}`}><span className="text-ink-200">Round {number}</span><span className="numeric text-ink-500">{count} kills</span></button>
                  })}
                </div>
                {roundFilter && <button className="mt-3 text-xs text-sodium-500" onClick={() => setRoundFilter(undefined)}>Clear round filter</button>}
              </aside>
            </div>
          )}
          <p className="mt-10 border-t border-ink-800 pt-4 text-xs text-ink-500">Times are demo ticks or estimated demo seconds. They do not map directly to a gameplay recording.</p>
        </>
      )}
    </div>
  )
}

function HighlightCard({ highlight, initialOpen }: { highlight: DemoHighlight; initialOpen: boolean }) {
  const [open, setOpen] = useState(initialOpen)
  useEffect(() => {
    if (initialOpen) setOpen(true)
  }, [initialOpen])
  const span = highlight.end_seconds !== null && highlight.start_seconds !== null
    ? `${formatDuration(highlight.start_seconds)}–${formatDuration(highlight.end_seconds)}`
    : `ticks ${highlight.start_tick}–${highlight.end_tick}`
  return (
    <details className="group border border-ink-800 bg-ink-850/30 p-4" open={open} onToggle={(event) => setOpen(event.currentTarget.open)}>
      <summary className="flex cursor-pointer list-none flex-wrap items-center gap-x-4 gap-y-2">
        <span className="numeric text-xs text-ink-500">R{highlight.round_number}</span>
        <span className="flex-1 text-sm text-ink-100">{highlight.tags.join(' + ')}</span>
        <span className="numeric text-xs text-sodium-500">{highlight.score} pts</span>
        <span className="text-xs text-ink-500">{highlight.kill_count} kills · {span}</span>
      </summary>
      <div className="mt-4 grid gap-5 border-t border-ink-800 pt-4 md:grid-cols-2">
        <div>
          <h3 className="eyebrow">Why was this selected?</h3>
          <dl className="mt-2 space-y-1 text-xs">{Object.entries(highlight.score_breakdown).map(([key, value]) => <div className="flex justify-between gap-3" key={key}><dt className="text-ink-400">{key.replaceAll('_', ' ')}</dt><dd className="numeric text-ink-200">+{value}</dd></div>)}</dl>
          {highlight.clutch && <pre className="mt-3 whitespace-pre-wrap text-[11px] text-ink-400">{JSON.stringify(highlight.clutch, null, 2)}</pre>}
        </div>
        <div>
          <h3 className="eyebrow">Supporting kills</h3>
          <ul className="mt-2 space-y-2 text-xs">{highlight.kills.map((kill, index) => <li className="border-l border-ink-700 pl-3" key={String(kill.event_id ?? index)}><span className="numeric text-ink-500">tick {String(kill.tick)}</span><span className="ml-2 text-ink-200">{String(kill.attacker_name ?? 'Unknown')} → {String(kill.victim_name ?? 'Unknown')}</span><span className="ml-2 text-ink-500">{String(kill.weapon ?? 'unknown weapon')}{kill.headshot ? ' · headshot' : ''}</span></li>)}</ul>
          <p className="mt-3 break-all text-[10px] leading-relaxed text-ink-600">Source: {highlight.source_event_ids.join(', ')}</p>
          {highlight.data_quality_warnings.map((warning) => <p className="mt-2 text-xs text-amber-300" key={warning}>{warning}</p>)}
          {highlight.timing_warning && <p className="mt-2 text-xs text-amber-300">{highlight.timing_warning}</p>}
        </div>
      </div>
    </details>
  )
}
