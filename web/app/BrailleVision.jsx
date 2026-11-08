import * as echarts from 'echarts@6';

// ---------- Insights (computed every minute by the tiger-sync task from Tiger Data, stored at app/insights) ----------
const LEVEL_TEXT = { crit: 'Critical', warn: 'Needs attention', info: 'Note', ok: 'Healthy' };
const levelColor = (C, lvl) => lvl === 'crit' ? C.bad : lvl === 'warn' ? C.warn : lvl === 'ok' ? C.ok : C.faint;
const agoText = (ms) => { if (!ms) return '—'; const s = Math.max(0, Math.round((Date.now() - ms) / 1000)); return s < 60 ? s + ' s ago' : s < 3600 ? Math.round(s / 60) + ' min ago' : Math.round(s / 3600) + ' h ago'; };
const sigList = (ins) => Object.keys((ins && ins.signals) || {}).sort().map(k => ins.signals[k]);

function EChart({ option, height }) {
  const ref = React.useRef(null), chart = React.useRef(null);
  React.useEffect(() => {
    const el = ref.current; if (!el) return;
    chart.current = echarts.init(el, null, { renderer: 'svg' });
    const ro = new ResizeObserver(() => chart.current && chart.current.resize());
    ro.observe(el);
    return () => { ro.disconnect(); chart.current && chart.current.dispose(); chart.current = null; };
  }, []);
  React.useEffect(() => { if (chart.current && option) chart.current.setOption(option, true); }, [option]);
  return <div ref={ref} style={{ width: '100%', height }} />;
}

function InsightsPanel({ ins, C, narrow }) {
  const [, tick] = React.useState(0);
  const [open, setOpen] = React.useState(null);
  React.useEffect(() => { const t = setInterval(() => tick(x => x + 1), 10000); return () => clearInterval(t); }, []);
  const label = { fontSize: 11, letterSpacing: '0.06em', textTransform: 'uppercase', color: C.faint, fontWeight: 600 };
  const series = (ins && ins.series) || [];
  const latencyOption = React.useMemo(() => {
    const t = series.map(s => new Date(s.t).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }));
    const axisText = { color: C.faint, fontSize: 10 };
    return {
      animation: false, grid: { left: 40, right: 36, top: 26, bottom: 22 },
      legend: { top: 0, right: 0, itemWidth: 10, itemHeight: 6, textStyle: { color: C.muted, fontSize: 11 }, data: ['p95 model', 'median model', 'p95 round trip', 'frames'] },
      tooltip: { trigger: 'axis', backgroundColor: C.surface, borderColor: C.hair, textStyle: { color: C.text, fontSize: 12 }, valueFormatter: v => v == null ? '—' : Math.round(v) },
      xAxis: { type: 'category', data: t, axisLabel: { ...axisText, interval: 14 }, axisLine: { lineStyle: { color: C.hair } }, axisTick: { show: false } },
      yAxis: [{ type: 'value', name: 'ms', nameTextStyle: axisText, axisLabel: axisText, splitLine: { lineStyle: { color: C.hair } } },
              { type: 'value', name: 'frames', nameTextStyle: axisText, axisLabel: axisText, splitLine: { show: false } }],
      series: [
        { name: 'frames', type: 'bar', yAxisIndex: 1, data: series.map(s => s.frames || null), itemStyle: { color: C.hair }, barWidth: '60%' },
        { name: 'p95 model', type: 'line', data: series.map(s => s.p95 ?? null), showSymbol: false, lineStyle: { width: 2, color: C.accent }, itemStyle: { color: C.accent } },
        { name: 'median model', type: 'line', data: series.map(s => s.p50 ?? null), showSymbol: false, lineStyle: { width: 1.5, color: C.ink }, itemStyle: { color: C.ink } },
        { name: 'p95 round trip', type: 'line', data: series.map(s => s.rtt95 ?? null), showSymbol: false, lineStyle: { width: 1.5, type: 'dashed', color: C.muted }, itemStyle: { color: C.muted } },
      ],
    };
  }, [ins && ins.computedAt, C.text]);
  const classes = (ins && (ins.classes || Object.keys(ins.labels || {}))) || [];
  const classOption = React.useMemo(() => {
    const lab = (ins && ins.labels) || {};
    const axisText = { color: C.faint, fontSize: 10 };
    return {
      animation: false, grid: { left: 40, right: 8, top: 26, bottom: 22 },
      legend: { top: 0, right: 0, itemWidth: 10, itemHeight: 6, textStyle: { color: C.muted, fontSize: 11 } },
      tooltip: { trigger: 'axis', backgroundColor: C.surface, borderColor: C.hair, textStyle: { color: C.text, fontSize: 12 },
        formatter: (ps) => { const c = ps[0].name, l = lab[c] || {}; return `<b>${c}</b><br/>Frames seen ${l.modelFrames || 0}<br/>Confirmed ${l.confirms || 0}<br/>Avg confidence ${l.avgConf != null ? Math.round(l.avgConf * 100) + '%' : '—'}`; } },
      xAxis: { type: 'category', data: classes, axisLabel: { color: C.muted, fontSize: 11 }, axisLine: { lineStyle: { color: C.hair } }, axisTick: { show: false } },
      yAxis: { type: 'value', axisLabel: axisText, splitLine: { lineStyle: { color: C.hair } } },
      series: [
        { name: 'frames seen', type: 'bar', data: classes.map(c => (lab[c] || {}).modelFrames || 0), itemStyle: { color: C.faint, borderRadius: [3, 3, 0, 0] }, barGap: '20%' },
        { name: 'confirmed', type: 'bar', data: classes.map(c => (lab[c] || {}).confirms || 0), itemStyle: { color: C.accent, borderRadius: [3, 3, 0, 0] } },
      ],
    };
  }, [ins && ins.computedAt, C.text]);

  if (!ins) return <div className="w-full" style={{ padding: 24, fontSize: 13, color: C.muted }}>Waiting for the first insights from Tiger Data. They refresh every minute.</div>;
  const sigs = sigList(ins), byId = Object.fromEntries(sigs.map(s => [s.id, s]));
  const stale = Date.now() - (ins.computedAt || 0) > 3 * 60000;
  const health = stale ? 'warn' : ins.health;
  const flagged = sigs.filter(s => s.level === 'crit' || s.level === 'warn').length;
  const kpi = (title, v, unit, id, hint) => {
    const s = byId[id], col = s ? levelColor(C, s.level === 'info' ? 'ok' : s.level) : C.faint;
    return (
      <div title={s ? s.detail : hint} style={{ padding: '10px 12px', borderRadius: 12, boxShadow: `inset 0 0 0 1px ${C.hair}`, minWidth: 0 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 11, color: C.muted, whiteSpace: 'nowrap' }}>
          <span style={{ width: 6, height: 6, borderRadius: 9999, background: s ? col : 'transparent', boxShadow: s ? 'none' : `inset 0 0 0 1px ${C.hair}` }} />{title}
        </div>
        <div style={{ fontSize: 22, fontWeight: 600, letterSpacing: '-0.02em', fontVariantNumeric: 'tabular-nums', marginTop: 4 }}>
          {v == null ? <span style={{ color: C.faint }}>—</span> : v}<span style={{ fontSize: 12, fontWeight: 500, color: C.muted, marginLeft: 3 }}>{v == null ? '' : unit}</span>
        </div>
      </div>
    );
  };
  const r = ins.recent || {}, d = ins.day || {};
  return (
    <div className="w-full" style={{ flex: 1, minHeight: 0, display: 'flex', flexDirection: 'column', gap: 14, overflowY: narrow ? 'visible' : 'auto' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 14, flexWrap: 'wrap' }}>
        <span style={{ width: 10, height: 10, borderRadius: 9999, background: levelColor(C, health) }} />
        <div style={{ fontSize: 18, fontWeight: 600, letterSpacing: '-0.01em' }}>{stale ? 'Insights are stale' : LEVEL_TEXT[health] || 'Healthy'}</div>
        <div style={{ fontSize: 12, color: C.muted }}>
          {stale ? `Last computed ${agoText(ins.computedAt)}. Check the Tiger Data sync task.` : `${flagged ? flagged + ' warning' + (flagged > 1 ? 's' : '') : 'No warnings'} · signals over the last 15 min, trends over 24 h`}
        </div>
        <div style={{ marginLeft: 'auto', fontSize: 12, color: C.faint }}>Tiger Data · {(ins.totalEvents || 0).toLocaleString()} events · updated {agoText(ins.computedAt)}</div>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: narrow ? 'repeat(2, minmax(0,1fr))' : 'repeat(6, minmax(0,1fr))', gap: 8 }}>
        {kpi('Model p95', r.p95 != null ? Math.round(r.p95) : null, 'ms', 'latency', 'Shown while the camera runs')}
        {kpi('Round trip p95', r.rtt95 != null ? Math.round(r.rtt95) : null, 'ms', 'roundtrip', 'Measured from the next camera run')}
        {kpi('Frame rate', byId.fps ? byId.fps.value : null, 'fps', 'fps', 'Shown while the camera runs')}
        {kpi('Time to confirm', byId.confirm ? byId.confirm.value : null, 's', 'confirm', 'Needs 3 confirmations')}
        {kpi('Confirmed', d.confirms != null ? Math.round(d.confirms) : null, '24 h', 'coverage', '')}
        {kpi('Servo pace', byId.servo ? byId.servo.value : null, 's/letter', 'servo', 'Needs a word spelled on the servos')}
      </div>

      <div style={{ flex: 1, minHeight: 0, display: 'grid', gridTemplateColumns: narrow ? '1fr' : 'minmax(260px, 340px) minmax(0, 1fr)', gap: 20 }}>
        <section style={{ minHeight: 0, overflowY: narrow ? 'visible' : 'auto' }}>
          <div style={{ ...label, marginBottom: 8 }}>Signals</div>
          {sigs.map(s => {
            const isOpen = open === s.id;
            return (
              <button key={s.id} onClick={() => setOpen(isOpen ? null : s.id)} aria-expanded={isOpen}
                style={{ display: 'block', width: '100%', textAlign: 'left', padding: '9px 0', borderTop: `1px solid ${C.hair}`, cursor: 'pointer', background: 'transparent' }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                  <span style={{ width: 7, height: 7, borderRadius: 9999, background: levelColor(C, s.level), flexShrink: 0 }} />
                  <span style={{ fontSize: 13, fontWeight: 500, color: C.text }}>{s.title}</span>
                  {s.seen24h > 0 && <span style={{ fontSize: 11, color: C.faint }}>flagged {s.seen24h}× today</span>}
                  <span style={{ marginLeft: 'auto', fontSize: 11, color: levelColor(C, s.level), fontWeight: 500 }}>{s.level === 'ok' ? 'OK' : LEVEL_TEXT[s.level]}</span>
                </div>
                <div style={{ fontSize: 12, color: C.muted, lineHeight: 1.45, marginTop: 3, paddingLeft: 15, ...(isOpen ? {} : { display: '-webkit-box', WebkitLineClamp: 1, WebkitBoxOrient: 'vertical', overflow: 'hidden' }) }}>{s.detail}</div>
              </button>
            );
          })}
          {!sigs.length && <div style={{ fontSize: 12, color: C.muted }}>Not enough data yet.</div>}
        </section>
        <section style={{ minHeight: 0, display: 'grid', gridTemplateRows: narrow ? 'auto auto' : 'minmax(0, 1.25fr) minmax(0, 1fr)', gap: 14 }}>
          <div style={{ minHeight: 0, display: 'flex', flexDirection: 'column' }}>
            <div style={label}>Latency and load · last 60 min</div>
            <div style={{ flex: 1, minHeight: 170 }}><EChart option={latencyOption} height="100%" /></div>
          </div>
          <div style={{ minHeight: 0, display: 'flex', flexDirection: 'column' }}>
            <div style={label}>Per class · 24 h</div>
            <div style={{ flex: 1, minHeight: 140 }}><EChart option={classOption} height="100%" /></div>
          </div>
        </section>
      </div>
      <div style={{ fontSize: 11, color: C.faint, lineHeight: 1.5 }}>
        Computed in Tiger Data: hypertable braille_events, continuous aggregates braille_minute and braille_label_minute (refreshed every minute), percentile_agg and time_bucket_gapfill, warning history in braille_signals, columnstore compression after 7 days. No images are stored.
      </div>
    </div>
  );
}

const DOTS = { a:'1', b:'12', c:'14', d:'145', e:'15', f:'124', g:'1245', h:'125', i:'24', j:'245', k:'13', l:'123', m:'134', n:'1345', o:'135', p:'1234', q:'12345', r:'1235', s:'234', t:'2345', u:'136', v:'1236', w:'2456', x:'1346', y:'13456', z:'1356' };
const DIGIT_LETTER = { '1':'a','2':'b','3':'c','4':'d','5':'e','6':'f','7':'g','8':'h','9':'i','0':'j' };
const toMask = (s) => s.split('').reduce((m, d) => m | (1 << (Number(d) - 1)), 0);
const SKIP = /(^|\/)(\.git|node_modules|__pycache__|\.venv|venv|\.ipynb_checkpoints|\.idea|\.vscode)(\/|$)|(^|\/)\.DS_Store$/;
const DEFAULTS = { threshold: 0.5, stableFrames: 3, dwellMs: 1200, autoSend: true, speak: true, capitals: true };

function brailleCells(text) {
  const out = []; let inNum = false;
  for (const ch of String(text || '').toLowerCase()) {
    if (/[0-9]/.test(ch)) {
      if (!inNum) { out.push({ ch: '#', mask: toMask('3456'), sign: true }); inNum = true; }
      out.push({ ch, mask: toMask(DOTS[DIGIT_LETTER[ch]]) });
      continue;
    }
    inNum = false;
    if (DOTS[ch]) out.push({ ch, mask: toMask(DOTS[ch]) });
    else if (/[\s_-]/.test(ch) && out.length && out[out.length - 1].mask !== 0) out.push({ ch: ' ', mask: 0 });
  }
  while (out.length && out[out.length - 1].mask === 0) out.pop();
  return out;
}

// Text tab: Unified English Braille, grade 1 (uncontracted) with capitals, numbers and common punctuation.
const PUNCT = { ',': ['2'], '.': ['256'], '?': ['236'], '!': ['235'], ';': ['23'], ':': ['25'], "'": ['3'], '’': ['3'], '‘': ['3'],
  '-': ['36'], '(': ['5', '126'], ')': ['5', '345'], '/': ['456', '34'] };
function textToBraille(text, capitals) {
  const src = String(text || ''); const cells = []; const unsupported = new Set();
  let inNum = false, capsWord = false, quoteOpen = false;
  const push = (ch, dots, sign) => cells.push({ ch, mask: toMask(dots), sign });
  for (let i = 0; i < src.length; i++) {
    const ch = src[i], lower = ch.toLowerCase();
    if (/[0-9]/.test(ch)) { if (!inNum) { push('#', '3456', 'number'); inNum = true; } push(ch, DOTS[DIGIT_LETTER[ch]]); capsWord = false; continue; }
    if ((ch === '.' || ch === ',') && inNum && /[0-9]/.test(src[i + 1] || '')) { push(ch, ch === '.' ? '256' : '2'); continue; }
    if (DOTS[lower]) {
      if (inNum && /[a-j]/.test(lower)) push('', '56', 'letter');
      inNum = false;
      const startsWord = !(i > 0 && /[a-z]/i.test(src[i - 1]));
      if (startsWord) {
        const w = (src.slice(i).match(/^[a-z]+/i) || [''])[0];
        capsWord = capitals && w.length >= 2 && w === w.toUpperCase();
        if (capsWord) { push('', '6', 'capital word'); push('', '6', 'capital word'); }
      }
      if (capitals && ch !== lower && !capsWord) push('', '6', 'capital');
      push(ch, DOTS[lower]); continue;
    }
    inNum = false; capsWord = false;
    if (ch === '\n') { while (cells.length && cells[cells.length - 1].mask === 0 && !cells[cells.length - 1].br) cells.pop(); cells.push({ ch: '\n', mask: 0, br: true }); continue; }
    if (/\s/.test(ch)) { const last = cells[cells.length - 1]; if (last && last.mask !== 0) cells.push({ ch: ' ', mask: 0 }); continue; }
    if (ch === '"' || ch === '“' || ch === '”') { const open = ch === '“' || (ch === '"' && !quoteOpen); quoteOpen = open; push(ch, open ? '236' : '356'); continue; }
    if (PUNCT[ch]) { PUNCT[ch].forEach((d, k) => push(k === PUNCT[ch].length - 1 ? ch : '', d, k === PUNCT[ch].length - 1 ? undefined : 'prefix')); continue; }
    unsupported.add(ch);
  }
  while (cells.length && cells[cells.length - 1].mask === 0) cells.pop();
  return { cells, unsupported: [...unsupported] };
}
const toUnicode = (cells) => cells.map(c => c.br ? '\n' : String.fromCharCode(0x2800 + c.mask)).join('');
const signLabel = (c) => c.sign === 'number' ? '#' : c.sign === 'capital' ? '⇧' : c.sign === 'capital word' ? '⇧' : c.sign === 'letter' ? '·' : c.sign === 'prefix' ? '' : c.ch === ' ' ? '' : c.ch;

const dotList = (mask) => [1,2,3,4,5,6].filter(d => (mask >> (d - 1)) & 1);

function BrailleCell({ mask, size, active, ink, hair, accent }) {
  const order = [0, 3, 1, 4, 2, 5];
  const dl = dotList(mask);
  return (
    <div role="img" aria-label={dl.length ? 'dots ' + dl.join(' ') : 'blank cell'}
      style={{ display: 'grid', gridTemplateColumns: `repeat(2, ${size}px)`, gap: Math.round(size * 0.55), padding: Math.round(size * 0.55),
        borderRadius: Math.round(size * 0.6), boxShadow: active ? `0 0 0 1.5px ${accent}` : `0 0 0 1px ${hair}`, transition: 'box-shadow 160ms' }}>
      {order.map(i => {
        const on = (mask >> i) & 1;
        return <span key={i} style={{ width: size, height: size, borderRadius: '50%',
          background: on ? ink : 'transparent',
          boxShadow: on ? `0 ${Math.max(1, size / 10)}px ${Math.max(2, size / 5)}px rgba(0,0,0,0.18), inset 0 ${-size / 8}px ${size / 5}px rgba(255,255,255,0.12)` : `inset 0 0 0 1px ${hair}`,
          transform: on ? 'scale(1)' : 'scale(0.82)', transition: 'transform 180ms ease, background 180ms ease' }} />;
      })}
    </div>
  );
}

function UserDiv() {
  const { theme } = useTheme();
  const L = theme === 'light';
  const C = {
    text: L ? '#18181b' : '#f4f4f5', muted: L ? '#71717a' : '#a1a1aa', faint: L ? '#a1a1aa' : '#52525b',
    hair: L ? 'rgba(24,24,27,0.12)' : 'rgba(244,244,245,0.14)', surface: L ? '#ffffff' : '#18181b', stage: L ? '#f4f4f5' : '#0f0f11',
    ink: L ? '#18181b' : '#f4f4f5', accent: L ? '#c2410c' : '#fb923c', ok: L ? '#15803d' : '#4ade80', bad: L ? '#b91c1c' : '#f87171', warn: L ? '#b45309' : '#fbbf24',
  };
  const { url, error: ingressError } = useIngressUrl('braille-vision');
  const repoStore = useStorageUrl('repo');
  const repoAddr = relPathAddress('repo-upload');
  const [repoInfo, setRepoInfo] = React.useState(null);
  const [upl, setUpl] = React.useState(null);
  const folderRef = React.useRef(null), zipRef = React.useRef(null);
  const settingsAddr = relPathAddress('settings');
  const firmwareAddr = relPathAddress('firmware');

  const [settings, setSettings] = React.useState(DEFAULTS);
  const [firmware, setFirmware] = React.useState('');
  const [health, setHealth] = React.useState(null);
  const [running, setRunning] = React.useState(false);
  const [devices, setDevices] = React.useState([]);
  const [deviceId, setDeviceId] = React.useState('');
  const [result, setResult] = React.useState(null);
  const [error, setError] = React.useState(null);
  const [current, setCurrent] = React.useState(null);
  const [play, setPlay] = React.useState({ word: '', cells: [], idx: -1, token: 0 });
  const [history, setHistory] = React.useState([]);
  const [serialOn, setSerialOn] = React.useState(false);
  const [serialErr, setSerialErr] = React.useState(null);
  const [serialLog, setSerialLog] = React.useState([]);
  const [boardMode, setBoardMode] = React.useState(null); // 'esp32' = her braille_serial firmware, 'arduino' = Braille Vision sketch
  const [fwView, setFwView] = React.useState('esp32');
  const [firmwareEsp, setFirmwareEsp] = React.useState('');
  const heardRef = React.useRef(false);
  const lineWaiters = React.useRef([]), boardModeRef = React.useRef(null), espRun = React.useRef(0), espDone = React.useRef(Promise.resolve()), playTokenRef = React.useRef(0);
  const [manual, setManual] = React.useState('');
  const [showFw, setShowFw] = React.useState(false);
  const [copied, setCopied] = React.useState('');
  const [narrow, setNarrow] = React.useState(false);
  const readTab = () => { try { const t = new URLSearchParams(window.location.search).get('tab'); return t === 'text' || t === 'insights' ? t : 'camera'; } catch (e) { return 'camera'; } };
  const [tab, setTab] = React.useState(readTab);
  const [text, setText] = React.useState('');
  const textInit = React.useRef(false), draftTimer = React.useRef(null), chunkTimers = React.useRef([]);
  const composerAddr = relPathAddress('composer');
  const insightsAddr = relPathAddress('insights');
  const [ins, setIns] = React.useState(null);
  React.useEffect(() => { if (!insightsAddr) return; const un = FS.onValue(FS.ref(FS.db, insightsAddr), s => setIns(s.val())); return () => un(); }, [insightsAddr]);

  const rootRef = React.useRef(null), videoRef = React.useRef(null), capRef = React.useRef(null);
  const streamRef = React.useRef(null), runningRef = React.useRef(false), timerRef = React.useRef(null), busyRef = React.useRef(false);
  const urlRef = React.useRef(null), settingsRef = React.useRef(DEFAULTS), streakRef = React.useRef({ label: null, count: 0 });
  const lastSentRef = React.useRef(null), portRef = React.useRef(null), writerRef = React.useRef(null), readerRef = React.useRef(null);
  const saveTimer = React.useRef(null), tickRef = React.useRef(null), sendRef = React.useRef(null);
  const sessionRef = React.useRef(Math.random().toString(36).slice(2) + Date.now().toString(36));
  const lastRttRef = React.useRef(0), errSentRef = React.useRef({});
  const doneWaiters = React.useRef([]); // resolve when a playback (by token) has shown its last letter
  urlRef.current = url; settingsRef.current = settings;

  React.useEffect(() => {
    if (!settingsAddr) return;
    const un = FS.onValue(FS.ref(FS.db, settingsAddr), s => { const v = s.val() || {}; setSettings(p => ({ ...DEFAULTS, ...v, metadata: undefined })); });
    return () => un();
  }, [settingsAddr]);
  React.useEffect(() => {
    if (!firmwareAddr) return;
    const un = FS.onValue(FS.ref(FS.db, firmwareAddr + '/code'), s => setFirmware(s.val() || ''));
    const un2 = FS.onValue(FS.ref(FS.db, relPathAddress('firmware-esp32') + '/code'), s => setFirmwareEsp(s.val() || ''));
    return () => { un(); un2(); };
  }, [firmwareAddr]);
  React.useEffect(() => {
    if (!url) return;
    let alive = true;
    fetch(url + '/healthz').then(r => r.ok ? r.json() : Promise.reject(new Error('HTTP ' + r.status)))
      .then(j => alive && setHealth(j)).catch(e => alive && setHealth({ ok: false, error: e.message }));
    return () => { alive = false; };
  }, [url]);
  React.useEffect(() => {
    const el = rootRef.current; if (!el) return;
    const ro = new ResizeObserver(([e]) => setNarrow(e.contentRect.width < 820));
    ro.observe(el); return () => ro.disconnect();
  }, []);

  React.useEffect(() => {
    const onPop = () => setTab(readTab());
    window.addEventListener('popstate', onPop); return () => window.removeEventListener('popstate', onPop);
  }, []);
  const switchTab = (t) => {
    if (t === tab) return;
    if (t === 'text' && runningRef.current) stop();  // insights can watch a live camera run
    setTab(t);
    const u = new URL(window.location.href);
    if (t === 'camera') u.searchParams.delete('tab'); else u.searchParams.set('tab', t);
    window.history.replaceState(window.history.state, '', u.toString());
  };
  React.useEffect(() => {
    if (!composerAddr) return;
    const un = FS.onValue(FS.ref(FS.db, composerAddr), s => { const v = s.val(); if (!textInit.current) { textInit.current = true; if (v && typeof v.text === 'string') setText(v.text); } });
    return () => un();
  }, [composerAddr]);
  const editText = (v) => {
    setText(v); textInit.current = true;
    clearTimeout(draftTimer.current);
    draftTimer.current = setTimeout(() => { if (composerAddr) { const now = Date.now(); FS.update(FS.ref(FS.db, composerAddr), { text: v, 'metadata/type': 'entry', 'metadata/updatedAt': now }); } }, 600);
  };

  React.useEffect(() => {
    if (!repoAddr) return;
    const un = FS.onValue(FS.ref(FS.db, repoAddr), s => setRepoInfo(s.val()));
    return () => un();
  }, [repoAddr]);
  const setFolderInput = React.useCallback((el) => { folderRef.current = el; if (el) { el.setAttribute('webkitdirectory', ''); el.setAttribute('directory', ''); el.setAttribute('mozdirectory', ''); } }, []);

  const uploadRepo = async (fileList, kind) => {
    const files = Array.from(fileList || []).map(f => ({ f, rel: f.webkitRelativePath || f.name })).filter(x => !SKIP.test(x.rel));
    if (!files.length) return;
    if (!repoStore.upload) { setUpl({ error: 'Storage is still connecting. Try again in a moment.' }); return; }
    const stamp = new Date().toISOString().replace(/[:.]/g, '-');
    const total = files.length, bytes = files.reduce((a, x) => a + x.f.size, 0);
    setUpl({ done: 0, total, bytes });
    let done = 0; const failed = []; const queue = files.slice();
    const worker = async () => {
      while (queue.length) {
        const x = queue.shift();
        try { await repoStore.upload(stamp + '/' + x.rel, x.f); } catch (e) { failed.push(x.rel + ': ' + (e.message || e)); }
        done += 1; setUpl(u => ({ ...u, done }));
      }
    };
    await Promise.all([0, 1, 2, 3].map(worker));
    const root = kind === 'zip' ? files[0].rel.replace(/\.zip$/i, '') : (files[0].rel.split('/')[0] || 'repository');
    const now = Date.now();
    await FS.set(FS.ref(FS.db, repoAddr), { kind, root, prefix: stamp, files: total, bytes, failed: failed.length,
      status: failed.length ? 'partial' : 'uploaded', uploadedAt: now, metadata: { type: 'entry', createdAt: now, updatedAt: now } });
    setUpl(u => ({ ...u, finished: true, failed }));
  };
  const requestHosting = () => {
    const r = repoInfo; if (!r) return;
    sendMessage(`I uploaded my repository (${r.root}). Please host it as-is.\n\n<details>\n<summary>Instructions</summary>\n\nUploaded through the Braille Vision app: ${r.files} files, ${(r.bytes / 1e6).toFixed(1)} MB, kind=${r.kind}, prefix=${r.prefix}. Files are under /cloudspace/app-storage/apps/braille-vision/app/repo/${r.prefix}/ and the record is apps/braille-vision/app/repo-upload. Run the user's own code unmodified as the detector backend; do not write or substitute a model. Only add a thin adapter if the app needs one.\n</details>`);
  };

  const writeBytes = async (arr) => { if (!writerRef.current) return false; try { await writerRef.current.write(new Uint8Array(arr)); return true; } catch (e) { setSerialErr(e.message); return false; } };
  const ascii = (s) => Array.from(s).map(c => c.charCodeAt(0));

  const saveSetting = (k, v) => {
    setSettings(s => ({ ...s, [k]: v }));
    clearTimeout(saveTimer.current);
    saveTimer.current = setTimeout(() => {
      if (settingsAddr) FS.update(FS.ref(FS.db, settingsAddr), { [k]: v, 'metadata/type': 'entry', 'metadata/updatedAt': Date.now() });
      if (k === 'dwellMs' && boardModeRef.current === 'arduino') writeBytes(ascii('#' + v + '\n'));
    }, 400);
  };

  // Plays cells on screen and, when connected, streams them to the servo board in chunks that fit its 96-byte queue.
  // Playback report for the Tiger Data usage log (detector queues it; the tiger-sync task ships it).
  const logPlayback = (ev) => { const u = urlRef.current; if (u) fetch(u + '/event', { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Session-Id': sessionRef.current }, body: JSON.stringify({ kind: 'playback', ...ev }) }).catch(() => {}); };
  // Errors seen by the app (board or detector) go into the same log, at most once per message every 30 s.
  const logError = (source, message) => {
    const u = urlRef.current, key = source + message, now = Date.now();
    if (!u || !message || now - (errSentRef.current[key] || 0) < 30000) return;
    errSentRef.current[key] = now;
    fetch(u + '/event', { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Session-Id': sessionRef.current }, body: JSON.stringify({ kind: 'error', source, message: String(message).slice(0, 300) }) }).catch(() => {});
  };
  React.useEffect(() => { if (serialErr) logError('board', serialErr); }, [serialErr]);
  React.useEffect(() => { if (error) logError('detector', error); }, [error]);
  const playCells = async (word, cells, source, speak) => {
    if (!cells.length) return;
    chunkTimers.current.forEach(clearTimeout); chunkTimers.current = [];
    const token = ++playTokenRef.current;
    const done = new Promise(res => doneWaiters.current.push({ token, resolve: res }));
    const hw = !!writerRef.current && boardModeRef.current === 'esp32';
    const t0 = Date.now();
    done.then(() => logPlayback({ word: String(word || '').slice(0, 200), source, hardware: !!writerRef.current, cells: cells.length, durationMs: Date.now() - t0, stopped: playTokenRef.current !== token }));
    setPlay({ word, cells, idx: hw ? -2 : 0, token, source, hw });
    if (hw) playOnEsp(cells, token);
    else if (writerRef.current) {
      const N = 64, dwell = settingsRef.current.dwellMs, masks = cells.map(c => 0x40 | c.mask);
      for (let k = 0; k * N < masks.length; k++) {
        const last = (k + 1) * N >= masks.length;
        const bytes = [...(k === 0 ? [0x21] : []), ...masks.slice(k * N, (k + 1) * N), ...(last ? [0x0A] : [])];
        if (k === 0) await writeBytes(bytes);
        else chunkTimers.current.push(setTimeout(() => writeBytes(bytes), Math.max(0, (k * N - 16) * dwell)));
      }
    }
    if (speak && window.speechSynthesis) {
      window.speechSynthesis.cancel();
      window.speechSynthesis.speak(new SpeechSynthesisUtterance(word));
    }
    return done;
  };
  // Her ESP32 firmware (braille_serial.ino): one lowercase letter + newline, then "Showing x on channels:…" and "Back at rest.".
  // Same protocol as braille_output.py: wait for each acknowledgment before the next letter, 0.8 s gap between words.
  const waitLine = (pred, ms) => new Promise((resolve, reject) => {
    const w = { pred, resolve, timer: setTimeout(() => { lineWaiters.current = lineWaiters.current.filter(x => x !== w); reject(new Error('timeout')); }, ms) };
    lineWaiters.current.push(w);
  });
  const playOnEsp = (cells, token) => {
    const prev = espDone.current; const run = ++espRun.current;
    const live = () => espRun.current === run && playTokenRef.current === token;
    const mark = (patch) => setPlay(p => p.token !== token ? p : { ...p, ...patch });
    espDone.current = (async () => {
      await prev.catch(() => {});
      for (let i = 0; i < cells.length && live(); i++) {
        const c = cells[i], ch = (c.ch || '').toLowerCase();
        if (c.mask === 0) { mark({ idx: i, hwState: 'gap' }); await new Promise(r => setTimeout(r, 800)); continue; }
        if (c.sign || !/^[a-z]$/.test(ch)) continue;
        mark({ idx: i, hwState: 'moving' });
        if (!(await writeBytes(ascii(ch + '\n')))) break;
        try {
          const restart = (l) => l.includes('Turn on servo power') || l.startsWith('rst:') || l.includes('PCA9685 did not respond');
          const shown = await waitLine(l => l.startsWith('Showing ') || restart(l), 15000);
          if (restart(shown)) throw new Error(shown.includes('PCA9685') ? shown + ' Check the PCA9685 wiring.' : 'The ESP32 restarted while moving the servos. This is usually the servo supply dipping: power the servos from their own 5–6 V supply on the PCA9685 V+ terminal, with grounds connected.');
          if (!shown.startsWith('Showing ' + ch)) throw new Error('ESP32 reported a different letter. Is another program writing to the port?');
          mark({ hwState: 'showing' });
          const rest = await waitLine(l => l === 'Back at rest.' || restart(l), 15000);
          if (restart(rest)) throw new Error('The ESP32 restarted while moving the servos. This is usually the servo supply dipping: power the servos from their own 5–6 V supply on the PCA9685 V+ terminal, with grounds connected.');
        } catch (e) { setSerialErr(e.message === 'timeout' ? `ESP32 did not acknowledge “${ch}”. ${heardRef.current ? 'It stopped replying mid-letter.' : 'It has sent nothing since you connected.'} Press EN/RST on the board, then Disconnect and Connect again.` : e.message); break; }
      }
      if (playTokenRef.current === token) mark({ idx: -1, hwState: null });
    })();
  };
  const stopPlayback = () => {
    chunkTimers.current.forEach(clearTimeout); chunkTimers.current = [];
    espRun.current++;
    if (boardModeRef.current === 'arduino') writeBytes([0x21]);
    playTokenRef.current++;
    setPlay(p => ({ ...p, idx: -1, token: playTokenRef.current }));
    if (window.speechSynthesis) window.speechSynthesis.cancel();
  };
  const sendWord = async (word) => {
    const cells = brailleCells(word);
    if (!cells.length) return Promise.resolve();
    lastSentRef.current = word;
    setHistory(h => [{ word, at: Date.now(), servo: !!writerRef.current }, ...h.filter(x => x.word !== word)].slice(0, 6));
    return playCells(word, cells, 'camera', settingsRef.current.speak);
  };
  sendRef.current = sendWord;
  // Ends the model's hold for this visitor so the next frame starts a new detection cycle.
  const releaseCycle = () => { const u = urlRef.current; if (u) fetch(u + '/release', { method: 'POST', headers: { 'X-Session-Id': sessionRef.current } }).catch(() => {}); };
  React.useEffect(() => {
    if (play.idx !== -1) return;
    const t = play.token, ready = doneWaiters.current.filter(w => w.token <= t);
    doneWaiters.current = doneWaiters.current.filter(w => w.token > t);
    ready.forEach(w => w.resolve());
  }, [play.idx, play.token]);

  React.useEffect(() => {
    if (play.hw || play.idx < 0 || play.idx >= play.cells.length) return;
    const token = play.token;
    const t = setTimeout(() => setPlay(p => p.token !== token ? p : { ...p, idx: p.idx + 1 < p.cells.length ? p.idx + 1 : -1 }), settingsRef.current.dwellMs);
    return () => clearTimeout(t);
  }, [play.idx, play.token]);

  const handleResult = (j, ms) => {
    const s = settingsRef.current;
    if (j.state) {
      // Julie's braille_assist pipeline: confirms after >3 s, then holds until the word has been fully shown; we release it here.
      const dets = j.detections || [];
      setResult({ dets, w: j.width, h: j.height, ms: Math.round(ms), serverMs: j.ms, state: j.state, scores: j.scores });
      if (j.state.confirmed && dets.length) {
        setCurrent(dets[0].label);
        if (s.autoSend) Promise.resolve(sendRef.current(dets[0].label)).finally(releaseCycle);
        else releaseCycle();
      }
      return;
    }
    const dets = (j.detections || []).filter(d => d.confidence >= s.threshold);
    setResult({ dets, w: j.width, h: j.height, ms: Math.round(ms), serverMs: j.ms });
    const top = dets.length ? dets[0].label : null;
    const st = streakRef.current;
    if (top === st.label) st.count += 1; else { st.label = top; st.count = 1; }
    if (top && st.count >= s.stableFrames) {
      setCurrent(top);
      if (s.autoSend && top !== lastSentRef.current) sendRef.current(top);
    }
    if (!top && st.count >= s.stableFrames * 2) lastSentRef.current = null;
  };

  const tick = async () => {
    if (!runningRef.current || busyRef.current) return;
    const v = videoRef.current, c = capRef.current;
    if (v && c && v.videoWidth && urlRef.current) {
      busyRef.current = true;
      try {
        const W = 640, H = Math.round(v.videoHeight * W / v.videoWidth);
        c.width = W; c.height = H; c.getContext('2d').drawImage(v, 0, 0, W, H);
        const blob = await new Promise(r => c.toBlob(r, 'image/jpeg', 0.72));
        const t0 = performance.now();
        const res = await fetch(urlRef.current + '/detect', { method: 'POST', headers: { 'Content-Type': 'image/jpeg', 'X-Session-Id': sessionRef.current, 'X-Prev-Rtt': String(lastRttRef.current || '') }, body: blob });
        if (!res.ok) throw new Error('Detector returned HTTP ' + res.status);
        const j = await res.json();
        lastRttRef.current = Math.round(performance.now() - t0);
        if (runningRef.current) { handleResult(j, performance.now() - t0); setError(null); }
      } catch (e) { setError(e.message || String(e)); }
      finally { busyRef.current = false; }
    }
    if (runningRef.current) timerRef.current = setTimeout(() => tickRef.current(), 120);
  };
  tickRef.current = tick;

  const stopCamera = () => { if (streamRef.current) streamRef.current.getTracks().forEach(t => t.stop()); streamRef.current = null; if (videoRef.current) videoRef.current.srcObject = null; };
  const startCamera = async (id) => {
    stopCamera();
    const stream = await navigator.mediaDevices.getUserMedia({ audio: false, video: id ? { deviceId: { exact: id } } : { facingMode: 'environment', width: { ideal: 1280 } } });
    streamRef.current = stream;
    const v = videoRef.current; v.srcObject = stream; await v.play().catch(() => {});
    const list = (await navigator.mediaDevices.enumerateDevices()).filter(d => d.kind === 'videoinput');
    setDevices(list);
    setDeviceId(stream.getVideoTracks()[0].getSettings().deviceId || id || '');
  };
  const start = async () => {
    setError(null);
    try { await startCamera(deviceId || null); releaseCycle(); runningRef.current = true; setRunning(true); streakRef.current = { label: null, count: 0 }; tickRef.current(); }
    catch (e) { setError(e.name === 'NotAllowedError' ? 'Camera permission was denied. Allow camera access for mana.space in the browser address bar.' : (e.message || String(e))); }
  };
  const stop = () => { runningRef.current = false; setRunning(false); clearTimeout(timerRef.current); stopCamera(); setResult(null); };
  const switchCamera = async (id) => { setDeviceId(id); if (runningRef.current) { try { await startCamera(id); } catch (e) { setError(e.message); } } };

  const serialSupported = typeof navigator !== 'undefined' && 'serial' in navigator;
  const readSerial = async (port) => {
    const dec = new TextDecoder(); let buf = '';
    try {
      const reader = port.readable.getReader(); readerRef.current = reader;
      while (true) {
        const { value, done } = await reader.read(); if (done) break;
        buf += dec.decode(value); let i;
        while ((i = buf.indexOf('\n')) >= 0) {
          const line = buf.slice(0, i).trim(); buf = buf.slice(i + 1);
          if (!line) continue;
          heardRef.current = true; setSerialLog(l => [...l.slice(-3), line]);
          if (line === 'Enter one lowercase letter (a-z).') setSerialErr(e => (e && e.includes('PCA9685')) ? null : e);
          const w = lineWaiters.current.find(x => x.pred(line));
          if (w) { clearTimeout(w.timer); lineWaiters.current = lineWaiters.current.filter(x => x !== w); w.resolve(line); }
        }
      }
    } catch (e) {} finally { try { readerRef.current && readerRef.current.releaseLock(); } catch (e) {} }
  };
  const disconnectSerial = async () => {
    try { readerRef.current && await readerRef.current.cancel(); } catch (e) {}
    try { writerRef.current && writerRef.current.releaseLock(); } catch (e) {}
    try { portRef.current && await portRef.current.close(); } catch (e) {}
    portRef.current = writerRef.current = readerRef.current = null; setSerialOn(false);
    espRun.current++; boardModeRef.current = null; setBoardMode(null);
  };
  const connectSerial = async () => {
    setSerialErr(null);
    try {
      const port = await navigator.serial.requestPort();
      const info = port.getInfo ? port.getInfo() : {};
      await port.open({ baudRate: 115200 });
      heardRef.current = false;
      portRef.current = port; writerRef.current = port.writable.getWriter(); setSerialOn(true); setSerialLog([]);
      readSerial(port);
      if (!info.usbVendorId) setSerialErr('This port is not a USB device (it may be Bluetooth). Disconnect and pick the one named usbserial, usbmodem, SLAB_USBtoUART or wchusbserial.');
      // Reset the board the way Arduino Serial Monitor / esptool do (RTS pulse with DTR low), then leave RTS released
      // so an ESP32 auto-reset circuit can't hold it in reset or in download mode.
      let booted = null;
      try {
        await port.setSignals({ dataTerminalReady: false, requestToSend: true });
        await new Promise(r => setTimeout(r, 120));
        await port.setSignals({ dataTerminalReady: false, requestToSend: false });
        await new Promise(r => setTimeout(r, 350));
        await port.setSignals({ dataTerminalReady: true, requestToSend: false });
        booted = await waitLine(x => x === 'Enter one lowercase letter (a-z).' || x === 'READY' || x.includes('PCA9685') || x.includes('waiting for download'), 4000).catch(() => null);
      } catch (e) {}
      // Identify the firmware: her ESP32 sketch answers "?" with its prompt; the Arduino sketch prints READY/DWELL.
      let mode = null, l = booted;
      if (l && l.includes('waiting for download')) { setSerialErr('The ESP32 is in download (boot) mode. Press its EN/RST button once, then Disconnect and Connect again.'); }
      for (let k = 0; k < 4 && !mode; k++) {
        if (!l) { await writeBytes(ascii('?\n')); l = await waitLine(x => x === 'Enter one lowercase letter (a-z).' || x === 'READY' || x.startsWith('DWELL') || x.includes('PCA9685'), 1200).catch(() => null); }
        if (l && !l.includes('waiting for download')) {
          mode = l === 'READY' || l.startsWith('DWELL') ? 'arduino' : 'esp32';
          if (l.includes('PCA9685')) setSerialErr(l + ' Check the PCA9685 wiring (VCC 3.3 V, GND, SDA, SCL).');
        }
        l = null;
      }
      if (!mode) setSerialErr(s => s || 'Connected, but the board sent nothing back. Check that braille_serial.ino is uploaded, that Arduino Serial Monitor is closed, and (ESP32-C3/S3) that Tools → USB CDC On Boot was Enabled when uploading. Then press EN/RST and reconnect.');
      mode = mode || 'esp32';
      boardModeRef.current = mode; setBoardMode(mode);
      if (mode === 'arduino') await writeBytes(ascii('#' + settingsRef.current.dwellMs + '\n'));
    } catch (e) { if (e.name !== 'NotFoundError') setSerialErr(e.name === 'InvalidStateError' || /open/i.test(e.message || '') ? 'Could not open the port. Close Arduino Serial Monitor (or any other program using it) and try again.' : (e.message || String(e))); }
  };
  React.useEffect(() => {
    if (!serialSupported) return;
    const onDis = (e) => { if (e.target === portRef.current) { portRef.current = writerRef.current = readerRef.current = null; setSerialOn(false); espRun.current++; boardModeRef.current = null; setBoardMode(null); setSerialErr('Servo board was unplugged.'); } };
    navigator.serial.addEventListener('disconnect', onDis);
    return () => navigator.serial.removeEventListener('disconnect', onDis);
  }, []);
  React.useEffect(() => () => { runningRef.current = false; clearTimeout(timerRef.current); clearTimeout(saveTimer.current); clearTimeout(draftTimer.current); chunkTimers.current.forEach(clearTimeout); stopCamera(); disconnectSerial(); }, []);

  const copy = async (text, key) => { const r = await copyText(text); setCopied(r === 'failed' ? 'failed' : key); setTimeout(() => setCopied(''), 1600); };

  // ---------- presentation ----------
  const camPlay = play.source === 'text' ? { word: '', cells: [], idx: -1 } : play;
  const wordCells = brailleCells(camPlay.idx >= 0 ? camPlay.word : (current || camPlay.word || ''));
  const shownWord = camPlay.idx >= 0 ? camPlay.word : (current || camPlay.word || '');
  const bigMask = camPlay.idx >= 0 && camPlay.cells[camPlay.idx] ? camPlay.cells[camPlay.idx].mask : 0;
  const detectorOk = !!url && health && health.ok;
  const detectorLabel = ingressError ? 'Offline' : !url ? 'Connecting…' : !health ? 'Checking…' : health.ok ? (health.source === 'fallback' ? 'Stand-in (testing only)' : health.model) : 'Unreachable';
  const btn = (primary) => ({ height: 36, padding: '0 14px', borderRadius: 9999, fontSize: 13, fontWeight: 500, cursor: 'pointer', whiteSpace: 'nowrap',
    background: primary ? C.ink : 'transparent', color: primary ? (L ? '#fff' : '#18181b') : C.text, border: primary ? 'none' : `1px solid ${C.hair}` });
  const label = { fontSize: 11, letterSpacing: '0.06em', textTransform: 'uppercase', color: C.faint, fontWeight: 600 };
  const Status = (name, ok, text) => (
    <div style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 12, color: C.muted, whiteSpace: 'nowrap' }}>
      <span style={{ width: 6, height: 6, borderRadius: 9999, background: ok ? C.ok : C.faint }} />
      <span style={{ color: C.faint }}>{name}</span><span style={{ color: C.text }}>{text}</span>
    </div>
  );
  const Toggle = (k, text) => (
    <label style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', fontSize: 13, color: C.text, cursor: 'pointer', gap: 12 }}>
      {text}
      <button role="switch" aria-checked={!!settings[k]} onClick={() => saveSetting(k, !settings[k])}
        style={{ width: 34, height: 20, borderRadius: 9999, background: settings[k] ? C.accent : C.hair, position: 'relative', transition: 'background 160ms', flexShrink: 0 }}>
        <span style={{ position: 'absolute', top: 2, left: settings[k] ? 16 : 2, width: 16, height: 16, borderRadius: 9999, background: '#fff', transition: 'left 160ms', boxShadow: '0 1px 2px rgba(0,0,0,.2)' }} />
      </button>
    </label>
  );
  const Slider = (k, text, min, max, step, fmt) => (
    <label style={{ display: 'block', fontSize: 13, color: C.text }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 4 }}><span>{text}</span><span style={{ color: C.muted, fontVariantNumeric: 'tabular-nums' }}>{fmt(settings[k])}</span></div>
      <input type="range" min={min} max={max} step={step} value={settings[k]} onChange={e => saveSetting(k, Number(e.target.value))}
        onPointerDown={e => e.stopPropagation()} style={{ width: '100%', accentColor: C.accent }} aria-label={text} />
    </label>
  );
  const Section = (title, children, extra) => (
    <section style={{ padding: '14px 0', borderTop: `1px solid ${C.hair}` }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 10 }}><span style={label}>{title}</span>{extra}</div>
      <div style={{ display: 'grid', gap: 10 }}>{children}</div>
    </section>
  );

  const ServoBlock = (withAuto) => <>
    {!serialSupported ? (
      <div style={{ fontSize: 13, color: C.muted }}>This browser can't talk to USB serial. Open Mana in desktop Chrome or Edge to drive the servos. The on-screen cell still mirrors what they would show.</div>
    ) : (
      <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
        {serialOn ? <button style={btn(false)} onClick={disconnectSerial}>Disconnect</button> : <button style={btn(true)} onClick={connectSerial}>Connect servo board</button>}
        <span style={{ fontSize: 12, color: serialErr ? C.bad : C.muted }}>{serialErr || (serialOn ? (boardMode === 'esp32' ? 'ESP32 · braille_serial firmware' : boardMode === 'arduino' ? 'Arduino · Braille Vision sketch' : 'Identifying board…') : 'ESP32 or Arduino over USB')}</span>
      </div>
    )}
    {serialOn && boardMode === 'esp32' && <div style={{ fontSize: 12, color: C.muted, lineHeight: 1.5 }}>Your firmware shows letters a–z, one at a time, holding each for 3 s and waiting for “Back at rest.” before the next. Capital and number signs and punctuation are shown on screen only.</div>}
    {withAuto && Toggle('autoSend', 'Send automatically when an object holds steady')}
    {withAuto && Toggle('speak', 'Also speak the word aloud')}
    {Slider('dwellMs', boardMode === 'esp32' && serialOn ? 'Hold each cell (on-screen only)' : 'Hold each cell', 300, 4000, 100, v => (v / 1000).toFixed(1) + ' s')}
    {serialOn && <div style={{ fontFamily: 'ui-monospace, SFMono-Regular, monospace', fontSize: 11, color: C.muted, lineHeight: 1.5 }}><div style={{ fontFamily: 'inherit', color: C.faint }}>Board says:</div>{serialLog.length ? serialLog.map((l, i) => <div key={i}>{l}</div>) : <div>nothing yet</div>}</div>}
  </>;

  const capitals = settings.capitals !== false;
  const tr = textToBraille(text, capitals);
  const uni = toUnicode(tr.cells);
  const textPlaying = play.source === 'text' && (play.idx >= 0 || play.idx === -2);
  const TextTab = () => {
    const words = []; let cur = [];
    tr.cells.forEach((c, i) => {
      if (c.br) { if (cur.length) words.push(cur); words.push('br'); cur = []; }
      else if (c.mask === 0) { if (cur.length) words.push(cur); cur = []; }
      else cur.push([c, i]);
    });
    if (cur.length) words.push(cur);
    const pc = textPlaying && play.idx >= 0 ? play.cells[play.idx] : null;
    const letters = tr.cells.filter(c => c.mask && !c.sign).length;
    return (
      <div style={{ flex: 1, minHeight: 0, display: 'flex', flexDirection: narrow ? 'column' : 'row', gap: 20 }}>
        <div style={{ flex: 1, minWidth: 0, minHeight: narrow ? 0 : 0, display: 'flex', flexDirection: 'column', gap: 14, overflowY: narrow ? 'visible' : 'auto' }}>
          <div style={{ position: 'relative' }}>
            <textarea value={text} onChange={e => editText(e.target.value)} placeholder="Type a sentence, e.g. The cat sat on the mat." aria-label="Text to translate into braille"
              rows={narrow ? 3 : 4} style={{ width: '100%', resize: 'vertical', borderRadius: 14, padding: '14px 16px', fontSize: 18, lineHeight: 1.5, background: 'transparent', color: C.text,
                border: 'none', boxShadow: `inset 0 0 0 1px ${C.hair}`, outline: 'none', fontFamily: 'inherit' }}
              onFocus={e => e.target.style.boxShadow = `inset 0 0 0 1.5px ${C.accent}`} onBlur={e => e.target.style.boxShadow = `inset 0 0 0 1px ${C.hair}`} />
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap', fontSize: 12, color: C.muted }}>
            <span style={{ fontVariantNumeric: 'tabular-nums' }}>{tr.cells.filter(c => !c.br).length} cells · {letters} letters</span>
            <span>Grade 1 UEB (uncontracted)</span>
            {tr.unsupported.length > 0 && <span style={{ color: C.bad }}>Skipped: {tr.unsupported.map(c => c === ' ' ? '␣' : c).join(' ')}</span>}
            <span style={{ marginLeft: 'auto' }}>{Toggle('capitals', 'Capital signs')}</span>
          </div>

          <section aria-label="Braille output" style={{ flex: 1, minHeight: narrow ? 160 : 0, borderRadius: 14, background: C.stage, boxShadow: `inset 0 0 0 1px ${C.hair}`, padding: 18 }}>
            {words.length === 0 ? (
              <div style={{ height: '100%', minHeight: 140, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', gap: 12, textAlign: 'center' }}>
                <BrailleCell mask={toMask('1236')} size={12} ink={C.ink} hair={C.hair} accent={C.accent} />
                <div style={{ fontSize: 13, color: C.muted, maxWidth: 320 }}>Braille appears here as you type. Each cell shows its print letter underneath.</div>
              </div>
            ) : (
              <div style={{ display: 'flex', flexWrap: 'wrap', columnGap: 22, rowGap: 16, alignItems: 'flex-start' }}>
                {words.map((w, wi) => w === 'br' ? <div key={wi} style={{ flexBasis: '100%', height: 0 }} /> : (
                  <div key={wi} style={{ display: 'flex', gap: 6 }}>
                    {w.map(([c, i]) => {
                      const on = textPlaying && play.idx === i;
                      return (
                        <button key={i} title={c.sign ? c.sign + ' sign' : 'dots ' + dotList(c.mask).join('')}
                          onClick={() => playCells(c.ch || c.sign, [c], 'text', false)}
                          style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 4, cursor: 'pointer', background: 'transparent' }}>
                          <BrailleCell mask={c.mask} size={7} active={on} ink={C.ink} hair={C.hair} accent={C.accent} />
                          <span style={{ fontSize: 12, height: 14, color: on ? C.accent : c.sign ? C.faint : C.muted, fontWeight: on ? 600 : 400 }}>{signLabel(c)}</span>
                        </button>
                      );
                    })}
                  </div>
                ))}
              </div>
            )}
          </section>

          {uni && (
            <div style={{ display: 'flex', alignItems: 'flex-start', gap: 12 }}>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ ...label, marginBottom: 6 }}>Braille text (Unicode)</div>
                <div style={{ fontSize: 26, lineHeight: 1.35, letterSpacing: '0.04em', whiteSpace: 'pre-wrap', overflowWrap: 'anywhere', userSelect: 'text', color: C.text }} lang="en-Brai">{uni}</div>
              </div>
              <button style={btn(false)} onClick={() => copy(uni, 'uni')}>{copied === 'uni' ? 'Copied' : copied === 'failed' ? 'Copy failed' : 'Copy braille'}</button>
            </div>
          )}
        </div>

        <aside style={{ width: narrow ? '100%' : 340, flexShrink: 0, overflowY: narrow ? 'visible' : 'auto', paddingRight: 2 }}>
          <section style={{ paddingBottom: 16 }}>
            <span style={label}>Now reading</span>
            <div style={{ display: 'flex', alignItems: 'center', gap: 20, marginTop: 10 }}>
              <BrailleCell mask={pc ? pc.mask : 0} size={22} active={textPlaying} ink={C.ink} hair={C.hair} accent={C.accent} />
              <div style={{ minWidth: 0 }}>
                <div style={{ fontSize: 28, fontWeight: 600, letterSpacing: '-0.02em', lineHeight: 1.1 }}>{pc ? (pc.sign ? signLabel(pc) || '·' : pc.ch === ' ' ? '␣' : pc.ch) : <span style={{ color: C.faint }}>—</span>}</div>
                <div style={{ fontSize: 12, color: C.muted, marginTop: 6 }}>{pc ? `Cell ${play.idx + 1} of ${play.cells.length} · ${pc.sign ? pc.sign + ' sign' : pc.mask ? 'dots ' + dotList(pc.mask).join('') : 'space'}` : tr.cells.length ? 'Ready to play' : 'Type something first'}</div>
              </div>
            </div>
            <div style={{ display: 'flex', gap: 8, marginTop: 14, flexWrap: 'wrap' }}>
              {textPlaying ? <button style={btn(true)} onClick={stopPlayback}>Stop</button>
                : <button style={btn(true)} disabled={!tr.cells.length} onClick={() => playCells(text.trim(), tr.cells.map(c => c.br ? { ...c, ch: ' ' } : c), 'text', false)}>{serialOn ? 'Send to servos' : 'Play cells'}</button>}
              <button style={btn(false)} disabled={!text.trim()} onClick={() => { if (window.speechSynthesis) { window.speechSynthesis.cancel(); window.speechSynthesis.speak(new SpeechSynthesisUtterance(text)); } }}>Speak</button>
            </div>
            {textPlaying && <div style={{ height: 2, borderRadius: 2, background: C.hair, marginTop: 14, overflow: 'hidden' }}><div style={{ height: '100%', width: `${(Math.max(0, play.idx + 1) / play.cells.length) * 100}%`, background: C.accent, transition: 'width 200ms' }} /></div>}
          </section>
          {Section('Servo cell', ServoBlock(false), <button onClick={() => setShowFw(true)} style={{ fontSize: 12, color: C.accent, fontWeight: 500 }}>Firmware</button>)}
          {Section('Key', <div style={{ display: 'grid', gridTemplateColumns: 'auto 1fr', columnGap: 10, rowGap: 6, fontSize: 12, color: C.muted, alignItems: 'center' }}>
            <BrailleCell mask={toMask('6')} size={4} ink={C.ink} hair={C.hair} accent={C.accent} /><span>⇧ capital sign (two for an all-caps word)</span>
            <BrailleCell mask={toMask('3456')} size={4} ink={C.ink} hair={C.hair} accent={C.accent} /><span># number sign, then a–j stand for 1–0</span>
            <BrailleCell mask={toMask('56')} size={4} ink={C.ink} hair={C.hair} accent={C.accent} /><span>· letter sign after a number</span>
          </div>)}
        </aside>
      </div>
    );
  };

  const others = result ? result.dets.slice(0, 5) : [];

  return (
    <div ref={rootRef} className="w-full" style={{ height: narrow ? 'auto' : 'var(--mana-app-height, 82vh)', color: C.text, display: 'flex', flexDirection: 'column', position: 'relative', fontFamily: 'inherit' }}>
      <canvas ref={capRef} style={{ display: 'none' }} />
      <header style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', flexWrap: 'wrap', gap: 10, padding: '4px 2px 14px' }}>
        <div style={{ display: 'flex', alignItems: 'baseline', gap: 14, flexWrap: 'wrap' }}>
          <h1 style={{ fontSize: 20, fontWeight: 600, letterSpacing: '-0.01em' }}>Braille Vision</h1>
          <div role="tablist" aria-label="Mode" style={{ display: 'inline-flex', padding: 3, borderRadius: 9999, boxShadow: `inset 0 0 0 1px ${C.hair}`, alignSelf: 'center' }}
            onKeyDown={e => { if (e.key === 'ArrowRight' || e.key === 'ArrowLeft') { e.preventDefault(); const o = ['camera', 'text', 'insights'], i = o.indexOf(tab); switchTab(o[(i + (e.key === 'ArrowRight' ? 1 : 2)) % 3]); } }}>
            {[['camera', 'Camera'], ['text', 'Text to braille'], ['insights', 'Insights']].map(([k, t]) => (
              <button key={k} role="tab" aria-selected={tab === k} tabIndex={tab === k ? 0 : -1} onClick={() => switchTab(k)}
                style={{ height: 28, padding: '0 14px', borderRadius: 9999, fontSize: 13, fontWeight: 500, cursor: 'pointer', whiteSpace: 'nowrap',
                  background: tab === k ? C.ink : 'transparent', color: tab === k ? (L ? '#fff' : '#18181b') : C.muted, transition: 'background 160ms, color 160ms' }}>{t}</button>
            ))}
          </div>
        </div>
        <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap' }}>
          {tab !== 'text' && Status('Model', detectorOk, detectorLabel)}
          {Status('Servo', serialOn, serialOn ? 'Connected' : 'Not connected')}
          {tab !== 'text' && Status('Speed', !!result, result ? result.ms + ' ms' : '–')}
        </div>
      </header>

      <div style={{ display: tab === 'camera' ? 'contents' : 'none' }}>
      {(() => {
        const standIn = health && health.source === 'fallback';
        if (!standIn && !upl) return null;
        const uploading = upl && !upl.finished && !upl.error;
        let title, sub;
        if (upl && upl.error) { title = 'Upload could not start'; sub = upl.error; }
        else if (uploading) { title = `Uploading ${upl.done} of ${upl.total} files`; sub = (upl.bytes / 1e6).toFixed(1) + ' MB · keep this page open'; }
        else if (upl && upl.finished && upl.failed.length) { title = `${upl.failed.length} file${upl.failed.length > 1 ? 's' : ''} failed to upload`; sub = upl.failed.slice(0, 2).join(' · '); }
        else if (repoInfo && standIn) { title = `Repository uploaded: ${repoInfo.root}`; sub = `${repoInfo.files} files · ${(repoInfo.bytes / 1e6).toFixed(1)} MB. Ask Mana to host it.`; }
        else { title = 'Upload your detection repository'; sub = 'Choose your project folder (or a .zip). Your code is hosted as-is. The stand-in model only lets you test the camera and servos meanwhile.'; }
        return (
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap', padding: '10px 12px 10px 16px', borderRadius: 12, boxShadow: `inset 0 0 0 1px ${C.hair}`, marginBottom: 14, position: 'relative', overflow: 'hidden' }}>
            <div style={{ flex: 1, minWidth: 220 }}>
              <div style={{ fontSize: 13, fontWeight: 500, color: upl && (upl.error || (upl.failed && upl.failed.length)) ? C.bad : C.text }}>{title}</div>
              <div style={{ fontSize: 12, color: C.muted, marginTop: 2 }}>{sub}</div>
            </div>
            <input ref={setFolderInput} type="file" multiple hidden onChange={e => { uploadRepo(e.target.files, 'folder'); e.target.value = ''; }} />
            <input ref={zipRef} type="file" accept=".zip,application/zip" hidden onChange={e => { uploadRepo(e.target.files, 'zip'); e.target.value = ''; }} />
            {repoInfo && standIn && !uploading && repoInfo.status === 'uploaded' && <button style={btn(true)} onClick={requestHosting}>Host it</button>}
            <button style={btn(!(repoInfo && standIn))} disabled={uploading} onClick={() => folderRef.current && folderRef.current.click()}>{repoInfo ? 'Upload again' : 'Upload folder'}</button>
            <button style={btn(false)} disabled={uploading} onClick={() => zipRef.current && zipRef.current.click()}>Upload .zip</button>
            {uploading && <div style={{ position: 'absolute', left: 0, bottom: 0, height: 2, width: `${(upl.done / Math.max(1, upl.total)) * 100}%`, background: C.accent, transition: 'width 200ms' }} />}
          </div>
        );
      })()}

      <div style={{ flex: 1, minHeight: 0, display: 'flex', flexDirection: narrow ? 'column' : 'row', gap: 20 }}>
        {/* camera stage */}
        <div style={{ flex: 1, minWidth: 0, minHeight: narrow ? 320 : 0, display: 'flex', flexDirection: 'column', gap: 10 }}>
          <div style={{ flex: 1, minHeight: narrow ? 300 : 0, position: 'relative', borderRadius: 14, overflow: 'hidden', background: C.stage, boxShadow: `inset 0 0 0 1px ${C.hair}` }}>
            <video ref={videoRef} playsInline muted aria-label="Camera view" style={{ position: 'absolute', inset: 0, width: '100%', height: '100%', objectFit: 'contain', display: running ? 'block' : 'none' }} />
            {running && result && result.w ? (
              <svg viewBox={`0 0 ${result.w} ${result.h}`} preserveAspectRatio="xMidYMid meet" style={{ position: 'absolute', inset: 0, width: '100%', height: '100%', pointerEvents: 'none' }} aria-hidden="true">
                {result.dets.map((d, i) => {
                  const [x1, y1, x2, y2] = d.box; const top = i === 0;
                  const lockedNow = result.state && result.state.phase === 'locked';
                  const col = lockedNow && top ? C.ok : top ? C.accent : '#ffffff';
                  const tag = `${d.label} ${Math.round(d.confidence * 100)}%${result.state ? (lockedNow ? ' · confirmed' : ' · confirming') : ''}`;
                  return <g key={i}>
                    <rect x={x1} y={y1} width={x2 - x1} height={y2 - y1} fill="none" stroke={col} strokeWidth={top ? 3 : 1.5} strokeOpacity={top ? 1 : 0.8} rx="4" />
                    <rect x={x1} y={Math.max(0, y1 - 22)} width={tag.length * 7.4 + 12} height="20" rx="4" fill={top ? col : 'rgba(24,24,27,0.72)'} />
                    <text x={x1 + 6} y={Math.max(0, y1 - 22) + 14} fill="#fff" fontSize="13" fontFamily="ui-sans-serif, system-ui">{tag}</text>
                  </g>;
                })}
              </svg>
            ) : null}
            {running && result && result.state && (
              <div style={{ position: 'absolute', top: 12, right: 12, padding: '6px 12px', borderRadius: 9999, fontSize: 13, fontWeight: 500, fontVariantNumeric: 'tabular-nums',
                background: 'rgba(24,24,27,0.72)', color: '#fff', backdropFilter: 'blur(6px)' }}>
                {result.state.phase === 'locked' ? (result.state.remaining == null ? `Spelling ${result.state.label} · scans again after the last letter` : `Locked · ${result.state.remaining.toFixed(1)} s`) : result.state.phase === 'confirming' ? `Confirming ${result.state.label} · ${result.state.remaining.toFixed(1)} s` : 'Scanning…'}
              </div>
            )}
            {!running && (
              <div style={{ position: 'absolute', inset: 0, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', gap: 14, padding: 24, textAlign: 'center' }}>
                <BrailleCell mask={toMask('1236')} size={14} ink={C.ink} hair={C.hair} accent={C.accent} />
                <div style={{ fontSize: 15, fontWeight: 500 }}>{health && health.classes ? 'Show chips, soda or a pen' : 'Point a camera at an object'}</div>
                <div style={{ fontSize: 13, color: C.muted, maxWidth: 380 }}>{health && health.classes ? `Frames go to your braille_assist model. A label that holds for ${health.confirmSeconds || 3} seconds is confirmed and spelled out on the braille cell${health.lockMode === 'until-playback' ? '. Scanning starts again after the last letter.' : `, then locked for ${health.lockSeconds || 10} seconds.`}` : 'Each frame goes to your detection model. When the same object holds steady, its name is spelled out on the servo braille cell.'}</div>
                <button onClick={start} style={btn(true)} disabled={!url}>{url ? 'Start camera' : 'Waiting for detector…'}</button>
              </div>
            )}
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
            {running ? <button onClick={stop} style={btn(false)}>Stop</button> : <button onClick={start} style={btn(true)} disabled={!url}>Start camera</button>}
            {devices.length > 1 && (
              <select value={deviceId} onChange={e => switchCamera(e.target.value)} aria-label="Camera"
                style={{ height: 36, borderRadius: 9999, padding: '0 12px', fontSize: 13, background: 'transparent', color: C.text, border: `1px solid ${C.hair}`, maxWidth: 260 }}>
                {devices.map((d, i) => <option key={d.deviceId} value={d.deviceId}>{d.label || 'Camera ' + (i + 1)}</option>)}
              </select>
            )}
            <span style={{ fontSize: 12, color: error || ingressError ? C.bad : C.muted, marginLeft: 'auto' }}>
              {ingressError ? 'Detector unavailable: ' + ingressError.message : error ? error : running && result && result.scores ? Object.entries(result.scores).map(([k, v]) => `${k} ${Math.round(v * 100)}%`).join(' · ') : running ? (others.length ? 'Seeing: ' + others.map(d => `${d.label} ${Math.round(d.confidence * 100)}%`).join(' · ') : 'Nothing above threshold') : ''}
            </span>
          </div>
        </div>

        {/* reading panel */}
        <aside style={{ width: narrow ? '100%' : 340, flexShrink: 0, overflowY: narrow ? 'visible' : 'auto', paddingRight: 2 }}>
          <div aria-live="polite" className="sr-only" style={{ position: 'absolute', width: 1, height: 1, overflow: 'hidden', clip: 'rect(0 0 0 0)' }}>{current ? 'Detected ' + current : ''}</div>
          <section style={{ paddingBottom: 16 }}>
            <span style={label}>Now reading</span>
            <div style={{ display: 'flex', alignItems: 'center', gap: 20, marginTop: 10 }}>
              <BrailleCell mask={bigMask} size={22} active={camPlay.idx >= 0} ink={C.ink} hair={C.hair} accent={C.accent} />
              <div style={{ minWidth: 0 }}>
                <div style={{ fontSize: 28, fontWeight: 600, letterSpacing: '-0.02em', lineHeight: 1.1, overflowWrap: 'anywhere' }}>{shownWord || <span style={{ color: C.faint }}>—</span>}</div>
                <div style={{ fontSize: 12, color: C.muted, marginTop: 6 }}>
                  {camPlay.idx >= 0 ? `Cell ${camPlay.idx + 1} of ${camPlay.cells.length} · ${camPlay.cells[camPlay.idx].sign ? 'number sign' : camPlay.cells[camPlay.idx].ch === ' ' ? 'space' : '“' + camPlay.cells[camPlay.idx].ch + '”'}` : shownWord ? 'Servos at rest' : 'No object yet'}
                </div>
              </div>
            </div>
            {wordCells.length > 0 && (
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginTop: 14 }} aria-label={'Braille for ' + shownWord}>
                {wordCells.map((c, i) => (
                  <div key={i} style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 3 }}>
                    <BrailleCell mask={c.mask} size={5} active={camPlay.idx === i} ink={C.ink} hair={C.hair} accent={C.accent} />
                    <span style={{ fontSize: 10, color: camPlay.idx === i ? C.accent : C.faint, height: 12 }}>{c.sign ? '#' : c.ch === ' ' ? '␣' : c.ch}</span>
                  </div>
                ))}
              </div>
            )}
            <div style={{ display: 'flex', gap: 8, marginTop: 14 }}>
              <button style={btn(true)} disabled={!shownWord} onClick={() => sendWord(current || camPlay.word)}>{serialOn ? 'Send to servos' : 'Play again'}</button>
              <button style={btn(false)} disabled={!shownWord} onClick={() => { if (window.speechSynthesis) { window.speechSynthesis.cancel(); window.speechSynthesis.speak(new SpeechSynthesisUtterance(shownWord)); } }}>Speak</button>
            </div>
          </section>

          {Section('Servo cell', ServoBlock(true), <button onClick={() => setShowFw(true)} style={{ fontSize: 12, color: C.accent, fontWeight: 500 }}>Firmware</button>)}

          {health && health.classes ? Section('Model', <>
            <div style={{ fontSize: 13, color: C.text }}>braille_assist · {health.checkpoint}{health.commit ? ' · ' + health.commit : ''}</div>
            <div style={{ fontSize: 12, color: C.muted, lineHeight: 1.5 }}>Recognizes {health.classes.join(', ')}. Thresholds come from your checkpoint. A label must persist for more than {health.confirmSeconds || 3} s to confirm{health.lockMode === 'until-playback' ? ', then holds until every letter has been shown on the braille cell. Scanning starts again right after the last letter.' : `, then locks for ${health.lockSeconds || 10} s.`}</div>
            <a href="https://github.com/julieyiu777/braille_assist" target="_blank" rel="noreferrer" style={{ fontSize: 12, color: C.accent, fontWeight: 500 }}>github.com/julieyiu777/braille_assist ↗</a>
          </>) : Section('Detection', <>
            {Slider('threshold', 'Minimum confidence', 0.2, 0.95, 0.05, v => Math.round(v * 100) + '%')}
            {Slider('stableFrames', 'Frames before it counts', 1, 8, 1, v => v + (v === 1 ? ' frame' : ' frames'))}
          </>)}

          {Section('Insights', (() => {
            if (!ins) return <div style={{ fontSize: 12, color: C.muted }}>Waiting for the first insights from Tiger Data.</div>;
            const sigs = sigList(ins).filter(s => s.level !== 'ok').slice(0, 3);
            const stale = Date.now() - (ins.computedAt || 0) > 3 * 60000, h = stale ? 'warn' : ins.health;
            return <>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 13, fontWeight: 500 }}>
                <span style={{ width: 7, height: 7, borderRadius: 9999, background: levelColor(C, h) }} />{stale ? 'Insights are stale' : LEVEL_TEXT[h]}
                <span style={{ marginLeft: 'auto', fontSize: 12, color: C.faint, fontWeight: 400 }}>{agoText(ins.computedAt)}</span>
              </div>
              {sigs.map(s => <div key={s.id} style={{ fontSize: 12, color: C.muted, lineHeight: 1.45, display: 'flex', gap: 8 }}>
                <span style={{ width: 5, height: 5, borderRadius: 9999, background: levelColor(C, s.level), marginTop: 6, flexShrink: 0 }} />
                <span><span style={{ color: C.text }}>{s.title}.</span> {s.detail}</span></div>)}
              <button style={{ ...btn(false), alignSelf: 'start', justifySelf: 'start' }} onClick={() => switchTab('insights')}>Open insights</button>
            </>;
          })(), <span style={{ fontSize: 12, color: C.faint }}>Tiger Data</span>)}

          {Section('Test without the camera', <>
            <form onSubmit={e => { e.preventDefault(); if (manual.trim()) sendWord(manual.trim()); }} style={{ display: 'flex', gap: 8 }}>
              <input value={manual} onChange={e => setManual(e.target.value)} placeholder="Type a word, e.g. cup" aria-label="Word to send"
                style={{ flex: 1, minWidth: 0, height: 36, borderRadius: 9999, padding: '0 14px', fontSize: 13, background: 'transparent', color: C.text, border: `1px solid ${C.hair}`, outline: 'none' }} />
              <button type="submit" style={btn(false)} disabled={!manual.trim()}>Send</button>
            </form>
            {history.length > 0 && <div style={{ fontSize: 12, color: C.muted }}>Recent: {history.map(h => h.word).join(' · ')}</div>}
          </>)}
        </aside>
      </div>
      </div>
      {tab === 'text' && TextTab()}
      {tab === 'insights' && <InsightsPanel ins={ins} C={C} narrow={narrow} />}

      {showFw && (
        <div style={{ position: 'absolute', inset: 0, background: L ? 'rgba(244,244,245,0.7)' : 'rgba(9,9,11,0.7)', backdropFilter: 'blur(6px)', display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 16, zIndex: 5 }}
          onClick={() => setShowFw(false)} onKeyDown={e => e.key === 'Escape' && setShowFw(false)}>
          <div onClick={e => e.stopPropagation()} role="dialog" aria-label="Firmware"
            style={{ width: 'min(760px, 100%)', maxHeight: '100%', display: 'flex', flexDirection: 'column', background: C.surface, borderRadius: 14, boxShadow: `0 0 0 1px ${C.hair}, 0 20px 50px rgba(0,0,0,0.18)` }}>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '12px 16px', borderBottom: `1px solid ${C.hair}` }}>
              <div>
                <div style={{ display: 'inline-flex', padding: 3, borderRadius: 9999, boxShadow: `inset 0 0 0 1px ${C.hair}`, marginBottom: 6 }}>
                  {[['esp32', 'ESP32 · your repo'], ['arduino', 'Arduino Uno · alternative']].map(([k, t]) => (
                    <button key={k} onClick={() => setFwView(k)} style={{ height: 26, padding: '0 12px', borderRadius: 9999, fontSize: 12, fontWeight: 500, background: fwView === k ? C.ink : 'transparent', color: fwView === k ? (L ? '#fff' : '#18181b') : C.muted }}>{t}</button>
                  ))}
                </div>
                <div style={{ fontSize: 12, color: C.muted }}>{fwView === 'esp32' ? 'firmware/braille_serial/braille_serial.ino · PCA9685 at 0x40, SDA 6 / SCL 7, channels 0 1 2 · 6 5 4' : 'braille_servo.ino · 6 servos on pins 3, 5, 6, 9, 10, 11 · external 5 V supply, shared ground'}</div>
              </div>
              <div style={{ display: 'flex', gap: 8 }}>
                <button style={btn(true)} onClick={() => copy(fwView === 'esp32' ? firmwareEsp : firmware, 'fw')}>{copied === 'fw' ? 'Copied' : copied === 'failed' ? 'Copy failed' : 'Copy'}</button>
                <button style={btn(false)} onClick={() => setShowFw(false)} autoFocus>Close</button>
              </div>
            </div>
            <pre style={{ margin: 0, padding: 16, overflow: 'auto', fontSize: 12, lineHeight: 1.55, fontFamily: 'ui-monospace, SFMono-Regular, monospace', color: C.text, userSelect: 'text' }}>{(fwView === 'esp32' ? firmwareEsp : firmware) || 'Loading…'}</pre>
          </div>
        </div>
      )}
    </div>
  );
}
