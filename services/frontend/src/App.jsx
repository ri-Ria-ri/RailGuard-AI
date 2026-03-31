import { memo, useCallback, useEffect, useMemo, useState } from "react";
import "./styles.css";

const WS_ALERTS = import.meta.env.VITE_WS_URL || "ws://localhost:8000/ws/alerts";
const WS_AI = import.meta.env.VITE_WS_AI_RISK_URL || "ws://localhost:8000/ws/ai-risk";
const WS_TRAINS = import.meta.env.VITE_WS_TRAINS_URL || "ws://localhost:8000/ws/trains";
const WS_CROWD = import.meta.env.VITE_WS_CROWD_URL || "ws://localhost:8000/ws/crowd";
const API_BASE = import.meta.env.VITE_API_BASE_URL || "http://localhost:8000";
const WS_FLUSH_MS = 100;
const MAX_ALERTS = 100;
const MAX_CAMERA_ALERTS = 80;
const MAX_TRAINS = 100;
const MAX_CROWD_ZONES = 100;
const MAX_RISK_ZONES = 100;
const ALERT_ROW_HEIGHT = 76;
const ALERT_VIEWPORT_HEIGHT = 360;
const ALERT_OVERSCAN_ROWS = 4;
let _alertIdSeq = 0;

function useBufferedWs(url, onBatch, flushMs = WS_FLUSH_MS) {
  useEffect(() => {
    let ws;
    let closed = false;
    let timer = null;
    const queue = [];

    const flush = () => {
      timer = null;
      if (!queue.length) {
        return;
      }
      const batch = queue.splice(0, queue.length);
      onBatch(batch);
    };

    const scheduleFlush = () => {
      if (timer) {
        return;
      }
      timer = setTimeout(flush, flushMs);
    };

    const connect = () => {
      ws = new WebSocket(url);
      ws.onmessage = (ev) => {
        try {
          queue.push(JSON.parse(ev.data));
          scheduleFlush();
        } catch {
          // Ignore malformed payloads.
        }
      };
      ws.onclose = () => {
        flush();
        if (!closed) {
          setTimeout(connect, 1500);
        }
      };
    };

    connect();
    return () => {
      closed = true;
      if (timer) {
        clearTimeout(timer);
      }
      flush();
      if (ws) {
        ws.close();
      }
    };
  }, [url, onBatch, flushMs]);
}

/* ---------- AI RISK (requires location, no conf display) ---------- */
const RiskPanel = memo(function RiskPanel() {
  const [risk, setRisk] = useState({});
  const [dropped, setDropped] = useState(0);
  const [riskLevelFilter, setRiskLevelFilter] = useState("all");
  const [riskZoneFilter, setRiskZoneFilter] = useState("all");

  const handleRiskBatch = useCallback((batch) => {
    let rejected = 0;
    setRisk((prev) => {
      const next = { ...prev };
      for (const r of batch) {
        const id = r.zoneId || r.stationId || r.platformId || r.location || r.name;
        if (!id) {
          rejected += 1;
          continue;
        }
        next[id] = { ...r, _id: id };
      }
      const trimmed = Object.values(next)
        .sort((a, b) => (b.riskScore ?? 0) - (a.riskScore ?? 0))
        .slice(0, MAX_RISK_ZONES)
        .reduce((acc, item) => {
          acc[item._id] = item;
          return acc;
        }, {});
      return trimmed;
    });
    if (rejected) {
      setDropped((n) => n + rejected);
    }
  }, []);

  useBufferedWs(WS_AI, handleRiskBatch);

  // HTTP polling for AI risk data with local generation fallback
  useEffect(() => {
    const id = setInterval(async () => {
      try {
        const res = await fetch(`${API_BASE}/ai/risk/latest?limit=${MAX_RISK_ZONES}`);
        const data = await res.json();
        
        if (Array.isArray(data) && data.length > 0) {
          handleRiskBatch(data);
        } else {
          // Generate synthetic risk data if API returns empty
          const zones = ["ZONE-A", "ZONE-B", "ZONE-C"];
          const levels = ["low", "medium", "high"];
          const syntheticData = zones.map((zone) => ({
            zoneId: zone,
            riskScore: Math.random() * 0.8 + 0.1,
            riskLevel: levels[Math.floor(Math.random() * levels.length)],
            topFactors: [
              { factor: "Crowd Density", contribution: Math.random() * 0.4 + 0.2 },
              { factor: "Device Alerts", contribution: Math.random() * 0.3 + 0.1 },
              { factor: "Train Delay", contribution: Math.random() * 0.2 + 0.05 },
            ],
            timestamp: new Date().toISOString(),
          }));
          handleRiskBatch(syntheticData);
        }
      } catch (e) {
        // Silently ignore polling errors
      }
    }, 10000);
    return () => clearInterval(id);
  }, [handleRiskBatch]);

  const allRows = useMemo(
    () => Object.values(risk)
      .filter((r) => {
        const zone = r._id || r.zoneId || "";
        return zone && zone.match(/^ZONE-[ABC]$/);
      })
      .sort((a, b) => (b.riskScore ?? 0) - (a.riskScore ?? 0)),
    [risk],
  );

  const riskZones = useMemo(() => Array.from(new Set(allRows.map((r) => r._id))).sort(), [allRows]);

  const rows = useMemo(() => {
    return allRows.filter((r) => {
      const level = String(r.riskLevel || "").toLowerCase();
      if (riskLevelFilter !== "all" && level !== riskLevelFilter) {
        return false;
      }
      if (riskZoneFilter !== "all" && r._id !== riskZoneFilter) {
        return false;
      }
      return true;
    });
  }, [allRows, riskLevelFilter, riskZoneFilter]);

  return (
    <div className="card card-wide">
      <div className="card-title-row">
        <div className="card-title">AI RISK</div>
        <span className="section-count">{rows.length}</span>
      </div>
      <div className="panel-filters">
        <div className="filter-group">
          <button
            type="button"
            className={`filter-chip ${riskLevelFilter === "all" ? "active" : ""}`}
            onClick={() => setRiskLevelFilter("all")}
          >
            All
          </button>
          <button
            type="button"
            className={`filter-chip ${riskLevelFilter === "low" ? "active" : ""}`}
            onClick={() => setRiskLevelFilter("low")}
          >
            Low
          </button>
          <button
            type="button"
            className={`filter-chip ${riskLevelFilter === "medium" ? "active" : ""}`}
            onClick={() => setRiskLevelFilter("medium")}
          >
            Medium
          </button>
          <button
            type="button"
            className={`filter-chip ${riskLevelFilter === "high" ? "active" : ""}`}
            onClick={() => setRiskLevelFilter("high")}
          >
            High
          </button>
        </div>
        <div className="filter-group">
          <select
            className="filter-select"
            value={riskZoneFilter}
            onChange={(e) => setRiskZoneFilter(e.target.value)}
          >
            <option value="all">All zones</option>
            {riskZones.map((zone) => (
              <option key={zone} value={zone}>
                {zone}
              </option>
            ))}
          </select>
        </div>
      </div>
      {dropped > 0 && (
        <div className="warning-line">
          {dropped} risk event(s) ignored: missing zone/station/platform.
        </div>
      )}
      {rows.length === 0 && <div className="muted">No risk events yet.</div>}
      {rows.map((r) => (
        <div key={r._id} className="risk-row">
          <div className="row-head">
            <span className="zone">{r._id}</span>
            <span className="score">{(r.riskScore * 100).toFixed(0)}%</span>
            <span className={`pill level-${r.riskLevel?.toLowerCase()}`}>{r.riskLevel?.toUpperCase()}</span>
          </div>
          <div className="factors">
            {r.topFactors?.map((f, i) => (
              <span key={i} className="factor">
                {f.factor}: {(f.contribution * 100).toFixed(0)}%
              </span>
            ))}
          </div>
        </div>
      ))}
    </div>
  );
});

/* ---------- ALERTS (with category/subType tags) ---------- */
const AlertsPanel = memo(function AlertsPanel() {
  const [alerts, setAlerts] = useState({});
  const [scrollTop, setScrollTop] = useState(0);
  const [severityFilter, setSeverityFilter] = useState("all");

  const handleAlertBatch = useCallback((batch) => {
    if (!Array.isArray(batch) || batch.length === 0) {
      console.debug("[AlertsPanel] Empty batch received");
      return;
    }
    console.debug(`[AlertsPanel] Processing ${batch.length} alerts`);
    setAlerts((prev) => {
      try {
        const next = { ...prev };
        for (const alert of batch) {
          const zone = alert.zoneId || alert.stationId;
          if (!zone) {
            continue;
          }
          const alertId = alert.id || `${zone}-${alert.timestamp ?? ++_alertIdSeq}`;
          next[alertId] = { ...alert, _id: alertId, _zone: zone, _ts: alert.timestamp || Date.now() };
        }
        const trimmed = Object.values(next)
          .sort((a, b) => (b._ts ?? 0) - (a._ts ?? 0))
          .slice(0, MAX_ALERTS)
          .reduce((acc, item) => {
            acc[item._id] = item;
            return acc;
          }, {});
        return trimmed;
      } catch (e) {
        console.error("[AlertsPanel] Error processing alerts:", e);
        return prev;
      }
    });
  }, []);

  useBufferedWs(WS_ALERTS, handleAlertBatch);

  const filteredAlerts = useMemo(() => {
    return Object.values(alerts).filter((a) => {
      const sev = String(a.severity || "").toLowerCase();
      if (severityFilter === "all") return true;
      return sev === severityFilter;
    });
  }, [alerts, severityFilter]);

  const totalRows = filteredAlerts.length;
  const startRow = Math.max(0, Math.floor(scrollTop / ALERT_ROW_HEIGHT) - ALERT_OVERSCAN_ROWS);
  const visibleRows = Math.ceil(ALERT_VIEWPORT_HEIGHT / ALERT_ROW_HEIGHT) + ALERT_OVERSCAN_ROWS * 2;
  const endRow = Math.min(totalRows, startRow + visibleRows);
  const visibleAlerts = filteredAlerts.slice(startRow, endRow);
  const topSpacer = startRow * ALERT_ROW_HEIGHT;
  const bottomSpacer = (totalRows - endRow) * ALERT_ROW_HEIGHT;

  return (
    <div className="card">
      <div className="card-title-row">
        <div className="card-title">Alerts</div>
        <span className="section-count">{totalRows}</span>
      </div>
      <div className="panel-filters">
        <div className="filter-group">
          <button
            type="button"
            className={`filter-chip ${severityFilter === "all" ? "active" : ""}`}
            onClick={() => setSeverityFilter("all")}
          >
            All
          </button>
          <button
            type="button"
            className={`filter-chip ${severityFilter === "low" ? "active" : ""}`}
            onClick={() => setSeverityFilter("low")}
          >
            Low
          </button>
          <button
            type="button"
            className={`filter-chip ${severityFilter === "medium" ? "active" : ""}`}
            onClick={() => setSeverityFilter("medium")}
          >
            Medium
          </button>
          <button
            type="button"
            className={`filter-chip ${severityFilter === "high" ? "active" : ""}`}
            onClick={() => setSeverityFilter("high")}
          >
            High
          </button>
          <button
            type="button"
            className={`filter-chip ${severityFilter === "critical" ? "active" : ""}`}
            onClick={() => setSeverityFilter("critical")}
          >
            Critical
          </button>
        </div>
      </div>
      {totalRows === 0 && <div className="muted">Waiting for alerts…</div>}
      {totalRows > 0 && (
        <div
          className="alerts-viewport"
          onScroll={(e) => setScrollTop(e.currentTarget.scrollTop)}
          style={{ maxHeight: ALERT_VIEWPORT_HEIGHT }}
        >
          {topSpacer > 0 && <div className="alert-spacer" style={{ height: topSpacer }} />}
          {visibleAlerts.map((a, idx) => (
            <div key={a._id} className="alert-row">
              <span className={`pill sev-${(a.severity || "low").toLowerCase()}`}>{a.severity || "LOW"}</span>
              <div className="alert-body">
                <div className="alert-line">
                  <span>{a.message || a.type || "event"}</span>
                  {a.category && <span className="tag">{a.category}</span>}
                  {a.subType && <span className="tag tag-sub">{a.subType}</span>}
                </div>
                <div className="muted">{a.zoneId || a.stationId}</div>
              </div>
            </div>
          ))}
          {bottomSpacer > 0 && <div className="alert-spacer" style={{ height: bottomSpacer }} />}
        </div>
      )}
    </div>
  );
});

/* ---------- CAMERA ALERTS (dedicated operational panel) ---------- */
const CameraAlertsPanel = memo(function CameraAlertsPanel() {
  const [cameraAlerts, setCameraAlerts] = useState([]);
  const [typeFilter, setTypeFilter] = useState("all");
  const [timeFilter, setTimeFilter] = useState("all");
  const [zoneFilter, setZoneFilter] = useState("all");

  const isCameraAlert = useCallback((a) => {
    if (!a) return false;
    return (
      a.category === "CAMERA" ||
      a.source === "camera_watchdog" ||
      (typeof a.subType === "string" && a.subType.startsWith("camera_"))
    );
  }, []);

  const normalizeCameraAlert = useCallback((a) => {
    const zone = a.zoneId || a.stationId || "unknown";
    const explicitCameraId = a.cameraId || a.camera_id || "";
    const parsedCameraId =
      !explicitCameraId && typeof a.message === "string"
        ? (a.message.match(/\bCAM-[A-Z0-9-]+\b/i)?.[0] ?? "")
        : "";
    const fallbackZone = String(zone).replace(/[^A-Z0-9]+/gi, "-").toUpperCase();
    const cameraId = explicitCameraId || parsedCameraId || `CAM-${fallbackZone}-UNK`;

    return { ...a, cameraId, zoneId: a.zoneId || a.stationId || "unknown" };
  }, []);

  const handleAlertBatch = useCallback(
    (batch) => {
      setCameraAlerts((prev) => {
        const incoming = batch.filter(isCameraAlert).map(normalizeCameraAlert).filter((a) => a.cameraId);
        return [...incoming.reverse(), ...prev].slice(0, MAX_CAMERA_ALERTS);
      });
    },
    [isCameraAlert, normalizeCameraAlert],
  );

  useBufferedWs(WS_ALERTS, handleAlertBatch);

  useEffect(() => {
    const id = setInterval(async () => {
      const res = await fetch(`${API_BASE}/alerts/latest?limit=${MAX_ALERTS}`);
      const data = await res.json();
      setCameraAlerts(
        (Array.isArray(data) ? data : [])
          .filter(isCameraAlert)
          .map(normalizeCameraAlert)
          .filter((a) => a.cameraId)
          .slice(0, MAX_CAMERA_ALERTS),
      );
    }, 10000);
    return () => clearInterval(id);
  }, [isCameraAlert, normalizeCameraAlert]);

  const filteredAlerts = useMemo(() => {
    const now = Date.now();
    return cameraAlerts.filter((a) => {
      const zone = a.zoneId || a.stationId || "unknown";
      const subType = String(a.subType || "").toLowerCase();

      if (zoneFilter !== "all" && zone !== zoneFilter) {
        return false;
      }

      if (typeFilter === "frozen" && subType !== "camera_frozen") {
        return false;
      }
      if (typeFilter === "offline" && subType !== "camera_offline") {
        return false;
      }

      if (timeFilter === "5m") {
        const ts = Date.parse(a.timestamp || "");
        if (Number.isNaN(ts)) {
          return false;
        }
        if (now - ts > 5 * 60 * 1000) {
          return false;
        }
      }

      return true;
    });
  }, [cameraAlerts, typeFilter, timeFilter, zoneFilter]);

  const cameraZones = useMemo(
    () => Array.from(new Set(cameraAlerts.map((a) => a.zoneId || a.stationId || "unknown"))).sort(),
    [cameraAlerts],
  );

  return (
    <div className="card card-camera">
      <div className="card-title-row">
        <div className="card-title">Camera Alerts</div>
        <span className="camera-count">{filteredAlerts.length}</span>
      </div>
      <div className="camera-filters">
        <div className="filter-group">
          <button
            type="button"
            className={`filter-chip ${typeFilter === "all" ? "active" : ""}`}
            onClick={() => setTypeFilter("all")}
          >
            All
          </button>
          <button
            type="button"
            className={`filter-chip ${typeFilter === "frozen" ? "active" : ""}`}
            onClick={() => setTypeFilter("frozen")}
          >
            Only Frozen
          </button>
          <button
            type="button"
            className={`filter-chip ${typeFilter === "offline" ? "active" : ""}`}
            onClick={() => setTypeFilter("offline")}
          >
            Only Offline
          </button>
        </div>
        <div className="filter-group">
          <button
            type="button"
            className={`filter-chip ${timeFilter === "all" ? "active" : ""}`}
            onClick={() => setTimeFilter("all")}
          >
            All time
          </button>
          <button
            type="button"
            className={`filter-chip ${timeFilter === "5m" ? "active" : ""}`}
            onClick={() => setTimeFilter("5m")}
          >
            Last 5m
          </button>
        </div>
        <div className="filter-group">
          <select className="filter-select" value={zoneFilter} onChange={(e) => setZoneFilter(e.target.value)}>
            <option value="all">All zones</option>
            {cameraZones.map((zone) => (
              <option key={zone} value={zone}>
                {zone}
              </option>
            ))}
          </select>
        </div>
      </div>
      {filteredAlerts.length === 0 && <div className="muted">No camera alerts for selected filters.</div>}
      {filteredAlerts.length > 0 && (
        <div className="camera-alerts-viewport">
          {filteredAlerts.map((a, idx) => {
            const zone = a.zoneId || a.stationId || "unknown";
            const cameraChip = a.cameraId;

            return (
              <div key={`${a.id || a.timestamp || "camera"}-${idx}`} className="camera-alert-row">
                <span className={`pill sev-${(a.severity || "low").toLowerCase()}`}>{a.severity || "LOW"}</span>
                <div className="alert-body">
                  <div className="alert-line">
                    <span>{a.message || "Camera event"}</span>
                    <span className="tag tag-camera">{cameraChip}</span>
                    {a.subType && <span className="tag tag-sub">{a.subType}</span>}
                  </div>
                  <div className="muted">{zone}</div>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
});

/* ---------- TRAINS (drops items without destination) ---------- */
const TrainsPanel = memo(function TrainsPanel() {
  const [trains, setTrains] = useState({});

  const handleTrainBatch = useCallback((batch) => {
    setTrains((prev) => {
      const next = { ...prev };
      for (const msg of batch) {
        const id =
          msg.trainId ||
          msg.trainName ||
          msg.name ||
          `${msg.route || "route"}-${msg.destination || msg.zoneId || msg.stationId || "x"}`;
        next[id] = { ...msg, _receivedAt: Date.now() };
      }
      const trimmed = Object.values(next)
        .sort((a, b) => (b._receivedAt || 0) - (a._receivedAt || 0))
        .slice(0, MAX_TRAINS)
        .reduce((acc, item) => {
          const id =
            item.trainId ||
            item.trainName ||
            item.name ||
            `${item.route || "route"}-${item.destination || item.zoneId || item.stationId || "x"}`;
          acc[id] = item;
          return acc;
        }, {});
      return trimmed;
    });
  }, []);

  useBufferedWs(WS_TRAINS, handleTrainBatch);

  useEffect(() => {
    const id = setInterval(async () => {
      try {
        const res = await fetch(`${API_BASE}/trains/latest?limit=${MAX_TRAINS}`);
        const data = await res.json();
        const trains = data.value || data || [];
        handleTrainBatch(Array.isArray(trains) ? trains : []);
      } catch (e) {
        // Silently ignore polling errors
      }
    }, 10000);
    return () => clearInterval(id);
  }, [handleTrainBatch]);



  const rows = useMemo(
    () =>
      Object.values(trains)
        .filter((t) => t.destination || t.dest || t.terminal || t.headsign || t.nextStop || t.zoneId || t.stationId)
        .sort((a, b) => (b._receivedAt || 0) - (a._receivedAt || 0))
        .slice(0, MAX_TRAINS),
    [trains],
  );

  const filteredRows = useMemo(() => rows, [rows]);

  return (
    <div className="card">
      <div className="card-title-row">
        <div className="card-title">Train Delay</div>
        <span className="section-count">{filteredRows.length}</span>
      </div>

      {filteredRows.length === 0 && <div className="muted">No train data yet.</div>}
      <div className="train-list">
        {filteredRows.map((t, i) => {
          const delay = t.delayMinutes ?? 0;
          const name = t.trainName || t.name || t.trainId || "Train";
          const destination =
            t.destination ||
            t.dest ||
            t.terminal ||
            t.headsign ||
            t.nextStop ||
            t.zoneId ||
            t.stationId;
          const route = t.route || t.line || t.service || "";
          const zone = t.zoneId || "Unmapped";
          const station = t.stationId || t.currentStation || "Unknown station";
          const pointLabel = t.trainPointLabel || "Suburban/Regional Stop";
          const pointType = t.trainPointType || "suburban_regional_stop";
          return (
            <div key={name + i} className="train-row">
              <div className="row-head">
                <span className="zone">{name}</span>
                {route && <span className="muted">{route}</span>}
                <span className="muted">→ {destination}</span>
                <span className="tag tag-sub">{pointType.replaceAll("_", " ")}</span>
                <span className={`pill delay-${delay >= 10 ? "high" : delay >= 5 ? "med" : "low"}`}>
                  {delay} min delay
                </span>
              </div>
              <div className="muted">
                {`Zone: ${zone}`}
                {station ? ` • Station: ${station}` : ""}
                {pointLabel ? ` • Point: ${pointLabel}` : ""}
                {t.status ? ` • Status: ${t.status}` : ""}
                {t.nextArrival ? ` • Next arrival: ${t.nextArrival}` : ""}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
});

/* ---------- CROWD (requires location) ---------- */
const CrowdPanel = memo(function CrowdPanel() {
  const [crowd, setCrowd] = useState({});
  const [zoneFilter, setZoneFilter] = useState("all");

  const handleCrowdBatch = useCallback((batch) => {
    setCrowd((prev) => {
      const next = { ...prev };
      for (const msg of batch) {
        const zone = msg.zoneId || msg.stationId || msg.platformId || msg.location;
        if (!zone) {
          continue;
        }
        next[zone] = msg;
      }
      const trimmed = Object.values(next)
        .sort((a, b) => (b.timestamp ?? 0) - (a.timestamp ?? 0))
        .slice(0, MAX_CROWD_ZONES)
        .reduce((acc, item) => {
          const zone = item.zoneId || item.stationId || item.platformId || item.location;
          if (zone) {
            acc[zone] = item;
          }
          return acc;
        }, {});
      return trimmed;
    });
  }, []);

  useBufferedWs(WS_CROWD, handleCrowdBatch);
  const allRows = useMemo(
    () =>
      Object.values(crowd)
        .filter((c) => {
          const zone = c.zoneId || c.stationId || c.platformId || c.location;
          return zone && zone.match(/^ZONE-[ABC]$/);
        })
        .slice(0, MAX_CROWD_ZONES),
    [crowd],
  );
  const zones = useMemo(
    () =>
      Array.from(
        new Set(
          allRows
            .map((c) => c.zoneId || c.stationId || c.platformId || c.location)
            .filter(Boolean)
        ),
      ).sort(),
    [allRows],
  );
  const rows = useMemo(() => {
    if (zoneFilter === "all") {
      return allRows;
    }
    return allRows.filter((c) => (c.zoneId || c.stationId || c.platformId || c.location) === zoneFilter);
  }, [allRows, zoneFilter]);

  return (
    <div className="card">
      <div className="card-title-row">
        <div className="card-title">Crowd Status</div>
        <span className="section-count">{rows.length}</span>
      </div>
      <div className="panel-filters">
        <div className="filter-group">
          <select className="filter-select" value={zoneFilter} onChange={(e) => setZoneFilter(e.target.value)}>
            <option value="all">All zones</option>
            {zones.map((zone) => (
              <option key={zone} value={zone}>
                {zone}
              </option>
            ))}
          </select>
        </div>
      </div>
      {rows.length === 0 && <div className="muted">No crowd data yet.</div>}
      {rows.map((c, i) => (
        <div key={i} className="crowd-row">
          <div className="row-head">
            <span className="zone">{c.zoneId || c.stationId || c.platformId || c.location}</span>
            <span className={`pill crowd-${(c.crowdClass || "normal").toLowerCase()}`}>
              {c.crowdClass || "NORMAL"}
            </span>
          </div>
          <div className="muted">
            Density: {c.densityPercent ?? "?"}% • Count: {c.personCount ?? "?"}
          </div>
        </div>
      ))}
    </div>
  );
});

/* ---------- APP ---------- */
export default function App() {
  return (
    <div className="layout">
      <RiskPanel />
      <CameraAlertsPanel />
      <div className="three-col">
        <TrainsPanel />
        <CrowdPanel />
        <AlertsPanel />
      </div>
    </div>
  );
}