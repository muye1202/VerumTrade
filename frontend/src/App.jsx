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
          <input
            type="date"
            className="gemini-date"
            value={analysisDate}
            onChange={(event) => setAnalysisDate(event.target.value)}
            disabled={isRunning}
          />
          <select
            className="gemini-select"
            value={timeHorizon}
            onChange={(event) => setTimeHorizon(event.target.value)}
            disabled={isRunning}
          >
            {HORIZONS.map((horizon) => (
              <option key={horizon.value} value={horizon.value}>{horizon.label}</option>
            ))}
          </select>
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
          <span className="brand-mark">BT</span>
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
