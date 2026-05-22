import { memo, useCallback, useEffect, useMemo, useRef, useState, useTransition } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import {
  ANALYST_SUMMARY_LABEL,
  DEFAULT_EXPANDED_REPORT_SECTIONS,
  DEFAULT_ANALYSTS,
  REPORT_SECTIONS,
  REPORT_GROUPS,
  isReportSectionExpanded,
} from './analysisConfig';
import { formatFinalDecisionReport } from './reportFormatting';
import {
  getRetrievedInfoTitle,
  getTranscriptMessagePresentation,
  groupTranscriptLogs,
} from './transcriptDisplay';
import EvidenceGraphPanel from './EvidenceGraphPanel';
import TraderReasoningPanel from './TraderReasoningPanel';
import ThemeCandidatesPanel from './ThemeCandidatesPanel';

const API_BASE = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000';
const WS_BASE = API_BASE.replace(/^http/, 'ws');

const HORIZONS = [
  { value: '1-2 weeks', label: 'Short term', detail: '1-2 weeks' },
  { value: '1-2 months', label: 'Swing', detail: '1-2 months' },
  { value: '2-3 months', label: 'Long term', detail: '2-3 months' },
];

const RESEARCH_DEPTHS = [
  { value: 1, label: 'Shallow', detail: 'Fast scan' },
  { value: 3, label: 'Medium', detail: 'Balanced' },
  { value: 5, label: 'Deep', detail: 'Thorough' },
];

const DISCOVERY_TRACKS = [
  { value: 'enricher', label: 'Enricher', detail: 'Stage 1 & 2' },
  { value: 'anomaly_scan', label: 'Anomaly Scan', detail: 'Short-term' },
  { value: 'dual_track', label: 'Dual-Track', detail: 'Both tracks' },
];

const CATALYST_MODES = [
  { value: 'daily_calendar', label: 'Daily Calendar', detail: 'Default' },
  { value: 'per_ticker_calendar', label: 'Per Ticker', detail: 'Slower' },
];

const SCAN_MODES = [
  { value: 'seed_only',     label: 'Seed Only',     detail: 'Instant, no network' },
  { value: 'with_evidence', label: 'With Evidence', detail: 'Live headlines' },
];

const LEGACY_HORIZON_VALUES = {
  short_term: '1-2 weeks',
  swing: '1-2 months',
  long_term: '2-3 months',
};

const normalizeHorizonValue = (value) => {
  const normalized = LEGACY_HORIZON_VALUES[value] || value;
  return HORIZONS.some((item) => item.value === normalized) ? normalized : HORIZONS[0].value;
};

const getHorizonMeta = (value) => (
  HORIZONS.find((item) => item.value === normalizeHorizonValue(value)) || HORIZONS[0]
);

const MODES = [
  { 
    id: 'analysis', 
    label: 'Analysis', 
    description: 'Run the agent pipeline',
    icon: <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><circle cx="12" cy="12" r="10"></circle><polyline points="12 6 12 12 16 14"></polyline></svg>
  },
  { 
    id: 'reports', 
    label: 'Reports', 
    description: 'Read generated findings',
    icon: <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"></path><polyline points="14 2 14 8 20 8"></polyline><line x1="16" y1="13" x2="8" y2="13"></line><line x1="16" y1="17" x2="8" y2="17"></line><polyline points="10 9 9 9 8 9"></polyline></svg>
  },
  { 
    id: 'execution', 
    label: 'Execution', 
    description: 'Paper-trade controls',
    icon: <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"></polygon></svg>
  },
];

const SHALLOW_MODELS = [
  { value: 'openai|gpt-4o-mini', label: 'GPT-4o Mini', detail: 'OpenAI default' },
  { value: 'qwen3-cn|qwen3.6-flash', label: 'Qwen3.6-Flash', detail: 'Fast, cost-effective' },
  { value: 'anthropic|claude-sonnet-4-6', label: 'Claude 4.6 Sonnet', detail: 'Balanced performance' },
  { value: 'anthropic|claude-haiku-4-5-20251001', label: 'Claude 4.5 Haiku', detail: 'Fast anthropic' },
];

const DEEP_MODELS = [
  { value: 'openai|gpt-4o-mini', label: 'GPT-4o Mini', detail: 'OpenAI default' },
  { value: 'glm|glm-4.7-flash', label: 'GLM-4.7-Flash', detail: 'Fast, cost-effective' },
  { value: 'qwen3-cn|qwen3.5-plus', label: 'Qwen3.5-Plus', detail: 'Strong reasoning' },
  { value: 'qwen3-cn|qwen3.6-plus', label: 'Qwen3.6-Plus', detail: 'Strong reasoning v3.6' },
  { value: 'deepseek|deepseek-reasoner', label: 'DeepSeek Reasoner', detail: 'Deep thinking' },
  { value: 'anthropic|claude-opus-4-6', label: 'Claude 4.6 Opus', detail: 'Maximum capability' },
  { value: 'anthropic|claude-sonnet-4-6', label: 'Claude 4.6 Sonnet', detail: 'Balanced performance' },
];

const BACKEND_URLS = {
  'openai': 'http://192.168.123.81:8045/v1',
  'qwen3-cn': 'https://dashscope.aliyuncs.com/compatible-mode/v1',
  'deepseek': 'https://api.deepseek.com/v1',
  'glm': 'https://open.bigmodel.cn/api/paas/v4',
  'anthropic': 'http://ai.tachira.cn/api',
  'openrouter': 'https://openrouter.ai/api/v1',
};

const getPredefinedModelsForProvider = (providerId) => {
  const list = [];
  const seen = new Set();

  SHALLOW_MODELS.forEach(m => {
    const [pid, name] = m.value.split('|');
    if (pid === providerId) {
      const isDeep = DEEP_MODELS.some(dm => dm.value === m.value);
      const scope = isDeep ? 'both' : 'shallow';
      const key = `${pid}|${name}`;
      if (!seen.has(key)) {
        seen.add(key);
        list.push({ name, label: m.label, detail: m.detail, scope });
      }
    }
  });

  DEEP_MODELS.forEach(m => {
    const [pid, name] = m.value.split('|');
    if (pid === providerId) {
      const isShallow = SHALLOW_MODELS.some(sm => sm.value === m.value);
      const scope = isShallow ? 'both' : 'deep';
      const key = `${pid}|${name}`;
      if (!seen.has(key)) {
        seen.add(key);
        list.push({ name, label: m.label, detail: m.detail, scope });
      }
    }
  });

  return list;
};

const makeLog = (type, content) => ({
  id: `${Date.now()}-${Math.random()}`,
  type,
  content,
});

const PREVIEW_LINES = 3;
const PREVIEW_CHARS = 300;

const CollapsibleContent = ({ content }) => {
  const [expanded, setExpanded] = useState(false);
  const lines = content.split('\n');
  const isLong = lines.length > PREVIEW_LINES || content.length > PREVIEW_CHARS;

  if (!isLong) {
    return (
      <div className="markdown-content">
        <ReactMarkdown remarkPlugins={[remarkGfm]}>{content}</ReactMarkdown>
      </div>
    );
  }

  return (
    <div className="msg-collapsible">
      <div className={`markdown-content ${expanded ? '' : 'collapsed'}`}>
        <ReactMarkdown remarkPlugins={[remarkGfm]}>{content}</ReactMarkdown>
      </div>
      <button className="expand-btn" onClick={() => setExpanded(e => !e)}>
        {expanded ? '▲ Show less' : `▼ Show more`}
      </button>
    </div>
  );
};

const ToolGroupMessage = ({ tools }) => {
  const [expanded, setExpanded] = useState(false);
  // Extract unique tool names (content is "toolName: {args}")
  const names = [...new Set(tools.map(t => t.content.split(':')[0].trim()))];
  const preview = names.slice(0, 3).join(' · ') + (names.length > 3 ? ` +${names.length - 3}` : '');

  return (
    <div className="tool-group-row">
      <button className="tool-group-chip" onClick={() => setExpanded(e => !e)}>
        <svg viewBox="0 0 24 24" width="12" height="12" fill="currentColor" style={{flexShrink:0}}>
          <path d="M19.14 12.94c.04-.3.06-.61.06-.94s-.02-.64-.07-.94l2.03-1.58a.49.49 0 00.12-.61l-1.92-3.32a.49.49 0 00-.59-.22l-2.39.96c-.5-.38-1.03-.7-1.62-.94l-.36-2.54A.484.484 0 0014.4 3h-3.84c-.24 0-.43.17-.47.41l-.36 2.54c-.59.24-1.13.56-1.62.94l-2.39-.96c-.22-.08-.47 0-.59.22L2.74 9.47c-.12.21-.08.47.12.61l2.03 1.58c-.05.3-.07.62-.07.94s.02.64.07.94l-2.03 1.58a.49.49 0 00-.12.61l1.92 3.32c.12.22.37.29.59.22l2.39-.96c.5.38 1.03.7 1.62.94l.36 2.54c.05.24.24.41.48.41h3.84c.24 0 .44-.17.47-.41l.36-2.54c.59-.24 1.13-.56 1.62-.94l2.39.96c.22.08.47 0 .59-.22l1.92-3.32c.12-.22.07-.47-.12-.61l-2.01-1.58zM12 15.6c-1.98 0-3.6-1.62-3.6-3.6s1.62-3.6 3.6-3.6 3.6 1.62 3.6 3.6-1.62 3.6-3.6 3.6z"/>
        </svg>
        <span className="tg-count">{tools.length} tool call{tools.length > 1 ? 's' : ''}</span>
        <span className="tg-names">{preview}</span>
        <span className="tg-chevron">{expanded ? '▲' : '▼'}</span>
      </button>
      {expanded && (
        <div className="tool-group-detail">
          {tools.map(t => (
            <div key={t.id} className="tool-detail-item">
              <code>{t.content}</code>
            </div>
          ))}
        </div>
      )}
    </div>
  );
};

const RetrievedInfoGroupMessage = ({ items }) => {
  const [expanded, setExpanded] = useState(false);
  const previews = items.map((item, index) => getRetrievedInfoTitle(item.content, index));
  const preview = previews.slice(0, 2).join(' / ') + (previews.length > 2 ? ` +${previews.length - 2}` : '');

  return (
    <div className="retrieved-info-row">
      <button className="retrieved-info-chip" onClick={() => setExpanded(e => !e)}>
        <svg viewBox="0 0 24 24" width="13" height="13" fill="currentColor" style={{flexShrink:0}}>
          <path d="M19 3H5c-1.1 0-2 .9-2 2v14c0 1.1.9 2 2 2h14c1.1 0 2-.9 2-2V5c0-1.1-.9-2-2-2zm-7 14H7v-2h5v2zm5-4H7v-2h10v2zm0-4H7V7h10v2z"/>
        </svg>
        <span className="ri-count">{items.length} retrieved info item{items.length > 1 ? 's' : ''}</span>
        <span className="ri-preview">{preview}</span>
        <span className="ri-chevron">{expanded ? '▲' : '▼'}</span>
      </button>
      {expanded && (
        <div className="retrieved-info-detail">
          {items.map((item, index) => (
            <details key={item.id} className="retrieved-info-item">
              <summary>
                <span className="retrieved-info-title">{getRetrievedInfoTitle(item.content, index)}</span>
              </summary>
              <pre><code>{item.content}</code></pre>
            </details>
          ))}
        </div>
      )}
    </div>
  );
};

const formatDateTime = (value) => {
  if (!value) return '';
  return new Intl.DateTimeFormat(undefined, {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  }).format(new Date(value));
};

function renderReportText(value) {
  if (!value) return null;
  if (typeof value === 'string') return value;
  if (value.judge_decision) return value.judge_decision;
  if (value.final_decision) return value.final_decision;
  // Debate state objects - extract the most useful readable text
  if (value.current_response) return value.current_response;
  if (value.history && Array.isArray(value.history)) {
    return value.history.map(h => typeof h === 'string' ? h : JSON.stringify(h, null, 2)).join('\n\n---\n\n');
  }
  return JSON.stringify(value, null, 2);
}

const ReportMarkdown = memo(({ markdown, hiddenDecisionJson }) => (
  <>
    <ReactMarkdown remarkPlugins={[remarkGfm]}>
      {markdown}
    </ReactMarkdown>
    {hiddenDecisionJson && (
      <pre hidden data-final-decision-json>
        {JSON.stringify(hiddenDecisionJson, null, 2)}
      </pre>
    )}
  </>
));

const ReportSection = memo(({ sectionKey, label, data, isExpanded, onToggle, allReports }) => {
  const header = (
    <button
      className="report-section-header"
      onClick={() => onToggle(sectionKey)}
      aria-expanded={isExpanded}
    >
      <span className="report-section-label">{label}</span>
      <span className="report-section-chevron">
        {isExpanded ? (
          <svg viewBox="0 0 24 24" width="16" height="16" fill="currentColor"><path d="M7.41 8.59L12 13.17l4.59-4.58L18 10l-6 6-6-6z"/></svg>
        ) : (
          <svg viewBox="0 0 24 24" width="16" height="16" fill="currentColor"><path d="M8.59 16.59L13.17 12 8.59 7.41 10 6l6 6-6 6z"/></svg>
        )}
      </span>
    </button>
  );

  if (!isExpanded) {
    return (
      <div className="report-section">
        {header}
      </div>
    );
  }

  const reportText = renderReportText(data) || '';
  const finalDecision = sectionKey === 'final_trade_decision' ? formatFinalDecisionReport(reportText) : null;

  return (
    <div className="report-section expanded">
      {header}
      <div className="report-section-body markdown-content">
        {sectionKey === 'evidence_graph' ? (
          <EvidenceGraphPanel data={data} />
        ) : sectionKey === 'agent_reasoning_trace' ? (
          <TraderReasoningPanel data={data} />
        ) : sectionKey === 'trader_investment_plan' ? (
          <>
            <ReportMarkdown
              markdown={finalDecision?.markdown || reportText}
              hiddenDecisionJson={finalDecision?.hiddenDecisionJson}
            />
            {allReports?.agent_reasoning_trace && (
              <div style={{ marginTop: '24px', paddingTop: '24px', borderTop: '1px solid rgba(255,255,255,0.1)' }}>
                <h4 style={{ marginBottom: '16px', color: '#e5e7eb' }}>Reasoning Trace</h4>
                <TraderReasoningPanel data={allReports.agent_reasoning_trace} />
              </div>
            )}
          </>
        ) : (
          <ReportMarkdown
            markdown={finalDecision?.markdown || reportText}
            hiddenDecisionJson={finalDecision?.hiddenDecisionJson}
          />
        )}
      </div>
    </div>
  );
});

const CustomSelect = ({ value, onChange, options, disabled, icon, title }) => {
  const [isOpen, setIsOpen] = useState(false);
  const containerRef = useRef(null);

  useEffect(() => {
    const handleClickOutside = (event) => {
      if (containerRef.current && !containerRef.current.contains(event.target)) {
        setIsOpen(false);
      }
    };
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, []);

  const selectedOption = options.find((opt) => opt.value === value) || options[0];

  return (
    <div className={`gemini-custom-select ${disabled ? 'disabled' : ''}`} ref={containerRef}>
      <button 
        className={`gemini-select-trigger ${isOpen ? 'active' : ''}`}
        onClick={() => !disabled && setIsOpen(!isOpen)}
        type="button"
      >
        {icon && <span className="select-icon">{icon}</span>}
        <span className="select-label">{title || selectedOption.label}</span>
        <svg width="10" height="6" viewBox="0 0 10 6" fill="none" xmlns="http://www.w3.org/2000/svg" className="chevron">
          <path d="M1 1L5 5L9 1" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/>
        </svg>
      </button>
      {isOpen && !disabled && (
        <div className="gemini-dropdown-menu">
          {options.map((opt) => (
            <button
              key={opt.value}
              className={`gemini-dropdown-item ${value === opt.value ? 'selected' : ''}`}
              onClick={() => {
                onChange(opt.value);
                setIsOpen(false);
              }}
              type="button"
            >
              <div className="item-content">
                <span className="item-label">{opt.label}</span>
                {opt.detail && <span className="item-detail">{opt.detail}</span>}
              </div>
              {value === opt.value && (
                <svg className="check-icon" viewBox="0 0 24 24" width="16" height="16" fill="currentColor">
                  <path d="M9 16.17L4.83 12l-1.42 1.41L9 19 21 7l-1.41-1.41L9 16.17z"/>
                </svg>
              )}
            </button>
          ))}
        </div>
      )}
    </div>
  );
};

const CustomDatePicker = ({ value, onChange, disabled }) => {
  const [isOpen, setIsOpen] = useState(false);
  const containerRef = useRef(null);
  const [viewDate, setViewDate] = useState(() => new Date(value + 'T00:00:00'));

  useEffect(() => {
    const handleClickOutside = (event) => {
      if (containerRef.current && !containerRef.current.contains(event.target)) {
        setIsOpen(false);
      }
    };
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, []);

  const dateObj = new Date(value + 'T00:00:00');
  const displayDate = new Intl.DateTimeFormat('en-US', { month: '2-digit', day: '2-digit', year: 'numeric' }).format(dateObj);

  const changeMonth = (offset) => {
    const newView = new Date(viewDate);
    newView.setMonth(newView.getMonth() + offset);
    setViewDate(newView);
  };

  const renderCalendar = () => {
    const year = viewDate.getFullYear();
    const month = viewDate.getMonth();
    const firstDay = new Date(year, month, 1).getDay();
    const daysInMonth = new Date(year, month + 1, 0).getDate();
    
    const days = [];
    for (let i = 0; i < firstDay; i++) days.push(<div key={`empty-${i}`} className="cal-empty" />);
    for (let d = 1; d <= daysInMonth; d++) {
      const dateStr = `${year}-${String(month+1).padStart(2, '0')}-${String(d).padStart(2, '0')}`;
      const isSelected = value === dateStr;
      const isToday = new Date().toISOString().split('T')[0] === dateStr;
      
      days.push(
        <button
          key={d}
          type="button"
          className={`cal-day ${isSelected ? 'selected' : ''} ${isToday && !isSelected ? 'today' : ''}`}
          onClick={() => { onChange(dateStr); setIsOpen(false); }}
        >
          {d}
        </button>
      );
    }
    return (
      <div className="cal-grid">
        {['Su','Mo','Tu','We','Th','Fr','Sa'].map(d => <div key={d} className="cal-header-day">{d}</div>)}
        {days}
      </div>
    );
  };

  return (
    <div className={`gemini-custom-select ${disabled ? 'disabled' : ''}`} ref={containerRef}>
      <button 
        className={`gemini-select-trigger ${isOpen ? 'active' : ''}`}
        onClick={() => {
          if (disabled) return;
          if (!isOpen && value) setViewDate(new Date(value + 'T00:00:00'));
          setIsOpen(!isOpen);
        }}
        type="button"
      >
        <span className="select-icon">
          <svg viewBox="0 0 24 24" width="16" height="16" fill="currentColor" style={{marginRight: 6}}>
            <path d="M19 4h-1V2h-2v2H8V2H6v2H5c-1.11 0-1.99.9-1.99 2L3 20c0 1.1.89 2 2 2h14c1.1 0 2-.9 2-2V6c0-1.1-.9-2-2-2zm0 16H5V10h14v10z"/>
          </svg>
        </span>
        <span className="select-label">{displayDate}</span>
      </button>
      {isOpen && !disabled && (
        <div className="gemini-dropdown-menu calendar-menu">
          <div className="cal-header">
            <button type="button" className="cal-nav" onClick={() => changeMonth(-1)}>
              <svg viewBox="0 0 24 24" width="16" height="16" fill="currentColor"><path d="M15.41 16.59L10.83 12l4.58-4.59L14 6l-6 6 6 6 1.41-1.41z"/></svg>
            </button>
            <strong>{new Intl.DateTimeFormat('en-US', { month: 'long', year: 'numeric' }).format(viewDate)}</strong>
            <button type="button" className="cal-nav" onClick={() => changeMonth(1)}>
              <svg viewBox="0 0 24 24" width="16" height="16" fill="currentColor"><path d="M8.59 16.59L13.17 12 8.59 7.41 10 6l6 6-6 6-1.41-1.41z"/></svg>
            </button>
          </div>
          {renderCalendar()}
        </div>
      )}
    </div>
  );
};

const LogoIcon = () => (
  <svg width="24" height="24" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
    <path d="M12 2L2 7l10 5 10-5-10-5z" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/>
    <path d="M2 17l10 5 10-5" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/>
    <path d="M2 12l10 5 10-5" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/>
  </svg>
);

const TranscriptAvatar = ({ avatar, label }) => (
  <div className={`avatar avatar-${avatar}`} title={label} aria-label={label}>
    {avatar === 'user' ? (
      <svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
        <path d="M12 12.2c2.27 0 4.12-1.84 4.12-4.12S14.27 4 12 4 7.88 5.81 7.88 8.08 9.73 12.2 12 12.2Z" fill="currentColor" opacity="0.92" />
        <path d="M4.95 20c.66-3.55 3.35-5.63 7.05-5.63S18.39 16.45 19.05 20H4.95Z" fill="currentColor" opacity="0.72" />
      </svg>
    ) : avatar === 'tool' ? (
      <svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
        <path d="M14.68 5.3 12.8 7.18l4.02 4.02 1.88-1.88a2.84 2.84 0 0 0-4.02-4.02Z" fill="currentColor" opacity="0.92" />
        <path d="m11.74 8.24-6.8 6.8L4 20l4.96-.94 6.8-6.8-4.02-4.02Z" fill="currentColor" opacity="0.72" />
      </svg>
    ) : (
      <svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
        <path d="M12 3.75 5.25 7.5v6.97L12 20.25l6.75-5.78V7.5L12 3.75Z" fill="currentColor" opacity="0.2" />
        <path d="M8.15 11.08h7.7M8.15 14.18h5.45M12 3.75 5.25 7.5v6.97L12 20.25l6.75-5.78V7.5L12 3.75Z" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" />
      </svg>
    )}
  </div>
);

function App() {
  const [theme, setTheme] = useState(() => localStorage.getItem('theme') || 'dark');
  const [ticker, setTicker] = useState('');
  const [analysisDate, setAnalysisDate] = useState(() => new Date().toISOString().split('T')[0]);
  const [timeHorizon, setTimeHorizon] = useState(HORIZONS[0].value);
  const [researchDepth, setResearchDepth] = useState(RESEARCH_DEPTHS[0].value);
  const [shallowThinker, setShallowThinker] = useState('openai|gpt-4o-mini');
  const [deepThinker, setDeepThinker] = useState('openai|gpt-4o-mini');
  const [activeMode, setActiveMode] = useState('analysis');
  const [mainPageMode, setMainPageMode] = useState('single');
  const [discoveryTrack, setDiscoveryTrack] = useState(DISCOVERY_TRACKS[0].value);
  const [catalystMode, setCatalystMode] = useState(CATALYST_MODES[0].value);
  const [scanMode, setScanMode] = useState(SCAN_MODES[0].value);
  const [discoveryStage, setDiscoveryStage] = useState(null); // -1 | 0 | 1 | null
  const [activeSessionType, setActiveSessionType] = useState('single');

  const [apiKeys, setApiKeys] = useState(() => {
    const saved = localStorage.getItem('apiKeys');
    return saved ? JSON.parse(saved) : { openai: '', anthropic: '', 'qwen3-cn': '', deepseek: '', glm: '' };
  });
  
  const [activeProviders, setActiveProviders] = useState(() => {
    const saved = localStorage.getItem('activeProviders');
    return saved ? JSON.parse(saved) : { openai: true, anthropic: true, 'qwen3-cn': true, deepseek: true, glm: true };
  });

  const [customModels, setCustomModels] = useState(() => {
    const saved = localStorage.getItem('customModels');
    return saved ? JSON.parse(saved) : {};
  });

  const [hiddenPredefinedModels, setHiddenPredefinedModels] = useState(() => {
    const saved = localStorage.getItem('hiddenPredefinedModels');
    return saved ? JSON.parse(saved) : [];
  });

  const [newModelInputs, setNewModelInputs] = useState({});
  const [newModelThinking, setNewModelThinking] = useState({});

  useEffect(() => {
    localStorage.setItem('apiKeys', JSON.stringify(apiKeys));
  }, [apiKeys]);

  useEffect(() => {
    localStorage.setItem('activeProviders', JSON.stringify(activeProviders));
  }, [activeProviders]);

  useEffect(() => {
    localStorage.setItem('customModels', JSON.stringify(customModels));
  }, [customModels]);

  useEffect(() => {
    localStorage.setItem('hiddenPredefinedModels', JSON.stringify(hiddenPredefinedModels));
  }, [hiddenPredefinedModels]);

  const allShallowModels = [...SHALLOW_MODELS];
  const allDeepModels = [...DEEP_MODELS];
  
  Object.entries(customModels).forEach(([providerId, models]) => {
    if (!Array.isArray(models)) return;
    models.forEach(entry => {
      // Support legacy plain-string entries and new {name, scope} objects
      const modelName = typeof entry === 'string' ? entry : entry.name;
      const scope     = typeof entry === 'string' ? 'both' : (entry.scope || 'both');
      const item = { value: `${providerId}|${modelName}`, label: modelName, detail: 'Custom' };
      if (scope !== 'deep'    && !allShallowModels.some(m => m.value === item.value)) allShallowModels.push(item);
      if (scope !== 'shallow' && !allDeepModels.some(m => m.value === item.value))    allDeepModels.push(item);
    });
  });

  const availableShallowModels = allShallowModels
    .filter(m => activeProviders[m.value.split('|')[0]])
    .filter(m => !hiddenPredefinedModels.includes(m.value));

  const availableDeepModels = allDeepModels
    .filter(m => activeProviders[m.value.split('|')[0]])
    .filter(m => !hiddenPredefinedModels.includes(m.value));

  // Reset selected models if they are hidden or removed
  useEffect(() => {
    if (availableShallowModels.length > 0 && !availableShallowModels.some(m => m.value === shallowThinker)) {
      setShallowThinker(availableShallowModels[0].value);
    }
  }, [availableShallowModels, shallowThinker]);

  useEffect(() => {
    if (availableDeepModels.length > 0 && !availableDeepModels.some(m => m.value === deepThinker)) {
      setDeepThinker(availableDeepModels[0].value);
    }
  }, [availableDeepModels, deepThinker]);
  const [expandedSections, setExpandedSections] = useState(DEFAULT_EXPANDED_REPORT_SECTIONS);
  const toggleSection = useCallback((key) => {
    setExpandedSections(prev => ({ ...prev, [key]: !prev[key] }));
  }, []);
  const [activeSessionId, setActiveSessionId] = useState(null);
  const [isRunning, setIsRunning] = useState(false);
  const [logs, setLogs] = useState([]);
  const [reports, setReports] = useState({});
  const [historyList, setHistoryList] = useState([]);
  const [errorMessage, setErrorMessage] = useState('');
  const [isPending, startTransition] = useTransition();
  const wsRef = useRef(null);
  const logsEndRef = useRef(null);

  const hasConversation = logs.length > 0 || Object.keys(reports).length > 0 || Boolean(activeSessionId);
  const currentHorizon = getHorizonMeta(timeHorizon);
  const availableReports = REPORT_SECTIONS.filter(([key]) => reports[key]);

  // Total tool calls for the live activity bar
  const toolCallCount = useMemo(() => logs.filter(l => l.type === 'tool').length, [logs]);

  // Group low-level tool traffic into compact expandable transcript entries.
  const processedLogs = useMemo(() => {
    return groupTranscriptLogs(logs);
  }, [logs]);

  useEffect(() => {
    document.documentElement.setAttribute('data-theme', theme);
    localStorage.setItem('theme', theme);
  }, [theme]);

  useEffect(() => {
    logsEndRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' });
  }, [logs]);

  const fetchHistory = async () => {
    try {
      const res = await fetch(`${API_BASE}/api/history`);
      if (!res.ok) return;
      const data = await res.json();
      setHistoryList(data);
    } catch (error) {
      console.error('Failed to fetch history', error);
    }
  };

  const deleteHistoryItem = async (e, id) => {
    e.stopPropagation();
    try {
      const res = await fetch(`${API_BASE}/api/history/${id}`, { method: 'DELETE' });
      if (res.ok) {
        if (activeSessionId === id) {
          setActiveSessionId(null);
          setLogs([]);
          setReports({});
          setActiveMode('analysis');
        }
        fetchHistory();
      }
    } catch (error) {
      console.error('Failed to delete history item', error);
    }
  };

  useEffect(() => {
    // Initial server sync; subsequent refreshes are user or socket driven.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    fetchHistory();
  }, []);

  const stopSocket = () => {
    if (wsRef.current) {
      wsRef.current.close();
      wsRef.current = null;
    }
  };

  const createPayload = (overrides = {}) => {
    const deepVal = overrides.deepThinker ?? deepThinker;
    const shallowVal = overrides.shallowThinker ?? shallowThinker;
    
    const [deepProvider, deepModel] = deepVal.split('|');
    const [shallowProvider, shallowModel] = shallowVal.split('|');

    const isThinkingEnabled = (provider, modelName) => {
      if (provider !== 'qwen3-cn') return false;
      const qwenCustoms = customModels['qwen3-cn'] || [];
      const customMatch = qwenCustoms.find(m => (typeof m === 'string' ? m : m.name) === modelName);
      if (customMatch && typeof customMatch === 'object') {
        return !!customMatch.enableThinking;
      }
      const lowerName = modelName.toLowerCase();
      return lowerName.startsWith('qwen3-max') || lowerName.includes('thinking') || lowerName.startsWith('qwq');
    };

    return {
      ticker: (overrides.ticker ?? ticker).trim().toUpperCase(),
      analysis_date: overrides.analysisDate ?? analysisDate,
      analysts: [...DEFAULT_ANALYSTS],
      research_depth: overrides.researchDepth ?? researchDepth,
      llm_provider: deepProvider,
      backend_url: BACKEND_URLS[deepProvider] || null,
      shallow_thinker: shallowModel,
      deep_thinker: deepModel,
      time_horizon: normalizeHorizonValue(overrides.timeHorizon ?? timeHorizon),
      skip_completed_analysts: false,
      mock: false,
      qwen_enable_thinking: isThinkingEnabled(deepProvider, deepModel) || isThinkingEnabled(shallowProvider, shallowModel),
      qwen_thinking_budget: 7000,
      execution: {
        enabled: false,
        provider: 'alpaca',
        paper: true,
        position_size_pct: 0.1,
      },
    };
  };

  const startAnalysis = (overrides = {}) => {
    const payload = createPayload(overrides);
    if (!payload.ticker || isRunning) return;

    stopSocket();
    setTicker(payload.ticker);
    setAnalysisDate(payload.analysis_date);
    setTimeHorizon(payload.time_horizon);
    setResearchDepth(payload.research_depth);
    setActiveSessionId(null);
    setActiveMode('analysis');
    setExpandedSections(DEFAULT_EXPANDED_REPORT_SECTIONS);
    setErrorMessage('');
    const payloadHorizon = HORIZONS.find((item) => item.value === payload.time_horizon) || HORIZONS[0];
    setLogs([makeLog('user', `Analyze ${payload.ticker} for ${payloadHorizon.label.toLowerCase()} positioning.`)]);
    setReports({});
    setIsRunning(true);

    const ws = new WebSocket(`${WS_BASE}/api/ws/analyze`);
    wsRef.current = ws;

    ws.onopen = () => {
      ws.send(JSON.stringify(payload));
    };

    ws.onmessage = (event) => {
      const data = JSON.parse(event.data);

      if (data.event === 'system') {
        setLogs((prev) => [...prev, makeLog('system', data.content)]);
        return;
      }

      if (data.event === 'chunk') {
        if (data.updates) {
          data.updates.forEach((update) => {
            if (update.event === 'message') {
              const rawType = (update.type || 'agent').toLowerCase();
              const frontendType = rawType === 'user' ? 'user'
                : rawType === 'system' ? 'system'
                : rawType === 'toolresult' ? 'tool_output'
                : 'agent';
              setLogs((prev) => [
                ...prev,
                makeLog(frontendType, update.content),
              ]);
            }
            if (update.event === 'tool_call') {
              const args = typeof update.args === 'object' ? JSON.stringify(update.args) : update.args;
              setLogs((prev) => [...prev, makeLog('tool', `${update.tool}: ${args}`)]);
            }
          });
        }
        if (data.reports) {
          setReports((prev) => ({ ...prev, ...data.reports }));
        }
        return;
      }

      if (data.event === 'completed') {
        setIsRunning(false);
        setLogs((prev) => [...prev, makeLog('system', `Analysis completed for ${data.ticker}.`)]);
        fetchHistory();
        return;
      }

      if (data.event === 'error') {
        setIsRunning(false);
        setErrorMessage(data.content);
        setLogs((prev) => [...prev, makeLog('system', `Error: ${data.content}`)]);
      }
    };

    ws.onerror = () => {
      setIsRunning(false);
      setErrorMessage('WebSocket error. Start the backend and try again.');
      setLogs((prev) => [...prev, makeLog('system', 'WebSocket error. Ensure the backend is running.')]);
    };

    ws.onclose = () => {
      setIsRunning(false);
      fetchHistory();
    };
  };

  const createDiscoveryPayload = (overrides = {}) => {
    const deepVal = overrides.deepThinker ?? deepThinker;
    const shallowVal = overrides.shallowThinker ?? shallowThinker;
    
    const [deepProvider, deepModel] = deepVal.split('|');
    const [, shallowModel] = shallowVal.split('|');

    return {
      analysis_mode: 'discovery',
      discovery_mode_variant: 'fresh',
      ticker: null,
      analysis_date: overrides.analysisDate ?? analysisDate,
      discovery_track: overrides.discoveryTrack ?? discoveryTrack,
      discovery_catalyst_mode: overrides.catalystMode ?? catalystMode,
      scan_mode: overrides.scanMode ?? scanMode,
      policy_mode: 'off',
      analysts: [],
      research_depth: 1,
      llm_provider: deepProvider,
      backend_url: BACKEND_URLS[deepProvider] || null,
      shallow_thinker: shallowModel,
      deep_thinker: deepModel,
      execution: {
        enabled: false,
        provider: 'alpaca',
        paper: true,
        position_size_pct: 0.1,
      },
      n_stocks: null,
    };
  };

  const startDiscovery = (overrides = {}) => {
    const payload = createDiscoveryPayload(overrides);
    if (isRunning) return;

    stopSocket();
    setAnalysisDate(payload.analysis_date);
    setDiscoveryTrack(payload.discovery_track);
    setCatalystMode(payload.discovery_catalyst_mode);
    setScanMode(payload.scan_mode ?? SCAN_MODES[0].value);
    setDiscoveryStage(null);
    setActiveSessionId(null);
    setActiveMode('analysis');
    setActiveSessionType('discovery');
    setActiveReport('discovery_report');
    setErrorMessage('');
    
    const trackLabel = DISCOVERY_TRACKS.find(t => t.value === payload.discovery_track)?.label || 'Discovery';
    setLogs([makeLog('user', `Start ${trackLabel} discovery pipeline for ${payload.analysis_date}.`)]);
    setReports({});
    setIsRunning(true);

    const ws = new WebSocket(`${WS_BASE}/api/ws/discovery`);
    wsRef.current = ws;

    ws.onopen = () => {
      ws.send(JSON.stringify(payload));
    };

    ws.onmessage = (event) => {
      const data = JSON.parse(event.data);

      if (data.event === 'system') {
        setLogs((prev) => [...prev, makeLog('system', data.content)]);
        return;
      }

      if (data.event === 'chunk') {
        if (data.updates) {
          data.updates.forEach((update) => {
            if (update.event === 'message') {
              const rawType = (update.type || 'agent').toLowerCase();
              const frontendType = rawType === 'user' ? 'user'
                : rawType === 'system' ? 'system'
                : rawType === 'toolresult' ? 'tool_output'
                : 'agent';
              setLogs((prev) => [
                ...prev,
                makeLog(frontendType, update.content),
              ]);
            }
            if (update.event === 'tool_call') {
              const args = typeof update.args === 'object' ? JSON.stringify(update.args) : update.args;
              setLogs((prev) => [...prev, makeLog('tool', `${update.tool}: ${args}`)]);
            }
          });
        }
        if (data.reports) {
          setReports((prev) => ({ ...prev, ...data.reports }));
        }
        return;
      }

      if (data.event === 'stage') {
        const arrow = data.status === 'started' ? '▶' : '✓';
        const stageNum = data.stage < 0 ? '-1' : String(data.stage);
        setLogs((prev) => [...prev, makeLog('system', `${arrow} Stage ${stageNum}: ${data.label}`)]);
        if (data.status === 'started') setDiscoveryStage(data.stage);
        if (data.status === 'completed' && data.stage === 1) setDiscoveryStage(null);
        return;
      }

      if (data.event === 'theme_candidates') {
        setReports((prev) => ({ ...prev, theme_candidates_json: data.candidates }));
        return;
      }

      if (data.event === 'completed') {
        setDiscoveryStage(null);
        setIsRunning(false);
        const tickerCount = (data.tickers || []).length;
        const themeCount = data.candidate_count || 0;
        setLogs((prev) => [
          ...prev,
          makeLog('system', `Discovery complete — ${tickerCount} ticker${tickerCount !== 1 ? 's' : ''} found, ${themeCount} theme signal${themeCount !== 1 ? 's' : ''}.`),
        ]);
        fetchHistory();
        return;
      }

      if (data.event === 'error') {
        setDiscoveryStage(null);
        setIsRunning(false);
        setErrorMessage(data.content);
        setLogs((prev) => [...prev, makeLog('system', `Error: ${data.content}`)]);
      }
    };

    ws.onerror = () => {
      setIsRunning(false);
      setErrorMessage('WebSocket error. Start the backend and try again.');
      setLogs((prev) => [...prev, makeLog('system', 'WebSocket error. Ensure the backend is running.')]);
    };

    ws.onclose = () => {
      setIsRunning(false);
      fetchHistory();
    };
  };

  const handleStop = () => {
    stopSocket();
    setIsRunning(false);
    setLogs((prev) => [...prev, makeLog('system', 'Analysis stopped by user.')]);
  };

  const newChat = () => {
    handleStop();
    setTicker('');
    setReports({});
    setLogs([]);
    setActiveSessionId(null);
    setErrorMessage('');
    setActiveMode('analysis');
    setActiveSessionType(mainPageMode);
    setActiveReport(mainPageMode === 'discovery' ? 'discovery_report' : 'market_report');
    setExpandedSections(DEFAULT_EXPANDED_REPORT_SECTIONS);
  };

  const loadHistoryItem = async (id) => {
    try {
      stopSocket();
      setIsRunning(false);
      setErrorMessage('');
      const res = await fetch(`${API_BASE}/api/history/${id}`);
      if (!res.ok) throw new Error('History session not found');
      const data = await res.json();

      startTransition(() => {
        setActiveSessionId(data.id);
        setTicker(data.ticker);
        setAnalysisDate(data.analysis_date);
        setTimeHorizon(normalizeHorizonValue(data.time_horizon));

        // Normalize stored logs from backend format → frontend display format.
        // Backend stores: {event:"message", type:"Reasoning"/"User"/..., content:"..."}
        //             and: {event:"tool_call", tool:"...", args:{...}}
        // Frontend expects: {id, type:"agent"/"user"/"tool"/"system", content:"..."}
        const normalizeLog = (log, index) => {
          const id = `history-${data.id}-${index}`;
          if (log.event === 'tool_call') {
            const args = typeof log.args === 'object' ? JSON.stringify(log.args) : (log.args ?? '');
            return { id, type: 'tool', content: `${log.tool}: ${args}` };
          }
          if (log.event === 'message') {
            const rawType = (log.type || 'agent').toLowerCase();
            const frontendType = rawType === 'user' ? 'user'
              : rawType === 'system' ? 'system'
              : rawType === 'toolresult' ? 'tool_output'
              : 'agent';
            return { id, type: frontendType, content: log.content || '' };
          }
          // Fallback for any logs already in frontend format (type but no event)
          return { id, type: log.type || 'agent', content: log.content || '' };
        };

        setLogs((Array.isArray(data.logs) ? data.logs : []).map(normalizeLog));
        setReports(typeof data.reports === 'object' && data.reports ? data.reports : {});
        setActiveMode('reports');
        setExpandedSections(DEFAULT_EXPANDED_REPORT_SECTIONS);
      });
    } catch (error) {
      setErrorMessage(error.message);
    }
  };

  const renderComposer = (isWelcome = false) => {
    if (mainPageMode === 'discovery') {
      return (
        <div className={`gemini-composer ${isWelcome ? 'large' : 'compact'}`} style={{ justifyContent: 'center', padding: '16px' }}>
          <button
            className="submit-circle primary"
            onClick={() => startDiscovery()}
            disabled={isRunning}
            style={{ width: '100%', height: '56px', borderRadius: '28px', fontSize: '18px', gap: '8px', padding: '0 24px' }}
          >
            {isRunning ? (
              <>
                <svg viewBox="0 0 24 24" fill="currentColor" width="24" height="24"><path d="M6 6h12v12H6z"/></svg>
                Stop Discovery
              </>
            ) : (
              <>
                <svg viewBox="0 0 24 24" fill="currentColor" width="24" height="24"><path d="M2.01 21L23 12 2.01 3 2 10l15 2-15 2z"/></svg>
                Start AI Discovery
              </>
            )}
          </button>
        </div>
      );
    }

    return (
      <div className={`gemini-composer ${isWelcome ? 'large' : 'compact'}`}>
        <div className="gemini-input-row">
          <input
            className="gemini-input"
            value={ticker}
            onChange={(event) => setTicker(event.target.value.toUpperCase())}
            onKeyDown={(event) => event.key === 'Enter' && startAnalysis()}
            placeholder="Ask about a ticker (e.g. AAPL, NVDA)..."
            disabled={isRunning}
          />
          <div className="input-actions">
            <CustomSelect
              value={researchDepth}
              onChange={(val) => setResearchDepth(Number(val))}
              options={RESEARCH_DEPTHS}
              disabled={isRunning}
              title={RESEARCH_DEPTHS.find((item) => item.value === researchDepth)?.label || 'Shallow'}
              icon={
                <svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
                  <path d="M12 3 3 7.5l9 4.5 9-4.5L12 3zm-6.76 7.56L3 11.69l9 4.5 9-4.5-2.24-1.13L12 13.94l-6.76-3.38zm0 4.2L3 15.89l9 4.5 9-4.5-2.24-1.13L12 18.14l-6.76-3.38z" />
                </svg>
              }
            />
            {isRunning ? (
              <button className="submit-circle danger" onClick={handleStop}>
                <svg viewBox="0 0 24 24" fill="currentColor"><path d="M6 6h12v12H6z"/></svg>
              </button>
            ) : (
              <button className="submit-circle primary" onClick={() => startAnalysis()} disabled={!ticker.trim()}>
                <svg viewBox="0 0 24 24" fill="currentColor"><path d="M2.01 21L23 12 2.01 3 2 10l15 2-15 2z"/></svg>
              </button>
            )}
          </div>
        </div>
      </div>
    );
  };

  const handleDeepThinkerChange = (val) => {
    setDeepThinker(val);
  };

  const handleShallowThinkerChange = (val) => {
    setShallowThinker(val);
  };

  const renderConfigStrip = () => {
    if (mainPageMode === 'discovery') {
      return (
        <div className="config-strip">
          <div className="config-card">
            <div className="config-card-icon horizon">
              <svg viewBox="0 0 24 24" width="18" height="18" fill="currentColor"><path d="M19.14,12.94c0.04-0.3,0.06-0.61,0.06-0.94c0-0.32-0.02-0.64-0.06-0.94l2.03-1.58c0.18-0.14,0.23-0.41,0.12-0.61 l-1.92-3.32c-0.12-0.22-0.37-0.29-0.59-0.22l-2.39,0.96c-0.5-0.38-1.03-0.7-1.62-0.94L14.4,2.81c-0.04-0.24-0.24-0.41-0.48-0.41 h-3.84c-0.24,0-0.43,0.17-0.47,0.41L9.25,5.35C8.66,5.59,8.12,5.92,7.63,6.29L5.24,5.33c-0.22-0.08-0.47,0-0.59,0.22L2.73,8.87 C2.62,9.08,2.66,9.34,2.86,9.48l2.03,1.58C4.84,11.36,4.8,11.69,4.8,12s0.02,0.64,0.06,0.94l-2.03,1.58 c-0.18,0.14-0.23,0.41-0.12,0.61l1.92,3.32c0.12,0.22,0.37,0.29,0.59,0.22l2.39-0.96c0.5,0.38,1.03,0.7,1.62,0.94l0.36,2.54 c0.05,0.24,0.24,0.41,0.48,0.41h3.84c0.24,0,0.43-0.17,0.47-0.41l0.36-2.54c0.59-0.24,1.13-0.56,1.62-0.94l2.39,0.96 c0.22,0.08,0.47,0,0.59-0.22l1.92-3.32c0.12-0.22,0.07-0.49-0.12-0.61L19.14,12.94z M12,15.6c-1.98,0-3.6-1.62-3.6-3.6 s1.62-3.6,3.6-3.6s3.6,1.62,3.6,3.6S13.98,15.6,12,15.6z"/></svg>
            </div>
            <div className="config-card-body">
              <span className="config-label">Track</span>
              <CustomSelect
                value={discoveryTrack}
                onChange={(val) => setDiscoveryTrack(val)}
                options={DISCOVERY_TRACKS}
                disabled={isRunning}
              />
            </div>
          </div>

          <div className="config-card">
            <div className="config-card-icon">
              <svg viewBox="0 0 24 24" width="18" height="18" fill="currentColor"><path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm1 15h-2v-6h2v6zm0-8h-2V7h2v2z"/></svg>
            </div>
            <div className="config-card-body">
              <span className="config-label">Catalyst Filter</span>
              <CustomSelect
                value={catalystMode}
                onChange={(val) => setCatalystMode(val)}
                options={CATALYST_MODES}
                disabled={isRunning}
              />
            </div>
          </div>

          <div className="config-card">
            <div className="config-card-icon">
              <svg viewBox="0 0 24 24" width="18" height="18" fill="currentColor"><path d="M11.5 2C6.81 2 3 5.81 3 10.5S6.81 19 11.5 19h.5v3c4.86-2.34 8-7 8-11.5C20 5.81 16.19 2 11.5 2zm1 14.5h-2v-2h2v2zm0-4h-2c0-3.25 3-3 3-5 0-1.1-.9-2-2-2s-2 .9-2 2h-2c0-2.21 1.79-4 4-4s4 1.79 4 4c0 2.5-3 2.75-3 5z"/></svg>
            </div>
            <div className="config-card-body">
              <span className="config-label">Scan Mode</span>
              <CustomSelect
                value={scanMode}
                onChange={(val) => setScanMode(val)}
                options={SCAN_MODES}
                disabled={isRunning}
              />
            </div>
          </div>

          <div className="config-card">
            <div className="config-card-icon date">
              <svg viewBox="0 0 24 24" width="18" height="18" fill="currentColor"><path d="M19 4h-1V2h-2v2H8V2H6v2H5c-1.11 0-1.99.9-1.99 2L3 20c0 1.1.89 2 2 2h14c1.1 0 2-.9 2-2V6c0-1.1-.9-2-2-2zm0 16H5V10h14v10z"/></svg>
            </div>
            <div className="config-card-body">
              <span className="config-label">Date</span>
              <CustomDatePicker
                value={analysisDate}
                onChange={(val) => setAnalysisDate(val)}
                disabled={isRunning}
              />
            </div>
          </div>

          <div className="config-card">
            <div className="config-card-icon deep">
              <svg viewBox="0 0 24 24" width="18" height="18" fill="currentColor"><path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm-1 14.5v-9l6 4.5-6 4.5z"/></svg>
            </div>
            <div className="config-card-body">
              <span className="config-label">Deep Thinker</span>
              <CustomSelect
                value={deepThinker}
                onChange={(val) => setDeepThinker(val)}
                options={availableDeepModels.length > 0 ? availableDeepModels : DEEP_MODELS}
                disabled={isRunning}
                title={(availableDeepModels.length > 0 ? availableDeepModels : DEEP_MODELS).find(m => m.value === deepThinker)?.label || 'Select'}
              />
            </div>
          </div>
        </div>
      );
    }

    return (
      <div className="config-strip">
        <div className="config-card">
          <div className="config-card-icon">
            <svg viewBox="0 0 24 24" width="18" height="18" fill="currentColor"><path d="M13 2.05v3.03c3.39.49 6 3.39 6 6.92 0 .9-.18 1.75-.48 2.54l2.6 1.53c.56-1.24.88-2.62.88-4.07 0-5.18-3.95-9.45-9-9.95zM12 19c-3.87 0-7-3.13-7-7 0-3.53 2.61-6.43 6-6.92V2.05c-5.05.5-9 4.76-9 9.95 0 5.52 4.47 10 9.99 10 3.31 0 6.24-1.61 8.06-4.09l-2.6-1.53C16.17 17.98 14.21 19 12 19z"/></svg>
          </div>
          <div className="config-card-body">
            <span className="config-label">Shallow Thinker</span>
            <CustomSelect
              value={shallowThinker}
              onChange={(val) => setShallowThinker(val)}
              options={availableShallowModels.length > 0 ? availableShallowModels : SHALLOW_MODELS}
              disabled={isRunning}
              title={(availableShallowModels.length > 0 ? availableShallowModels : SHALLOW_MODELS).find(m => m.value === shallowThinker)?.label || 'Select'}
            />
          </div>
        </div>

        <div className="config-card">
          <div className="config-card-icon deep">
            <svg viewBox="0 0 24 24" width="18" height="18" fill="currentColor"><path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm-1 14.5v-9l6 4.5-6 4.5z"/></svg>
          </div>
          <div className="config-card-body">
            <span className="config-label">Deep Thinker</span>
            <CustomSelect
              value={deepThinker}
              onChange={(val) => setDeepThinker(val)}
              options={availableDeepModels.length > 0 ? availableDeepModels : DEEP_MODELS}
              disabled={isRunning}
              title={(availableDeepModels.length > 0 ? availableDeepModels : DEEP_MODELS).find(m => m.value === deepThinker)?.label || 'Select'}
            />
          </div>
        </div>

        <div className="config-card">
          <div className="config-card-icon date">
            <svg viewBox="0 0 24 24" width="18" height="18" fill="currentColor"><path d="M19 4h-1V2h-2v2H8V2H6v2H5c-1.11 0-1.99.9-1.99 2L3 20c0 1.1.89 2 2 2h14c1.1 0 2-.9 2-2V6c0-1.1-.9-2-2-2zm0 16H5V10h14v10z"/></svg>
          </div>
          <div className="config-card-body">
            <span className="config-label">Analysis Date</span>
            <CustomDatePicker
              value={analysisDate}
              onChange={(val) => setAnalysisDate(val)}
              disabled={isRunning}
            />
          </div>
        </div>

        <div className="config-card">
          <div className="config-card-icon horizon">
            <svg viewBox="0 0 24 24" width="18" height="18" fill="currentColor"><path d="M11.99 2C6.47 2 2 6.48 2 12s4.47 10 9.99 10C17.52 22 22 17.52 22 12S17.52 2 11.99 2zM12 20c-4.42 0-8-3.58-8-8s3.58-8 8-8 8 3.58 8 8-3.58 8-8 8zm.5-13H11v6l5.25 3.15.75-1.23-4.5-2.67z"/></svg>
          </div>
          <div className="config-card-body">
            <span className="config-label">Time Horizon</span>
            <CustomSelect
              value={timeHorizon}
              onChange={(val) => setTimeHorizon(val)}
              options={HORIZONS}
              disabled={isRunning}
            />
          </div>
        </div>
      </div>
    );
  };

  const [showApiKey, setShowApiKey] = useState({});
  const [newModelScope, setNewModelScope] = useState({});

  const cycleScope = (providerId) => {
    setNewModelScope(prev => {
      const cur = prev[providerId] || 'both';
      const next = cur === 'both' ? 'shallow' : cur === 'shallow' ? 'deep' : 'both';
      return { ...prev, [providerId]: next };
    });
  };

  const addModel = (providerId) => {
    const name = (newModelInputs[providerId] || '').trim();
    if (!name) return;
    const scope = newModelScope[providerId] || 'both';
    const enableThinking = providerId === 'qwen3-cn' ? !!newModelThinking[providerId] : false;
    setCustomModels(prev => ({
      ...prev,
      [providerId]: [...(prev[providerId] || []), { name, scope, enableThinking }],
    }));
    setNewModelInputs(prev => ({ ...prev, [providerId]: '' }));
    setNewModelThinking(prev => ({ ...prev, [providerId]: false }));
  };

  const deleteModel = (providerId, index) => {
    setCustomModels(prev => ({
      ...prev,
      [providerId]: (prev[providerId] || []).filter((_, i) => i !== index),
    }));
  };

  const hidePredefinedModel = (providerId, modelName) => {
    const key = `${providerId}|${modelName}`;
    setHiddenPredefinedModels(prev => [...prev, key]);
  };

  const showPredefinedModel = (providerId, modelName) => {
    const key = `${providerId}|${modelName}`;
    setHiddenPredefinedModels(prev => prev.filter(k => k !== key));
  };


  const renderSettings = () => {
    const PROVIDERS = [
      { id: 'openai', name: 'OpenAI', url: 'https://platform.openai.com/api-keys' },
      { id: 'anthropic', name: 'Anthropic', url: 'https://console.anthropic.com/' },
      { id: 'qwen3-cn', name: 'Qwen (DashScope)', url: 'https://dashscope.console.aliyun.com/' },
      { id: 'deepseek', name: 'DeepSeek', url: 'https://platform.deepseek.com/' },
      { id: 'glm', name: 'GLM (ZhipuAI)', url: 'https://open.bigmodel.cn/' }
    ];

    const SCOPE_META = {
      both:    { label: 'Both',    color: '#4285f4' },
      shallow: { label: 'Shallow', color: '#9b72cb' },
      deep:    { label: 'Deep',    color: '#d96570' },
    };

    return (
      <div className="settings-page">
        <div className="settings-header">
          <h2>Model Providers</h2>
          <p>Enable providers, set API keys, and add custom model IDs for each agent tier.</p>
        </div>

        <div className="provider-grid">
          {PROVIDERS.map(provider => {
            const isActive = activeProviders[provider.id];
            const models = customModels[provider.id] || [];
            const scope = newModelScope[provider.id] || 'both';
            const scopeMeta = SCOPE_META[scope];
            const predefined = getPredefinedModelsForProvider(provider.id);

            return (
              <div key={provider.id} className={`provider-card ${isActive ? 'active' : ''}`}>
                {/* Header row */}
                <div className="provider-card-header">
                  <div className="provider-info">
                    <h3>{provider.name}</h3>
                    <a href={provider.url} target="_blank" rel="noreferrer" className="get-key-chip">
                      <svg viewBox="0 0 24 24" width="11" height="11" fill="currentColor"><path d="M19 19H5V5h7V3H5a2 2 0 00-2 2v14a2 2 0 002 2h14a2 2 0 002-2v-7h-2v7zM14 3v2h3.59l-9.83 9.83 1.41 1.41L19 6.41V10h2V3h-7z"/></svg>
                      Get API Key
                    </a>
                  </div>
                  <label className="switch">
                    <input
                      type="checkbox"
                      checked={isActive}
                      onChange={(e) => setActiveProviders(prev => ({ ...prev, [provider.id]: e.target.checked }))}
                    />
                    <span className="slider round"></span>
                  </label>
                </div>

                {isActive && (
                  <div className="provider-body">
                    {/* API Key input */}
                    <div className="api-key-field">
                      <svg className="api-key-field-icon" viewBox="0 0 24 24" width="16" height="16" fill="currentColor"><path d="M12.65 10A5.99 5.99 0 007 6c-3.31 0-6 2.69-6 6s2.69 6 6 6a5.99 5.99 0 005.65-4H17v4h4v-4h2v-4H12.65zM7 14c-1.1 0-2-.9-2-2s.9-2 2-2 2 .9 2 2-.9 2-2 2z"/></svg>
                      <input
                        type={showApiKey[provider.id] ? 'text' : 'password'}
                        className="api-key-input"
                        placeholder={`${provider.name} API Key`}
                        value={apiKeys[provider.id] || ''}
                        onChange={(e) => setApiKeys(prev => ({ ...prev, [provider.id]: e.target.value }))}
                      />
                      <button
                        type="button"
                        className="api-key-eye"
                        onClick={() => setShowApiKey(prev => ({ ...prev, [provider.id]: !prev[provider.id] }))}
                        title={showApiKey[provider.id] ? 'Hide key' : 'Reveal key'}
                      >
                        {showApiKey[provider.id] ? (
                          <svg viewBox="0 0 24 24" width="16" height="16" fill="currentColor"><path d="M12 7c2.76 0 5 2.24 5 5 0 .65-.13 1.26-.36 1.83l2.92 2.92c1.51-1.26 2.7-2.89 3.43-4.75-1.73-4.39-6-7.5-11-7.5-1.4 0-2.74.25-3.98.7l2.16 2.16C10.74 7.13 11.35 7 12 7zM2 4.27l2.28 2.28.46.46A11.804 11.804 0 001 12c1.73 4.39 6 7.5 11 7.5 1.55 0 3.03-.3 4.38-.84l.42.42L19.73 22 21 20.73 3.27 3 2 4.27zM7.53 9.8l1.55 1.55c-.05.21-.08.43-.08.65 0 1.66 1.34 3 3 3 .22 0 .44-.03.65-.08l1.55 1.55c-.67.33-1.41.53-2.2.53-2.76 0-5-2.24-5-5 0-.79.2-1.53.53-2.2zm4.31-.78l3.15 3.15.02-.16c0-1.66-1.34-3-3-3l-.17.01z"/></svg>
                        ) : (
                          <svg viewBox="0 0 24 24" width="16" height="16" fill="currentColor"><path d="M12 4.5C7 4.5 2.73 7.61 1 12c1.73 4.39 6 7.5 11 7.5s9.27-3.11 11-7.5c-1.73-4.39-6-7.5-11-7.5zM12 17c-2.76 0-5-2.24-5-5s2.24-5 5-5 5 2.24 5 5-2.24 5-5 5zm0-8c-1.66 0-3 1.34-3 3s1.34 3 3 3 3-1.34 3-3-1.34-3-3-3z"/></svg>
                        )}
                      </button>
                    </div>

                    {/* Unified Models section */}
                    <div className="custom-models-section">
                      <div className="custom-models-header">
                        <span className="custom-models-title">Models Configuration</span>
                        <span className="custom-models-hint">Remove default models or manage custom ones.</span>
                      </div>

                      <div className="model-list">
                        {/* Predefined Models */}
                        {predefined
                          .filter(entry => !hiddenPredefinedModels.includes(`${provider.id}|${entry.name}`))
                          .map((entry, idx) => {
                            const meta = SCOPE_META[entry.scope] || SCOPE_META.both;
                            return (
                              <div key={`predefined-${idx}`} className="model-list-item">
                                <span className="model-scope-badge" style={{ color: meta.color, borderColor: `${meta.color}44` }}>{meta.label}</span>
                                <span className="model-list-name" title={entry.name}>
                                  {entry.label} <small style={{ color: 'var(--faint)', fontFamily: 'inherit' }}>({entry.name})</small>
                                </span>
                                <button
                                  type="button"
                                  className="model-delete-btn"
                                  onClick={() => hidePredefinedModel(provider.id, entry.name)}
                                  title="Remove model"
                                >
                                  <svg viewBox="0 0 24 24" width="13" height="13" fill="currentColor"><path d="M19 6.41L17.59 5 12 10.59 6.41 5 5 6.41 10.59 12 5 17.59 6.41 19 12 13.41 17.59 19 19 17.59 13.41 12z"/></svg>
                                </button>
                              </div>
                            );
                          })}

                        {/* Custom Models */}
                        {models.length > 0 && models.map((entry, i) => {
                          // Support legacy plain-string entries
                          const modelName = typeof entry === 'string' ? entry : entry.name;
                          const modelScope = typeof entry === 'string' ? 'both' : entry.scope;
                          const meta = SCOPE_META[modelScope] || SCOPE_META.both;
                          return (
                            <div key={`custom-${i}`} className="model-list-item">
                              <span className="model-scope-badge" style={{ color: meta.color, borderColor: `${meta.color}44` }}>{meta.label}</span>
                              <span className="model-list-name" title={modelName}>{modelName}</span>
                              {entry && typeof entry === 'object' && entry.enableThinking && (
                                <span className="model-scope-badge" style={{ color: '#10b981', borderColor: 'rgba(16, 185, 129, 0.27)', textTransform: 'none', letterSpacing: 'normal' }}>Thinking</span>
                              )}
                              <button className="model-delete-btn" onClick={() => deleteModel(provider.id, i)} title="Remove model">
                                <svg viewBox="0 0 24 24" width="13" height="13" fill="currentColor"><path d="M19 6.41L17.59 5 12 10.59 6.41 5 5 6.41 10.59 12 5 17.59 6.41 19 12 13.41 17.59 19 19 17.59 13.41 12z"/></svg>
                              </button>
                            </div>
                          );
                        })}
                      </div>

                      {/* Add model row */}
                      <div className="model-add-row" style={{ flexWrap: 'wrap', gap: '8px' }}>
                        <button
                          type="button"
                          className="scope-toggle"
                          style={{ color: scopeMeta.color, borderColor: `${scopeMeta.color}55`, background: `${scopeMeta.color}11` }}
                          onClick={() => cycleScope(provider.id)}
                          title="Click to cycle: Both → Shallow → Deep"
                        >
                          {scopeMeta.label}
                        </button>
                        <input
                          className="model-name-input"
                          placeholder="model-id (e.g. qwen3-max)"
                          value={newModelInputs[provider.id] || ''}
                          onChange={(e) => setNewModelInputs(prev => ({ ...prev, [provider.id]: e.target.value }))}
                          onKeyDown={(e) => { if (e.key === 'Enter') addModel(provider.id); }}
                        />
                        {provider.id === 'qwen3-cn' && (
                          <label style={{ display: 'flex', alignItems: 'center', gap: '6px', fontSize: '13px', color: 'var(--muted)', cursor: 'pointer', userSelect: 'none', marginRight: '4px' }}>
                            <input
                              type="checkbox"
                              checked={newModelThinking[provider.id] || false}
                              onChange={(e) => setNewModelThinking(prev => ({ ...prev, [provider.id]: e.target.checked }))}
                              style={{ width: '15px', height: '15px', accentColor: 'var(--accent)' }}
                            />
                            Thinking Mode
                          </label>
                        )}
                        <button
                          type="button"
                          className="model-add-btn"
                          onClick={() => addModel(provider.id)}
                          disabled={!(newModelInputs[provider.id] || '').trim()}
                        >
                          <svg viewBox="0 0 24 24" width="14" height="14" fill="currentColor"><path d="M19 13h-6v6h-2v-6H5v-2h6V5h2v6h6v2z"/></svg>
                          Add
                        </button>
                      </div>
                    </div>
                  </div>
                )}
              </div>
            );
          })}
        </div>

        <div className="settings-footer">
          <p className="settings-note">Keys are stored locally in your browser and sent to your backend proxy only.</p>
          <button className="primary-btn" onClick={() => setActiveMode('analysis')}>Done</button>
        </div>
      </div>
    );
  };

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <button className="brand" onClick={newChat}>
          <span className="brand-mark"><LogoIcon /></span>
          <span>
            <strong>Boolean Trader</strong>
            <small>Agentic stock analysis</small>
          </span>
        </button>

        <button className="new-chat" onClick={newChat}>
          <span>+</span>
          New analysis
        </button>

        {hasConversation && (
          <nav className="segmented-modes" aria-label="Functionality">
            {MODES.map((mode) => (
              <button
                key={mode.id}
                className={activeMode === mode.id ? 'segmented-item active' : 'segmented-item'}
                onClick={() => setActiveMode(mode.id)}
                title={mode.description}
              >
                {mode.icon}
                <span>{mode.label}</span>
              </button>
            ))}
          </nav>
        )}

        <section className="history-block">
          <div className="sidebar-heading">
            <span>History</span>
            <button onClick={fetchHistory}>Refresh</button>
          </div>
          <div className="history-list">
            {historyList.length === 0 ? (
              <p className="empty-copy">No saved sessions yet.</p>
            ) : (
              historyList.map((item) => (
                <div key={item.id} className={activeSessionId === item.id ? 'history-item active' : 'history-item'}>
                  <button className="history-item-content" onClick={() => loadHistoryItem(item.id)}>
                    <span>{item.ticker}</span>
                    <small>{(item.time_horizon || '').replaceAll('_', ' ')} · {formatDateTime(item.created_at)}</small>
                  </button>
                  <button className="history-delete-btn" onClick={(e) => deleteHistoryItem(e, item.id)} title="Delete session">
                    <svg viewBox="0 0 24 24" width="16" height="16" fill="currentColor"><path d="M16 9v10H8V9h8m-1.5-6h-5l-1 1H5v2h14V4h-3.5l-1-1zM18 7H6v12c0 1.1.9 2 2 2h8c1.1 0 2-.9 2-2V7z"/></svg>
                  </button>
                </div>
              ))
            )}
          </div>
        </section>

        <div style={{ marginTop: 'auto' }}>
          <button 
            className={`mode-item ${activeMode === 'settings' ? 'active' : ''}`} 
            onClick={() => setActiveMode('settings')}
            style={{ width: '100%', marginBottom: '8px' }}
          >
            <span style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
              <svg viewBox="0 0 24 24" width="16" height="16" fill="currentColor"><path d="M19.14,12.94c0.04-0.3,0.06-0.61,0.06-0.94c0-0.32-0.02-0.64-0.06-0.94l2.03-1.58c0.18-0.14,0.23-0.41,0.12-0.61 l-1.92-3.32c-0.12-0.22-0.37-0.29-0.59-0.22l-2.39,0.96c-0.5-0.38-1.03-0.7-1.62-0.94L14.4,2.81c-0.04-0.24-0.24-0.41-0.48-0.41 h-3.84c-0.24,0-0.43,0.17-0.47,0.41L9.25,5.35C8.66,5.59,8.12,5.92,7.63,6.29L5.24,5.33c-0.22-0.08-0.47,0-0.59,0.22L2.73,8.87 C2.62,9.08,2.66,9.34,2.86,9.48l2.03,1.58C4.84,11.36,4.8,11.69,4.8,12s0.02,0.64,0.06,0.94l-2.03,1.58 c-0.18,0.14-0.23,0.41-0.12,0.61l1.92,3.32c0.12,0.22,0.37,0.29,0.59,0.22l2.39-0.96c0.5,0.38,1.03,0.7,1.62,0.94l0.36,2.54 c0.05,0.24,0.24,0.41,0.48,0.41h3.84c0.24,0,0.43-0.17,0.47-0.41l0.36-2.54c0.59-0.24,1.13-0.56,1.62-0.94l2.39,0.96 c0.22,0.08,0.47,0,0.59-0.22l1.92-3.32c0.12-0.22,0.07-0.49-0.12-0.61L19.14,12.94z M12,15.6c-1.98,0-3.6-1.62-3.6-3.6 s1.62-3.6,3.6-3.6s3.6,1.62,3.6,3.6S13.98,15.6,12,15.6z"/></svg>
              Settings
            </span>
          </button>
          <button className="theme-button" onClick={() => setTheme((prev) => (prev === 'dark' ? 'light' : 'dark'))}>
            {theme === 'dark' ? 'Light mode' : 'Dark mode'}
          </button>
        </div>
      </aside>

      <main className="workspace">
        {activeMode === 'settings' ? (
          renderSettings()
        ) : hasConversation ? (
          <>
            <header className="session-bar">
              <div className="session-title">
                {activeSessionType === 'discovery' ? (
                  <>
                    <span className="ticker-pill">AI Discovery</span>
                    <span className="session-meta">
                      {DISCOVERY_TRACKS.find(t => t.value === discoveryTrack)?.label} • {analysisDate}
                    </span>
                  </>
                ) : (
                  <>
                    <span className="ticker-pill">{ticker || 'Session'}</span>
                    <span className="session-meta">{currentHorizon.label} analysis</span>
                  </>
                )}
              </div>
              <div className="session-actions">
                <div className={isRunning ? 'run-status active' : 'run-status'}>
                  <span />
                  {isRunning ? 'Agents running' : isPending ? 'Loading session' : 'Idle'}
                </div>
                {isRunning && (
                  <button className="stop-analysis-btn" onClick={handleStop} type="button">
                    <svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
                      <path d="M6 6h12v12H6z" />
                    </svg>
                    Stop
                  </button>
                )}
              </div>
            </header>

            {errorMessage && <div className="error-banner">{errorMessage}</div>}

            <div className="content-grid">
          <section className="conversation-panel">
            <div className="panel-header">
              <div>
                <h2>Transcript</h2>
                <p>Agent activity and intermediate reasoning stream.</p>
              </div>
            </div>
            <div className="message-list">
              {logs.length === 0 ? (
                <div className="empty-state">
                  <h3>No active thread</h3>
                  <p>Choose a ticker or open a previous analysis from history.</p>
                </div>
              ) : (
                processedLogs.map((log) => {
                  // System status → subtle centered pill
                  if (log.type === 'system') {
                    return (
                      <div key={log.id} className="status-line">
                        <span className="status-dot" />
                        <span>{log.content}</span>
                      </div>
                    );
                  }
                  // Grouped tool calls → compact collapsible chip
                  if (log.type === 'tool_group') {
                    return <ToolGroupMessage key={log.id} tools={log.tools} />;
                  }

                  if (log.type === 'retrieved_info_group') {
                    return <RetrievedInfoGroupMessage key={log.id} items={log.items} />;
                  }
                  
                  // Hide completely empty messages
                  if (!log.content || log.content.trim() === '') {
                    return null;
                  }


                  // User & agent messages → full bubbles
                  const presentation = getTranscriptMessagePresentation(log.type);
                  return (
                    <article key={log.id} className={`message ${log.type} ${presentation.side}`}>
                      <TranscriptAvatar avatar={presentation.avatar} label={presentation.label} />
                      <div className="message-bubble">
                        <div className="message-meta">{presentation.label}</div>
                        {log.type === 'user'
                          ? <p>{log.content}</p>
                          : <CollapsibleContent content={log.content} />
                        }
                      </div>
                    </article>
                  );
                })
              )}
              {isRunning && (
                <div className="live-activity-bar">
                  <span className="activity-pulse-dot" />
                  <span>Agents working</span>
                  {toolCallCount > 0 && (
                    <span className="activity-calls">{toolCallCount} tool call{toolCallCount !== 1 ? 's' : ''}</span>
                  )}
                </div>
              )}
              <div ref={logsEndRef} />
            </div>
          </section>

          <section className="inspector-panel">
            {activeMode === 'analysis' && activeSessionType === 'discovery' && (
              <>
                <div className="panel-header">
                  <div>
                    <h2>Discovery pipeline</h2>
                    <p>Stage progress and theme signals.</p>
                  </div>
                </div>

                {/* Pipeline stage progress bar */}
                <div style={{ padding: '0 16px 16px' }}>
                  {[
                    { stage: -1, label: 'Theme Engine' },
                    { stage: 0,  label: 'Universe Screen' },
                    { stage: 1,  label: 'Enrich & Score' },
                  ].map(({ stage, label }) => {
                    const isDone = discoveryStage !== null && stage < discoveryStage;
                    const isActive = discoveryStage === stage;
                    const isPending = discoveryStage === null || stage > discoveryStage;
                    return (
                      <div key={stage} style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '8px' }}>
                        <div style={{
                          width: '20px', height: '20px', borderRadius: '50%',
                          flexShrink: 0,
                          display: 'flex', alignItems: 'center', justifyContent: 'center',
                          fontSize: '10px', fontWeight: 700,
                          background: isDone
                            ? 'color-mix(in srgb, var(--accent) 20%, transparent)'
                            : isActive
                            ? 'var(--accent)'
                            : 'var(--surface-muted)',
                          border: `1px solid ${isDone || isActive ? 'var(--accent)' : 'var(--border)'}`,
                          color: isActive ? 'var(--accent-ink)' : isDone ? 'var(--accent)' : 'var(--muted)',
                        }}>
                          {isDone ? '✓' : stage < 0 ? '−1' : stage}
                        </div>
                        <span style={{
                          fontSize: '12px',
                          color: isDone ? 'var(--text)' : isActive ? 'var(--accent)' : 'var(--muted)',
                          fontWeight: isActive ? 600 : 400,
                        }}>
                          {label}
                        </span>
                        {isActive && isRunning && (
                          <span style={{ marginLeft: 'auto', fontSize: '10px', color: 'var(--accent)' }}>running…</span>
                        )}
                      </div>
                    );
                  })}
                </div>

                {/* Live theme signals preview while running */}
                {reports.theme_candidates_json && reports.theme_candidates_json.length > 0 && (
                  <div style={{ padding: '0 16px 16px' }}>
                    <div style={{ fontSize: '11px', color: 'var(--muted)', marginBottom: '6px', textTransform: 'uppercase', letterSpacing: '0.06em' }}>
                      Theme Signals
                    </div>
                    <ThemeCandidatesPanel
                      candidates={reports.theme_candidates_json}
                      isStreaming={isRunning}
                    />
                  </div>
                )}

                <div className="metric-stack" style={{ marginTop: '8px' }}>
                  <div><span>Track</span><strong>{DISCOVERY_TRACKS.find(t => t.value === discoveryTrack)?.label}</strong></div>
                  <div><span>Scan Mode</span><strong>{SCAN_MODES.find(s => s.value === scanMode)?.label}</strong></div>
                  <div><span>Date</span><strong>{analysisDate}</strong></div>
                </div>
              </>
            )}

            {activeMode === 'analysis' && activeSessionType !== 'discovery' && (
              <>
                <div className="panel-header">
                  <div>
                    <h2>Session summary</h2>
                    <p>Current pipeline scope for this ticker.</p>
                  </div>
                </div>
                <div className="metric-stack">
                  <div><span>Analysts</span><strong>{ANALYST_SUMMARY_LABEL}</strong></div>
                  <div><span>Horizon</span><strong>{currentHorizon.label}</strong><small>{currentHorizon.detail}</small></div>
                  <div><span>Date</span><strong>{analysisDate}</strong><small>Analysis snapshot</small></div>
                  <div><span>Execution</span><strong>Paper disabled</strong><small>No orders will be submitted</small></div>
                </div>
              </>
            )}

            {activeMode === 'reports' && (
              <>
                <div className="panel-header">
                  <div>
                    <h2>Report</h2>
                    <p>Generated sections for the active ticker.</p>
                  </div>
                </div>

                {/* Theme Signals section — discovery sessions only */}
                {activeSessionType === 'discovery' && reports.theme_candidates_json && (
                  <div className="report-group" style={{ marginBottom: '8px' }}>
                    <h3 className="report-group-title">
                      <span className="report-group-icon">🔭</span>
                      Theme Signals
                    </h3>
                    <div className="report-group-content" style={{ padding: '12px 16px' }}>
                      <ThemeCandidatesPanel
                        candidates={reports.theme_candidates_json}
                        isStreaming={false}
                      />
                    </div>
                  </div>
                )}

                {availableReports.length === 0 && !reports.theme_candidates_json ? (
                  <div className="empty-state compact">
                    <h3>Reports pending</h3>
                    <p>Reports appear here as agents complete their work.</p>
                  </div>
                ) : availableReports.length > 0 ? (
                  <div className="report-accordion-container">
                    {REPORT_GROUPS.map(group => {
                      const activeGroupSections = group.sections
                        .map(key => {
                          const sectionMeta = REPORT_SECTIONS.find(s => s[0] === key);
                          return {
                            key,
                            label: sectionMeta ? sectionMeta[1] : key,
                            data: reports[key]
                          };
                        })
                        .filter(s => s.data);

                      if (activeGroupSections.length === 0) return null;

                      return (
                        <div key={group.id} className="report-group">
                          <h3 className="report-group-title">
                            <span className="report-group-icon">{group.icon}</span>
                            {group.label}
                          </h3>
                          <div className="report-group-content">
                            {activeGroupSections.map(section => {
                              const isExpanded = isReportSectionExpanded(section.key, expandedSections);
                              return (
                                <ReportSection
                                  key={section.key}
                                  sectionKey={section.key}
                                  label={section.label}
                                  data={section.data}
                                  isExpanded={isExpanded}
                                  onToggle={toggleSection}
                                  allReports={reports}
                                />
                              );
                            })}
                          </div>
                        </div>
                      );
                    })}
                  </div>
                ) : null}
              </>
            )}

            {activeMode === 'execution' && (
              <>
                <div className="panel-header">
                  <div>
                    <h2>Execution guardrails</h2>
                    <p>Trading controls stay visible without enabling live orders.</p>
                  </div>
                </div>
                <div className="execution-card">
                  <strong>Paper execution is off</strong>
                  <p>The UI sends execution.enabled=false. Enablement should require an explicit backend permission check before any broker action.</p>
                </div>
              </>
            )}
          </section>
        </div>
          </>
        ) : (
          <div className="welcome-container">
            <div className="welcome-hero">
              <div style={{ display: 'flex', justifyContent: 'center', marginBottom: '32px' }}>
                <div className="segmented-modes" style={{ display: 'flex', width: '380px', padding: '6px', borderRadius: '24px', background: 'var(--surface-strong)', boxShadow: 'var(--shadow)' }}>
                  <button 
                    className={`segmented-item ${mainPageMode === 'single' ? 'active' : ''}`} 
                    onClick={() => setMainPageMode('single')} 
                    style={{ padding: '12px', fontSize: '15px', borderRadius: '18px' }}
                  >
                    Single Ticker
                  </button>
                  <button 
                    className={`segmented-item ${mainPageMode === 'discovery' ? 'active' : ''}`} 
                    onClick={() => setMainPageMode('discovery')} 
                    style={{ padding: '12px', fontSize: '15px', borderRadius: '18px' }}
                  >
                    Stock Discovery
                  </button>
                </div>
              </div>
              <h2 style={{ textAlign: 'center' }}><span className="greeting-gradient">Hi Trader</span></h2>
              <h1 style={{ textAlign: 'center' }}>Where should we start?</h1>
            </div>

            <section className="composer-wrapper large">
              {renderComposer(true)}
              {renderConfigStrip()}

              {mainPageMode === 'single' && (
                <div className="gemini-suggestions">
                  <p className="suggestions-label">Try asking</p>
                  {[
                    ['Analyze NVDA', 'NVDA', 'short_term', '📈'],
                    ['Swing trade TSLA', 'TSLA', 'swing', '🚗'],
                    ['Long term SPY', 'SPY', 'long_term', '🏦'],
                    ['Research AAPL', 'AAPL', 'short_term', '🍎'],
                  ].map(([label, symbol, horizon, icon]) => (
                    <button key={`${symbol}-${horizon}`} className="suggestion-row" onClick={() => startAnalysis({ ticker: symbol, timeHorizon: horizon })}>
                      <span className="suggestion-row-icon">{icon}</span>
                      <span className="suggestion-row-text">{label}</span>
                      <svg className="suggestion-row-arrow" viewBox="0 0 24 24" width="14" height="14" fill="currentColor"><path d="M8.59 16.59L13.17 12 8.59 7.41 10 6l6 6-6 6-1.41-1.41z"/></svg>
                    </button>
                  ))}
                </div>
              )}
            </section>
          </div>
        )}
      </main>
    </div>
  );
}

export default App;
