-- ═══════════════════════════════════════════════════════
-- OMNIX V3 — Supabase Schema
-- Run in: Supabase Dashboard → SQL Editor → New Query → Run
-- ═══════════════════════════════════════════════════════

CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- User Profiles
CREATE TABLE IF NOT EXISTS omnix_profiles (
  id            UUID DEFAULT uuid_generate_v4() PRIMARY KEY,
  user_id       UUID REFERENCES auth.users(id) ON DELETE CASCADE UNIQUE NOT NULL,
  name          TEXT,
  age           INTEGER,
  city          TEXT,
  role          TEXT DEFAULT 'student',
  context       TEXT,
  challenge     TEXT,
  tone          TEXT DEFAULT 'direct',
  sleep_hours   NUMERIC(4,1) DEFAULT 7.0,
  wakeup_time   TEXT DEFAULT '07:00',
  peak_time     TEXT DEFAULT 'morning',
  exercise      TEXT DEFAULT '3-4x',
  backend_url   TEXT DEFAULT 'http://localhost:8000',
  onboarded     BOOLEAN DEFAULT FALSE,
  created_at    TIMESTAMPTZ DEFAULT NOW(),
  updated_at    TIMESTAMPTZ DEFAULT NOW()
);

-- Generated Schedules
CREATE TABLE IF NOT EXISTS omnix_schedules (
  id            UUID DEFAULT uuid_generate_v4() PRIMARY KEY,
  user_id       UUID REFERENCES auth.users(id) ON DELETE CASCADE NOT NULL,
  user_input    TEXT NOT NULL,
  blocks        JSONB NOT NULL,
  provider      TEXT,
  latency_ms    INTEGER,
  block_count   INTEGER,
  created_at    TIMESTAMPTZ DEFAULT NOW(),
  schedule_date DATE DEFAULT CURRENT_DATE
);

-- Loop Runs
CREATE TABLE IF NOT EXISTS omnix_loop_runs (
  id                UUID DEFAULT uuid_generate_v4() PRIMARY KEY,
  user_id           UUID REFERENCES auth.users(id) ON DELETE CASCADE NOT NULL,
  status            TEXT DEFAULT 'COMPLETE',
  total_latency_ms  INTEGER,
  actions_executed  INTEGER DEFAULT 0,
  edge_count        INTEGER DEFAULT 0,
  cloud_count       INTEGER DEFAULT 0,
  planner_provider  TEXT,
  urgency           TEXT,
  sleep_score       INTEGER,
  stress_score      INTEGER,
  stress_level      TEXT,
  context_snapshot  JSONB,
  executed_actions  JSONB,
  stress_profile    JSONB,
  twin_simulations  JSONB,
  created_at        TIMESTAMPTZ DEFAULT NOW()
);

-- User Memory
CREATE TABLE IF NOT EXISTS omnix_memory (
  id                   UUID DEFAULT uuid_generate_v4() PRIMARY KEY,
  user_id              UUID REFERENCES auth.users(id) ON DELETE CASCADE UNIQUE NOT NULL,
  behavioral_patterns  JSONB DEFAULT '{}',
  academic_profile     JSONB DEFAULT '{}',
  action_history       JSONB DEFAULT '[]',
  personalization      JSONB DEFAULT '{}',
  loop_count           INTEGER DEFAULT 0,
  last_updated         TIMESTAMPTZ DEFAULT NOW(),
  created_at           TIMESTAMPTZ DEFAULT NOW()
);

-- Health Check-ins
CREATE TABLE IF NOT EXISTS omnix_health_checkins (
  id                UUID DEFAULT uuid_generate_v4() PRIMARY KEY,
  user_id           UUID REFERENCES auth.users(id) ON DELETE CASCADE NOT NULL,
  sleep_hours       NUMERIC(4,1),
  sleep_quality     INTEGER,
  energy_level      INTEGER,
  steps_today       INTEGER DEFAULT 0,
  mood              TEXT,
  breakfast_consumed BOOLEAN DEFAULT TRUE,
  hydration         TEXT DEFAULT 'OK',
  notes             TEXT,
  created_at        TIMESTAMPTZ DEFAULT NOW(),
  checkin_date      DATE DEFAULT CURRENT_DATE
);

-- RLS
ALTER TABLE omnix_profiles        ENABLE ROW LEVEL SECURITY;
ALTER TABLE omnix_schedules       ENABLE ROW LEVEL SECURITY;
ALTER TABLE omnix_loop_runs       ENABLE ROW LEVEL SECURITY;
ALTER TABLE omnix_memory          ENABLE ROW LEVEL SECURITY;
ALTER TABLE omnix_health_checkins ENABLE ROW LEVEL SECURITY;

CREATE POLICY "own_profile"   ON omnix_profiles        FOR ALL USING (auth.uid()=user_id) WITH CHECK (auth.uid()=user_id);
CREATE POLICY "own_schedules" ON omnix_schedules        FOR ALL USING (auth.uid()=user_id) WITH CHECK (auth.uid()=user_id);
CREATE POLICY "own_loops"     ON omnix_loop_runs        FOR ALL USING (auth.uid()=user_id) WITH CHECK (auth.uid()=user_id);
CREATE POLICY "own_memory"    ON omnix_memory           FOR ALL USING (auth.uid()=user_id) WITH CHECK (auth.uid()=user_id);
CREATE POLICY "own_checkins"  ON omnix_health_checkins  FOR ALL USING (auth.uid()=user_id) WITH CHECK (auth.uid()=user_id);

CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$ BEGIN NEW.updated_at=NOW(); RETURN NEW; END; $$ language 'plpgsql';

CREATE TRIGGER trg_profiles_updated_at BEFORE UPDATE ON omnix_profiles FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
CREATE TRIGGER trg_memory_updated_at   BEFORE UPDATE ON omnix_memory    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE INDEX IF NOT EXISTS idx_sched_user_date   ON omnix_schedules(user_id,schedule_date DESC);
CREATE INDEX IF NOT EXISTS idx_loops_user_date   ON omnix_loop_runs(user_id,created_at DESC);
CREATE INDEX IF NOT EXISTS idx_checkin_user_date ON omnix_health_checkins(user_id,checkin_date DESC);
