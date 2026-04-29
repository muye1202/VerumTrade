import { useEffect, useRef, useState, useTransition } from 'react';

const API_BASE = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000';
const WS_BASE = API_BASE.replace(/^http/, 'ws');

const HORIZONS = [
  { value: 'short_term', label: 'Short term', detail: 'Days to weeks' },
  { value: 'swing', label: 'Swing', detail: 'Weeks to months' },
  { value: 'long_term', label: 'Long term', detail: 'Months to years' },
];

const MODES = [
  { id: 'analysis', label: 'Analysis', description: 'Run the agent pipeline' },
  { id: 'reports', label: 'Reports', description: 'Read generated findings' },
  { id: 'execution', label: 'Execution', description: 'Paper-trade controls' },
];

const REPORT_SECTIONS = [
  ['market_report', 'Market'],
  ['sentiment_report', 'Sentiment'],
  ['news_report', 'News'],
  ['fundamentals_report', 'Fundamentals'],
  ['trader_investment_plan', 'Trader Plan'],
  ['final_trade_decision', 'Final Decision'],
];

const makeLog = (type, content) => ({
  id: `${Date.now()}-${Math.random()}`,
  type,
  content,
});

const formatDateTime = (value) => {
  if (!value) return '';
  return new Intl.DateTimeFormat(undefined, {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  }).format(new Date(value));
};

const CustomSelect = ({ value, onChange, options, disabled, icon }) => {
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
        <span className="select-label">{selectedOption.label}</span>
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

  useEffect(() => {
    if (value) setViewDate(new Date(value + 'T00:00:00'));
  }, [value]);

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
        onClick={() => !disabled && setIsOpen(!isOpen)} 
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

function App() {
  const [theme, setTheme] = useState(() => localStorage.getItem('theme') || 'dark');
  const [ticker, setTicker] = useState('');
  const [analysisDate, setAnalysisDate] = useState(() => new Date().toISOString().split('T')[0]);
  const [timeHorizon, setTimeHorizon] = useState('short_term');
  const [activeMode, setActiveMode] = useState('analysis');
  const [activeReport, setActiveReport] = useState('market_report');
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
  const currentHorizon = HORIZONS.find((item) => item.value === timeHorizon) || HORIZONS[0];
  const availableReports = REPORT_SECTIONS.filter(([key]) => reports[key]);
  const selectedReportKey = availableReports.some(([key]) => key === activeReport)
    ? activeReport
    : availableReports[0]?.[0];

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

  const createPayload = (overrides = {}) => ({
    ticker: (overrides.ticker ?? ticker).trim().toUpperCase(),
    analysis_date: overrides.analysisDate ?? analysisDate,
    analysts: ['market', 'social', 'news', 'fundamentals'],
    research_depth: 1,
    llm_provider: 'openai',
    shallow_thinker: 'gpt-4o-mini',
    deep_thinker: 'gpt-4o-mini',
    time_horizon: overrides.timeHorizon ?? timeHorizon,
    skip_completed_analysts: false,
    mock: true,
    execution: {
      enabled: false,
      provider: 'alpaca',
      paper: true,
      position_size_pct: 0.1,
    },
  });

  const startAnalysis = (overrides = {}) => {
    const payload = createPayload(overrides);
    if (!payload.ticker || isRunning) return;

    stopSocket();
    setTicker(payload.ticker);
    setAnalysisDate(payload.analysis_date);
    setTimeHorizon(payload.time_horizon);
    setActiveSessionId(null);
    setActiveMode('analysis');
    setActiveReport('market_report');
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
              setLogs((prev) => [
                ...prev,
                makeLog(update.type?.toLowerCase() || 'agent', update.content),
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
    setActiveReport('market_report');
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
        setTimeHorizon(data.time_horizon);
        setLogs((Array.isArray(data.logs) ? data.logs : []).map((log, index) => ({
          ...log,
          id: `history-${data.id}-${index}`,
          type: log.type || 'agent',
        })));
        setReports(typeof data.reports === 'object' && data.reports ? data.reports : {});
        setActiveMode('reports');
        setActiveReport('market_report');
      });
    } catch (error) {
      setErrorMessage(error.message);
    }
  };

  const renderReportText = (value) => {
    if (!value) return null;
    if (typeof value === 'string') return value;
    if (value.judge_decision) return value.judge_decision;
    if (value.final_decision) return value.final_decision;
    return JSON.stringify(value, null, 2);
  };

  const renderComposer = (isWelcome = false) => (
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
      </div>
      <div className="gemini-toolbar">
        <div className="toolbar-left">
          <button className="icon-btn" title="Add file">
            <svg viewBox="0 0 24 24" fill="currentColor"><path d="M19 13h-6v6h-2v-6H5v-2h6V5h2v6h6v2z"/></svg>
          </button>
          <button className="text-btn">
            <svg viewBox="0 0 24 24" width="18" height="18" fill="currentColor"><path d="M22.7 19l-9.1-9.1c.9-2.3.4-5-1.5-6.9-2-2-5-2.4-7.4-1.3L9 6 6 9 1.6 4.7C.4 7.1.9 10.1 2.9 12.1c1.9 1.9 4.6 2.4 6.9 1.5l9.1 9.1c.4.4 1 .4 1.4 0l2.3-2.3c.5-.4.5-1.1.1-1.4z"/></svg>
            Tools
            <span className="dot"></span>
          </button>
        </div>
        <div className="toolbar-right">
          <CustomDatePicker
            value={analysisDate}
            onChange={(val) => setAnalysisDate(val)}
            disabled={isRunning}
          />
          <CustomSelect
            value={timeHorizon}
            onChange={(val) => setTimeHorizon(val)}
            options={HORIZONS}
            disabled={isRunning}
          />
          <button className="icon-btn" title="Microphone">
            <svg viewBox="0 0 24 24" fill="currentColor"><path d="M12 14c1.66 0 3-1.34 3-3V5c0-1.66-1.34-3-3-3S9 3.34 9 5v6c0 1.66 1.34 3 3 3zm5-3c0 2.76-2.24 5-5 5s-5-2.24-5-5H5c0 3.53 2.61 6.43 6 6.92V21h2v-3.08c3.39-.49 6-3.39 6-6.92h-2z"/></svg>
          </button>
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
          <nav className="mode-list" aria-label="Functionality">
            {MODES.map((mode) => (
              <button
                key={mode.id}
                className={activeMode === mode.id ? 'mode-item active' : 'mode-item'}
                onClick={() => setActiveMode(mode.id)}
              >
                <span>{mode.label}</span>
                <small>{mode.description}</small>
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
                <button
                  key={item.id}
                  className={activeSessionId === item.id ? 'history-item active' : 'history-item'}
                  onClick={() => loadHistoryItem(item.id)}
                >
                  <span>{item.ticker}</span>
                  <small>{item.time_horizon.replaceAll('_', ' ')} · {formatDateTime(item.created_at)}</small>
                </button>
              ))
            )}
          </div>
        </section>

        <button className="theme-button" onClick={() => setTheme((prev) => (prev === 'dark' ? 'light' : 'dark'))}>
          {theme === 'dark' ? 'Light mode' : 'Dark mode'}
        </button>
      </aside>

      <main className="workspace">
        {hasConversation ? (
          <>
            <header className="workspace-header">
              <div>
                <p className="eyebrow">TradingAgents workspace</p>
                <h1>{ticker || 'Session'} analysis</h1>
              </div>
              <div className={isRunning ? 'run-status active' : 'run-status'}>
                <span />
                {isRunning ? 'Agents running' : isPending ? 'Loading session' : 'Idle'}
              </div>
            </header>

            {errorMessage && <div className="error-banner">{errorMessage}</div>}

            <section className="composer-wrapper compact">
              {renderComposer(false)}
            </section>

            <div className="content-grid">
          <section className="conversation-panel">
            <div className="panel-header">
              <div>
                <h2>Agent transcript</h2>
                <p>Live stream from analyst, research, trader, and risk agents.</p>
              </div>
            </div>
            <div className="message-list">
              {logs.length === 0 ? (
                <div className="empty-state">
                  <h3>No active thread</h3>
                  <p>Choose a ticker or open a previous analysis from history.</p>
                </div>
              ) : (
                logs.map((log) => (
                  <article key={log.id} className={`message ${log.type}`}>
                    <div className="avatar">{log.type === 'user' ? 'You' : log.type.slice(0, 2).toUpperCase()}</div>
                    <p>{log.content}</p>
                  </article>
                ))
              )}
              <div ref={logsEndRef} />
            </div>
          </section>

          <section className="inspector-panel">
            {activeMode === 'analysis' && (
              <>
                <div className="panel-header">
                  <div>
                    <h2>Run setup</h2>
                    <p>Current settings sent to the analysis pipeline.</p>
                  </div>
                </div>
                <div className="metric-stack">
                  <div><span>Analysts</span><strong>Market, Social, News, Fundamentals</strong></div>
                  <div><span>Horizon</span><strong>{currentHorizon.label}</strong><small>{currentHorizon.detail}</small></div>
                  <div><span>Mode</span><strong>Mock stream</strong><small>UI-safe test execution</small></div>
                  <div><span>Execution</span><strong>Paper disabled</strong><small>No orders will be submitted</small></div>
                </div>
              </>
            )}

            {activeMode === 'reports' && (
              <>
                <div className="panel-header">
                  <div>
                    <h2>Executive report</h2>
                    <p>Switch between generated report sections.</p>
                  </div>
                </div>
                {availableReports.length === 0 ? (
                  <div className="empty-state compact">
                    <h3>Reports pending</h3>
                    <p>Reports appear here as agents complete their work.</p>
                  </div>
                ) : (
                  <>
                    <div className="report-tabs">
                      {availableReports.map(([key, label]) => (
                        <button
                          key={key}
                          className={selectedReportKey === key ? 'active' : ''}
                          onClick={() => setActiveReport(key)}
                        >
                          {label}
                        </button>
                      ))}
                    </div>
                    <pre className="report-body">{renderReportText(reports[selectedReportKey])}</pre>
                  </>
                )}
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
              <h2><span className="greeting-gradient">Hi Trader</span></h2>
              <h1>Where should we start?</h1>
            </div>

            <section className="composer-wrapper large">
              {renderComposer(true)}

              <div className="gemini-suggestions">
                {[
                  ['Analyze NVDA', 'NVDA', 'short_term', '📈', '#4285f4'],
                  ['Swing trade TSLA', 'TSLA', 'swing', '🚗', '#d96570'],
                  ['Long term SPY', 'SPY', 'long_term', '🏦', '#f4b400'],
                  ['Research AAPL', 'AAPL', 'short_term', '🍎', '#9b72cb'],
                ].map(([label, symbol, horizon, icon, color]) => (
                  <button key={`${symbol}-${horizon}`} className="suggestion-pill" onClick={() => startAnalysis({ ticker: symbol, timeHorizon: horizon })}>
                    <span className="suggestion-icon" style={{ color }}>{icon}</span> {label}
                  </button>
                ))}
              </div>
            </section>
          </div>
        )}
      </main>
    </div>
  );
}

export default App;
