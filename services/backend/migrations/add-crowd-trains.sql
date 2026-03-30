CREATE TABLE IF NOT EXISTS crowd_density (
    id SERIAL PRIMARY KEY,
    zone_id TEXT NOT NULL,              -- Platform zone identifier
    density_percent INTEGER NOT NULL,    -- 0-100 percentage
    timestamp TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Index for fast zone lookups
CREATE INDEX IF NOT EXISTS idx_crowd_zone_time 
ON crowd_density(zone_id, timestamp DESC);

-- Table 2: Train Status
-- Stores real-time train tracking information
CREATE TABLE IF NOT EXISTS train_status (
    id SERIAL PRIMARY KEY,
    train_number TEXT NOT NULL,
    train_name TEXT NOT NULL,
    route TEXT NOT NULL,
    current_station TEXT,
    next_station TEXT,
    eta_minutes INTEGER,
    delay_minutes INTEGER DEFAULT 0,
    kavach_status TEXT DEFAULT 'ACTIVE',  -- ACTIVE, DEGRADED, OFFLINE
    timestamp TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Index for fast train number lookups
CREATE INDEX IF NOT EXISTS idx_train_number_time 
ON train_status(train_number, timestamp DESC);
```