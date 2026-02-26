# PolyEdge Automator v2.5
Autonomous prediction-market edge research + execution system (Polymarket)

Version: 2.5
Date: 2026-02-25
Primary modes: ALERT, PAPER, LIVE
Default runway: PAPER for 30 days with pessimistic 2.0× simulated fees
Default paper bankroll reference: $100

============================================================
0) Goals, scope, and non-goals
============================================================

0.1 Goals
- Run 24/7 to:
  (a) maintain an up-to-date internal view of eligible Polymarket markets and live prices,
  (b) identify candidate edges using cheap filters first,
  (c) fetch deterministic evidence from an allowlist when required,
  (d) optionally consult a multi-LLM swarm within strict budget limits,
  (e) decide trades using explicit EV math and conservative friction buffers,
  (f) execute automatically in PAPER and LIVE with strict safety invariants,
  (g) produce fully replayable decision logs.

0.2 Scope (locked)
- Only categories: geopolitics, economics, tech/AI (allowlist).
- Sports excluded (denylist).
- Binary YES/NO markets only.
- Automated trading in PAPER and LIVE (no manual confirmations).
- Telegram is the primary control/alert channel.

0.3 Non-goals (explicit)
- No discretionary “LLM browsing”.
- No trading on REST-only pricing.
- No “market order” primitives (only limit orders; marketable limits are treated as taker-like and restricted).
- No multi-outcome markets, no parlay/portfolio optimisers.

============================================================
1) Glossary and fixed definitions
============================================================

- Market: a Polymarket condition/market that resolves to YES or NO.
- Token: YES token or NO token for a market.
- Snapshot: an immutable record of WS-derived best bid/ask + depth at time T, used for decision/execution.
- Candidate: a queued unit of evaluation derived from a trigger + snapshot.
- Decision: deterministic record of math + gates producing either NO_TRADE or an executable instruction.
- Exposure: total open notional at risk (conservative mark).
- BARRIER: in-memory “hard stop” flag set by /halt; when true, system must not create new exposure.
- RECONCILE: process that compares local state vs venue state to prevent ghost orders.
- PENDING_UNKNOWN: a local order state meaning “we attempted a submit/cancel and do not yet know what happened”.

All time references are UTC unless stated.

============================================================
2) Hard invariants (must never be violated)
============================================================

I1 Fail-closed
If any critical input is missing, stale, contradictory, suspicious, or cannot be durably logged → NO_TRADE and downgrade state as defined.

I2 Cannot silently re-enable LIVE
A restart, deploy, crash, or config edit must never result in LIVE_TRADING. LIVE requires a fresh, explicit arming ceremony per process lifetime.

I3 Replayability
Every decision and order must be reconstructible from stored inputs and hashes:
- snapshot (WS-only for trading),
- evidence bundle (canonical + hash),
- AI raw outputs + parsed JSON + schema version + prompt hashes (if used),
- decision math inputs (prices, buffers, w_ai, EV, gates),
- WAL/DB events for any LIVE order intent and result.

I4 Cost-capped AI under concurrency
AI spend is hard capped per UTC day and rolling window. Accounting is atomic under parallelism. Reaper/settlement transitions are idempotent.

I5 WS required for creating new exposure
If WS is not healthy for that market per the explicit predicates, new exposure is forbidden in PAPER and LIVE. REST is allowed only for monitoring and cancel/reconcile operations.

I6 No duplicate/ghost orders
Any ambiguous submit/cancel outcome enters PENDING_UNKNOWN. While any PENDING_UNKNOWN exists, RECONCILE_GREEN is false and the system must not create new exposure.

I7 Kill switch is a true barrier
Once HALTED is durably recorded (WAL fsync + DB write), no new exposure can be created; in-flight submits are bounded and then reconciled and cancelled.

============================================================
3) Locked defaults (operator requirements)
============================================================

3.1 Categories
- ALLOWLIST_CATEGORIES = {geopolitics, economics, tech/AI}
- DENYLIST_CATEGORIES = {sports}

3.2 Risk limits (percentage-of-wallet, auto-scales)
- DAILY_STOP_LOSS_PCT = 0.03
- MAX_PER_MARKET_PCT = 0.02
- MAX_TOTAL_EXPOSURE_PCT = 0.10
- MAX_OPEN_POSITIONS = 5
Rule: stricter rule wins if any conflict.

3.3 AI budget
- AI_CAP_USD_USER = 2.00
- AI_CAP_PCT_PER_DAY_DEFAULT = 0.005
- AI_CAP_USD_EFFECTIVE = min(AI_CAP_USD_USER, wallet_usd * AI_CAP_PCT_PER_DAY_DEFAULT)

Rolling window:
- AI_WINDOW_SEC = 600
- AI_WINDOW_CAP_PCT_OF_DAILY = 0.20
- AI_WINDOW_CAP_USD = AI_CAP_USD_EFFECTIVE * AI_WINDOW_CAP_PCT_OF_DAILY

Hard cap on number of analyses:
- AI_ANALYSES_PER_DAY_HARD_CAP = 100

3.4 Paper runway
- PAPER_RUNWAY_DAYS_MIN = 30
- PAPER_FEE_MULTIPLIER = 2.0
- PAPER_MIN_FEE_BPS = 10

3.5 Watchlist and throughput caps
- WATCHLIST_MAX = 200
- PROBATION_MAX = 50
- CANDIDATES_PER_MIN_MAX = 50
- PER_MARKET_CANDIDATES_PER_MIN_MAX = 10
- EVIDENCE_FETCHES_PER_HOUR_MAX = 60

3.6 WS intervals
- FAST_LOOP_SEC = 2
- WS_HEARTBEAT_SEC = 10

Decision freshness:
- MAX_MARKET_SNAPSHOT_AGE_DECISION_SEC = 6

Execution freshness:
- MAX_MARKET_SNAPSHOT_AGE_EXEC_SEC = 3
- MAX_DECISION_TO_EXEC_DELAY_SEC = 8

Candidate max age:
- CANDIDATE_MAX_AGE_SEC = 120

Trigger persistence / spoof resistance:
- TRIGGER_PERSIST_UPDATES = 3
- TRIGGER_PERSIST_MIN_SEC = 6

3.7 Execution guardrails
Reconciliation:
- RECONCILE_HEARTBEAT_SEC = 60
- RECONCILIATION_LAG_SEC = 5
- RECONCILE_RETRY_N = 3
- RECONCILE_RETRY_BACKOFF_SEC = 2

Residual cancel:
- LIVE_RESIDUAL_CANCEL_AFTER_SEC = 30

Cancel/replace storm protection:
- MAX_REPLACE_PER_MARKET_PER_MIN = 3
- MIN_REPLACE_INTERVAL_SEC = 5

Locks:
- LOCK_TTL_SEC = 60
- LOCK_RENEW_EVERY_SEC = 10
- LOCK_STEAL_GRACE_AFTER_EXPIRY_SEC = 5
- MIN_LOCK_TTL_BEFORE_SUBMIT_SEC = 10

3.8 Arming constants (locked)
- ARMING_WINDOW_SEC = 300
- ARMING_NONCE1_TTL_SEC = 120
- ARMING_FILE_MAX_AGE_SEC = 900
- TOTP_REPLAY_BLOCK_SEC = 60

3.9 Market quality thresholds (explicit defaults)
- TIME_TO_RESOLUTION_MIN_SEC = 3600
- TIME_TO_RESOLUTION_MAX_SEC = 90*86400
- MIN_VOLUME_24H_USD = 500
- MIN_LIQUIDITY_USD = 1000
- MAX_SPREAD_ABS = 0.03
- MIN_DEPTH_USD_NEAR_TOP = 50
- BOOK_LEVELS_REQUIRED = 3

Binary consistency anomaly:
- ASK_SUM_LOW = 0.98
- ASK_SUM_HIGH = 2.00

3.10 Clock drift
- CLOCK_SKEW_MAX_SEC = 5
If drift exceeds max, system must downgrade to OBSERVE_ONLY and forbid arming LIVE.

============================================================
4) Architecture overview
============================================================

Modules:
A) Market Registry (Gamma sync + eligibility classification)
B) Price & Orderbook Service (WS primary; REST monitoring)
C) Watchlist Manager (bounded selection)
D) Candidate Pipeline (fast triggers → slow evaluation queue)
E) Evidence Service (allowlist + deterministic parsers + hashing)
F) Injection Defence (deterministic pattern engine + versioned ruleset)
G) AI Analysis (OpenRouter swarm + strict JSON + quorum)
H) Budget Manager (atomic reservations + settlement + reaper)
I) Calibration + Trust Control (Brier + bins + w_ai control law)
J) Decision Engine (EV math + friction model)
K) Risk Manager (limits + MTM + daily stop)
L) Execution Engine (PAPER + LIVE)
M) Durability Layer (WAL + DB event log)
N) Reconciliation Engine (ghost-order prevention)
O) Observability + Alerts
P) Ops + Runbooks + Backups
Q) Test + Chaos suite
R) Config integrity + secrets

============================================================
5) Durable states, degraded modes, and control plane
============================================================

5.1 Durable trading states (stored in DB; only these are valid)
- OBSERVE_ONLY
- PAPER_TRADING
- LIVE_ARMED
- LIVE_TRADING
- HALTED
- HALTED_DAILY

5.2 State invariants
- HALTED is sticky and requires /unhalt <totp> to leave.
- HALTED_DAILY auto-expires at next UTC midnight and returns to prior non-LIVE state:
  - if prior was PAPER_TRADING, resume PAPER_TRADING
  - else resume OBSERVE_ONLY
- LIVE_TRADING can only be entered from LIVE_ARMED via confirm step2 within current process lifetime.
- On startup, if durable state is LIVE_ARMED or LIVE_TRADING, the system must force OBSERVE_ONLY before any other worker starts.

5.3 Degraded blocker flags (orthogonal; any blocker prevents LIVE_TRADING and new exposure)
Blockers:
- WS_DOWN
- DB_DEGRADED
- WAL_DEGRADED
- RECONCILE_DEGRADED
- CLOCK_SKEW
- COST_ACCOUNTING_DEGRADED
- INJECTION_DETECTOR_INVALID

Rule:
- If any blocker is set → force OBSERVE_ONLY for LIVE, and prohibit any new exposure.
- PAPER_TRADING may continue only if blocker is NOT one of:
  {WS_DOWN, DB_DEGRADED, WAL_DEGRADED, RECONCILE_DEGRADED, CLOCK_SKEW, INJECTION_DETECTOR_INVALID}

5.4 Startup sequence (strict order; no workers before completion)
(1) Load signed config manifest (Section 18). Verify hash and signature. If fail: HALTED.
(2) Verify secret file permissions and presence. If fail: HALTED.
(3) Clock drift check: compare system UTC vs DB UTC and vs exchange server time (REST endpoint). If |skew| > CLOCK_SKEW_MAX_SEC: set CLOCK_SKEW blocker and OBSERVE_ONLY.
(4) Load bot_state row and verify HMAC signature. If invalid: HALTED.
(5) If bot_state.state in {LIVE_ARMED, LIVE_TRADING}:
    - Write STATE_CHANGED to WAL (fsync) and DB (event_log).
    - Set bot_state.state = OBSERVE_ONLY and write to DB with new signature.
    - If WAL fsync fails or DB write fails: HALTED.
(6) Delete local arming file if present. If delete fails: HALTED.
(7) Invalidate all arming nonces in DB (delete/expire). If fails: HALTED.
(8) WAL replay (LIVE-only intents). If DB insert fails during replay: HALTED.
(9) Reconciliation-on-startup using REST allowed; record last_reconcile_completed_at_utc = now() on success.
(10) Initialise wallet_usd_last_good via REST wallet/balance call. If fails: remain OBSERVE_ONLY until obtained.
(11) Start workers only after steps (1)-(10).

5.5 Telegram privileged commands (only from allowlisted operator chat_id + user_id)
- /status
- /halt
- /unhalt <totp>
- /resume_paper <totp>
- /arm_live
- /confirm_live_step1 <nonce1> <totp>
- /confirm_live_step2 <nonce2> <totp>

5.6 Arming ceremony (two-step, TOTP, local file, per-process binding)
Step 0: /arm_live
- Create nonce1 (single-use), expires in ARMING_NONCE1_TTL_SEC.
- Reply with nonce1 and instructions.

Step 1: /confirm_live_step1 nonce1 totp
- Validate:
  (a) nonce1 exists, unused, unexpired
  (b) totp valid and not replayed within TOTP_REPLAY_BLOCK_SEC
- Consume nonce1.
- Set bot_state.state = LIVE_ARMED and bot_state.armed_until_utc = now + ARMING_WINDOW_SEC.
- Create nonce2 (single-use), expires at armed_until_utc.
- Reply with nonce2 and instructions to create local arming file.

Local arming file (must exist for step 2):
- path: /var/run/polyedge/armed (or /run/polyedge/armed)
- required ownership: root:polyedge
- required perms: 0640
- required JSON single-line content:
  {"nonce2":"...","ts_utc":"...","process_start_unix_ms":"...","sig":"..."}
- sig = HMAC_SHA256(nonce2 + "|" + ts_utc + "|" + process_start_unix_ms, LOCAL_STATE_SECRET) hex
- Validation:
  - now_utc - ts_utc <= ARMING_FILE_MAX_AGE_SEC
  - abs(now_utc - ts_utc) <= ARMING_WINDOW_SEC
  - process_start_unix_ms must match current process start time within ±5s

Step 2: /confirm_live_step2 nonce2 totp
- Validate:
  (a) current durable state == LIVE_ARMED and now <= armed_until_utc
  (b) nonce2 exists, unused, unexpired, equals argument
  (c) totp valid and not replayed
  (d) local arming file exists and passes validation above
- Consume nonce2.
- Transition durable state to LIVE_TRADING, write STATE_CHANGED to WAL (fsync) and DB.
- Delete arming file. If delete fails: HALTED.

============================================================
6) Market Registry (Gamma sync) and eligibility
============================================================

6.1 Sync schedule
- Full sync every 30 minutes
- Delta sync every 2 minutes

6.2 Stored market fields (minimum)
- market_id, condition_id, event_id
- category, tags, title, description
- resolutionSource text
- endDate/close time, resolve time (if available)
- YES token id, NO token id
- volume_24h_usd, liquidity_usd (if available)
- critical_field_hash = SHA256(title|description|resolutionSource|endDate|token_ids|category)

6.3 Binary mapping rules (deterministic)
Market is binary-eligible iff:
- exactly two outcomes/tokens
- outcome labels map to YES and NO after normalisation:
  normalise(label):
    - Unicode NFKC
    - trim
    - collapse whitespace to single spaces
    - uppercase
Accepted YES labels: "YES"
Accepted NO labels: "NO"
If labels are not exactly YES/NO after normalisation, market is NON_BINARY and excluded.

6.4 Rule-change monitor
If critical_field_hash changes for a market with any open position or open order:
- Freeze new exposure in that market immediately.
- Allow cancel-only and close-only operations.
- Require full re-evaluation before any further action.

============================================================
7) Price & Orderbook Service (WS primary)
============================================================

7.1 WS ingestion
Maintain:
- ws_connected boolean
- ws_last_message_unix_ms (global)
- current_ws_epoch (monotonic; increment immediately on disconnect)
Per market_id:
- market_last_ws_update_unix_ms (updated when any message for that market received)
- orderbook_last_change_unix_ms (updated only when best bid/ask OR any of top BOOK_LEVELS_REQUIRED levels change)

7.2 Snapshot schema (immutable; WS-only snapshots required for trading)
snapshot fields:
- snapshot_id UUID
- market_id
- snapshot_at_unix_ms (local receive time)
- snapshot_source: "WS" or "REST"
- snapshot_ws_epoch
- ws_last_message_unix_ms
- market_last_ws_update_unix_ms
- orderbook_last_change_unix_ms
- best_bid_yes, best_ask_yes, best_bid_no, best_ask_no
- depth_levels_yes[], depth_levels_no[] for top BOOK_LEVELS_REQUIRED (price, size_usd)
- orderbook_hash = SHA256(canonical orderbook JSON)
- ask_sum_anomaly:
    true if (best_ask_yes + best_ask_no) < ASK_SUM_LOW OR > ASK_SUM_HIGH
- invalid_book_anomaly:
    true if any of:
      - any price <= 0 or >= 1
      - bid > ask on any side
      - missing best bid or best ask on either token

7.3 WS health predicates (split: decision vs execution)
WS_HEALTHY_DECISION(market_id, snapshot) is true iff:
- ws_connected == true
- now - ws_last_message_unix_ms <= WS_HEARTBEAT_SEC*1000
- snapshot.snapshot_source == "WS"
- snapshot.snapshot_ws_epoch == current_ws_epoch
- snapshot.market_id == market_id
- snapshot.market_last_ws_update_unix_ms is not null and >0
- now - snapshot.market_last_ws_update_unix_ms <= MAX_MARKET_SNAPSHOT_AGE_DECISION_SEC*1000
- snapshot.orderbook_last_change_unix_ms is not null and >0
- now - snapshot.orderbook_last_change_unix_ms <= MAX_MARKET_SNAPSHOT_AGE_DECISION_SEC*1000
- snapshot.ws_last_message_unix_ms >= snapshot.snapshot_at_unix_ms

WS_HEALTHY_EXEC(market_id, snapshot) is identical except it uses MAX_MARKET_SNAPSHOT_AGE_EXEC_SEC.

7.4 REST usage rules
REST is allowed for:
- market discovery fallback
- reconciliation (open orders, fills, positions)
- cancel-only operations
REST is forbidden for:
- producing a tradable snapshot
- any decision or execution that creates new exposure

============================================================
8) Watchlist Manager
============================================================

8.1 Watchlist selection
- Maintain watchlist of up to WATCHLIST_MAX eligible markets.
- Use a scoring function that prioritises:
  - nearing resolution window
  - sufficient volume/liquidity
  - tight spreads
  - “recently active” orderbooks
- Enforce PROBATION_MAX: markets with repeated anomalies are put into probation and excluded from watchlist until probation expires.

8.2 Noisy quarantine
If a market triggers >10 times/hour and yields no trade:
- quarantine for 2 hours
- send one deduped alert per quarantine event

============================================================
9) Candidate Pipeline
============================================================

9.1 Fast loop (every FAST_LOOP_SEC)
For each watchlist market:
- read latest WS snapshot
- compute triggers (examples):
  - spread_change
  - depth_drop
  - mid_move
  - approaching_resolution
- only enqueue a Candidate if trigger persists:
  - at least TRIGGER_PERSIST_UPDATES WS updates, and
  - at least TRIGGER_PERSIST_MIN_SEC elapsed
- enforce global + per-market candidate caps.

9.2 Candidate record
- candidate_id UUID
- market_id
- snapshot_id
- created_at_unix_ms
- trigger_reasons[]
- status enum: NEW, FILTERED, EVIDENCE_DONE, AI_DONE, DECIDED, EXECUTED, DROPPED

9.3 Coarse deterministic filters (fail fast)
Reject candidate (NO_TRADE) if any:
- candidate_age > CANDIDATE_MAX_AGE_SEC
- market not eligible (category deny, non-binary)
- time_to_resolution outside min/max bounds
- volume_24h_usd < MIN_VOLUME_24H_USD or liquidity_usd < MIN_LIQUIDITY_USD
- invalid_book_anomaly == true
- ask_sum_anomaly == true
- spread > MAX_SPREAD_ABS (computed on both YES and NO as ask - bid; if either > MAX_SPREAD_ABS reject)
- depth_top_levels < MIN_DEPTH_USD_NEAR_TOP on either side (sum size_usd of top BOOK_LEVELS_REQUIRED bids for that token)
- WS_HEALTHY_DECISION == false

============================================================
10) Evidence Service (allowlist, deterministic, hostile input)
============================================================

10.1 Evidence principles
- Evidence is fetched by code from a signed allowlist (no LLM browsing).
- Evidence text is hostile input; injection defence applies to evidence too.
- Default evidence mode: STRICT.

10.2 Evidence modes
- STRICT (default): any thesis market requires evidence bundle.
- MARKET_ONLY: permitted only for markets explicitly marked strategy_type=PURE_MICROSTRUCTURE in signed config; otherwise forbidden.
- STRICT_WITH_CORROBORATION: for high-stakes candidates (defined below).

10.3 Thesis vs microstructure determination (deterministic)
Candidate requires evidence (THESIS_REQUIRED=true) if any:
- category in {geopolitics, economics, tech/AI} AND trigger includes mid_move or approaching_resolution
- intended_order_size_usd >= 0.5% of wallet
- resolution text contains any subjective term (config list)
Otherwise, THESIS_REQUIRED=false only if strategy_type=PURE_MICROSTRUCTURE.

10.4 High-stakes candidate definition
HIGH_STAKES=true if any:
- intended_order_size_usd >= 1.0% of wallet
- time_to_resolution <= 6 hours
- dispute_risk prior >= 0.7 (if available)

10.5 Source registry (signed file)
config/evidence_sources.json entries:
- source_id
- domains
- type: API|RSS|HTML
- parser_name, parser_version
- reliability_tier: 1|2|3
- freshness_ttl_sec
- category_overrides_ttl_sec (optional)

TTL validity:
Evidence item is valid iff:
- published_at_utc exists AND (now_utc - published_at_utc) <= min(source_ttl, category_ttl_override_if_any)

10.6 Evidence bundle build (deterministic)
- MAX_EVIDENCE_ITEMS = 6
- Sort selection:
  Tier1 newest first, then Tier2 newest, then Tier3 newest; tie-break by source_id.
- Enforce caps:
  MAX_EVIDENCE_BYTES_TOTAL = 250KB
  MAX_EVIDENCE_TEXT_CHARS_TOTAL = 40,000
- Truncation is deterministic:
  truncate by dropping lowest-tier items first, then oldest, then truncate text by character count.

10.7 Conflict policy (deterministic)
Conflict exists if:
- two Tier1/Tier2 items assert mutually exclusive outcomes, OR
- numeric claims differ beyond tolerance (configurable; default 2% relative)

Resolution:
- If HIGH_STAKES and Tier1 valid items < 2 after excluding SUSPICIOUS -> NO_TRADE with EVIDENCE_TIER1_INSUFFICIENT.
- If Tier1 majority exists -> proceed but increase dispute_buffer multiplier to 1.5.
- Else -> NO_TRADE with EVIDENCE_CONFLICT.

============================================================
11) Injection defence (deterministic, versioned, signed)
============================================================

11.1 Ruleset file
config/injection_patterns.json:
- pattern_set_version (semver)
- updated_at_utc
- patterns[] each with: pattern_id, regex_utf8, severity {SUSPICIOUS|INJECTION_DETECTED}

11.2 Governance
- injection_patterns.json is included in the signed manifest hash.
- If missing or pattern_set_version < MIN_INJECTION_VERSION in config -> set INJECTION_DETECTOR_INVALID blocker and OBSERVE_ONLY.

11.3 Normalisation before detection
Injection detection must run on:
- Unicode NFKC normalised
- BOM stripped
- null bytes removed
- whitespace collapsed

11.4 Actions
If INJECTION_DETECTED in market text or Tier1 evidence:
- NO_TRADE and alert
If only SUSPICIOUS:
- for HIGH_STAKES: NO_TRADE
- otherwise: allowed only if Tier1 count remains >=2 and AI quorum agreement is strong; else NO_TRADE

============================================================
12) AI Analysis (OpenRouter swarm, strict JSON)
============================================================

12.1 Swarm composition (fixed weights, total weight=6)
- deepseek/deepseek-v3.2 weight 2
- minimax/minimax-m2.5 weight 2
- moonshotai/kimi-k2.5 weight 1
- z-ai/glm-5 weight 1

12.2 Timeouts
- PER_MODEL_TIMEOUT_SEC = 8
- SWARM_TOTAL_TIMEOUT_SEC = 10

12.3 Retry policy (budget-safe)
- Default: no retries.
- One optional SWARM_RETRY_TOTAL = 1 is allowed only if quorum is not met due to a transient failure,
  and only if budget reservation succeeds for the retry call(s).
- A retry is never per-model; it is a single “retry sweep” where only failed models are called once more.

12.4 Quorum and disagreement
- Quorum requires:
  (a) >= 3 distinct models returned valid JSON
  (b) total weight returned >= 4
- Disagreement threshold:
  DISAGREE_THRESHOLD = 0.12 (weighted stdev of prob_yes_raw)
- If quorum fails or disagreement exceeds threshold -> NO_TRADE and alert.

12.5 Strict JSON schema
schema_version = "polyedge.ai.v2.5"
Required:
- market_id (string)
- prob_yes_raw (number 0..1)
- confidence_raw (number 0..1)
- resolution_risk (0..1)
- dispute_risk (0..1)
- resolution_summary (string)
- evidence_summary (string)
- uncertainty_reason (string)
- key_drivers (array of strings)
- disqualifiers (array of strings)
- recommended_side ("YES"|"NO"|"NO_TRADE")
- notes (string)

Invalid JSON or out-of-range -> parse_ok=false; that model contributes zero weight.

12.6 Late AI results discard rule (enforceable)
Every AI call is tied to:
- candidate_id
- candidate_state_version
- barrier_generation (monotonic)
On AI completion:
- if BARRIER true OR barrier_generation changed OR candidate state no longer AI_PENDING -> discard and log AI_RESULT_DISCARDED.

============================================================
13) AI Budget Manager (atomic, idempotent)
============================================================

13.1 Budget unit
AI_CALL = one HTTP request to one model.

13.2 Tables
- ai_budget_day(day_utc PK, spent_usd, in_flight_usd, updated_at_utc)
- ai_reservations(reservation_id PK, day_utc, ts_utc_db, model_key, reserved_usd, actual_usd, status, correlation_id, expires_at_utc)
Status enum: RESERVED, SETTLED, FORCE_SETTLED, RELEASED

13.3 Reservation algorithm (SERIALIZABLE + row locks)
Within one SERIALIZABLE transaction:
- Lock ai_budget_day row FOR UPDATE.
- Compute daily_effective_cap = AI_CAP_USD_EFFECTIVE.
- Compute window_sum_usd as:
  sum(COALESCE(actual_usd, reserved_usd))
  for reservations with ts_utc_db >= (db_now - AI_WINDOW_SEC) AND ts_utc_db <= (db_now + 5s)
  AND status in (RESERVED, SETTLED, FORCE_SETTLED)
- Add ai_budget_day.in_flight_usd to window_sum_usd.
- Check:
  spent_usd + in_flight_usd + worst_case_usd <= daily_effective_cap
  window_sum_usd + worst_case_usd <= AI_WINDOW_CAP_USD
- Enforce analysis count cap:
  count(distinct correlation_id) for day_utc < AI_ANALYSES_PER_DAY_HARD_CAP
- If any check fails: deny reservation with AI_BUDGET_DENIED.

On success:
- insert ai_reservations row status=RESERVED, reserved_usd=worst_case_usd, expires_at=db_now+120s
- increment ai_budget_day.in_flight_usd by worst_case_usd

13.4 Settlement (idempotent compare-and-swap)
Within SERIALIZABLE tx:
- Lock ai_budget_day row FOR UPDATE.
- UPDATE ai_reservations SET status=SETTLED, actual_usd=?, ... WHERE reservation_id=? AND status=RESERVED.
- If rows_affected==0 -> log RESERVATION_ALREADY_FINAL and do nothing.
- Else:
  - decrement in_flight_usd by reserved_usd
  - increment spent_usd by actual_usd (if missing, use reserved_usd)
  - store actual_usd on reservation so future window sums use actual where available.

13.5 Reaper (FORCE_SETTLE, idempotent)
Runs every 30s:
- Find reservations status=RESERVED with expires_at_utc < db_now - 5s.
- For each:
  SERIALIZABLE tx:
    Lock ai_budget_day FOR UPDATE.
    UPDATE ai_reservations SET status=FORCE_SETTLED, actual_usd=reserved_usd WHERE reservation_id=? AND status=RESERVED.
    if rows_affected==1:
      decrement in_flight by reserved
      increment spent by reserved
      log AI_FORCE_SETTLED
If FORCE_SETTLED count >=3 in LIVE within one UTC day -> set COST_ACCOUNTING_DEGRADED blocker and OBSERVE_ONLY.

============================================================
14) Calibration + baseline vs AI trust control
============================================================

14.1 Baseline p_market
- For entry feasibility: use best ask of the side being bought.
- For conservative marking: use best bid.

14.2 AI influence control law
- w_ai_max = 0.35
- Until N_RESOLVED >= 50 -> w_ai = 0 for all categories (AI cannot move baseline).
- When sufficient samples exist, compute w_ai based on:
  - category calibration vs baseline
  - disagreement
  - evidence tier mix
  - dispute_risk
- p_eff = p_market + w_ai * (p_ai_cal - p_market)
Hard bounds:
- DELTA_MAX = 0.10 default
- if dispute_risk >= 0.7 -> DELTA_MAX = 0.05
- if |p_eff - p_market| > 0.20 -> NO_TRADE with P_EFF_OUTLIER

============================================================
15) Decision engine (EV math + friction model)
============================================================

15.1 Position payoff
Binary token pays $1 if correct, $0 otherwise.

15.2 Entry prices
- entry_price_yes = best_ask_yes (or chosen limit price if maker)
- entry_price_no = best_ask_no

15.3 Friction model (all in $ per $1 payout share, same unit as prices)
required_edge =
  spread_cost +
  fee_cost +
  slippage_buffer +
  dispute_buffer +
  latency_penalty +
  time_value_penalty

Default conservative formulas:
- spread_cost (maker-first):
  spread_cost = 0.5 * (ask - bid)  (use side-specific)
- fee_cost:
  fee_cost = max(fee_rate_bps/10000, PAPER_MIN_FEE_BPS/10000 in paper) * (PAPER_FEE_MULTIPLIER in paper else 1.0)
- slippage_buffer:
  slippage_buffer = max(0.005, order_size_usd / max(depth_usd_top_levels, 1) * 0.02)
- dispute_buffer:
  dispute_buffer = 0.01 + 0.02 * dispute_risk
  if evidence conflict Tier1 majority used: dispute_buffer *= 1.5
- latency_penalty:
  latency_penalty = max(0, (decision_to_exec_sec - 2)) * 0.001
- time_value_penalty:
  time_value_penalty = min(0.02, time_to_resolution_days * 0.0002)

15.4 EV calculation (YES side)
EV_yes = (p_eff * 1.0) - entry_price_yes - required_edge
EV_no  = ((1 - p_eff) * 1.0) - entry_price_no  - required_edge

15.5 Trade rule
- Execute only if max(EV_yes, EV_no) >= EV_MIN where EV_MIN = 0.01
- Choose side with higher EV.
- If both < EV_MIN -> NO_TRADE.

============================================================
16) Risk Manager (limits, MTM, daily stop)
============================================================

16.1 Order sizing
- intended_order_size_usd = min(
    MAX_PER_MARKET_PCT * wallet_usd_last_good,
    remaining_exposure_capacity_usd,
    venue_balance_available_usd
  )
- enforce MAX_OPEN_POSITIONS and MAX_TOTAL_EXPOSURE_PCT.

16.2 Two MTM marks
- conservative_mtm: for reporting, uses best bid when available, else 0.
- risk_mtm: for daily stop, uses TWAP with anti-spoof.

16.3 risk_mtm TWAP (anti-spoof)
- Window: 300s
- Build mid samples only when:
  (a) both bid and ask exist,
  (b) spread <= 0.10 (10%) AND depth at top >= MIN_DEPTH_USD_NEAR_TOP,
  otherwise sample is invalid and not used.
- Outlier rejection: discard mids > 2 std dev from mean (if >=10 samples), else use median.
- Require at least 3 samples spanning >=60s; else fall back to last_trade if <=10m old.
- If still unavailable:
  - allow entry-price fallback only for first 300s after position opened.
  - after that, if no valid valuation for 3 consecutive checks -> HALTED.

16.4 wallet_usd_last_good
- Initialise on startup via REST.
- Update only when risk_mtm is based on TWAP or last_trade (not entry fallback).
- If stale > 3600s -> set OBSERVE_ONLY + alert WALLET_REF_STALE.

16.5 Daily stop loss
- If daily_pnl <= -DAILY_STOP_LOSS_PCT * wallet_usd_last_good -> enter HALTED_DAILY.
- On HALTED_DAILY:
  - cancel all resting orders (best effort with reconcile)
  - block new exposure until next UTC midnight

============================================================
17) Execution Engine (PAPER + LIVE)
============================================================

17.1 Global submit gate and barrier generation
- SUBMIT_GATE mutex (global) must be held during any network submit.
- barrier_generation increments on every /halt.
- Every submit attempt records submit_generation = barrier_generation at the start.

17.2 Pre-exec hard checks (immediately before network send)
Must all be true:
- durable state in {PAPER_TRADING, LIVE_TRADING}
- BARRIER == false AND submit_generation == barrier_generation
- candidate_age <= CANDIDATE_MAX_AGE_SEC
- WS_HEALTHY_EXEC(market_id, snapshot) == true
- snapshot.snapshot_ws_epoch == current_ws_epoch
- decision_to_exec_delay <= MAX_DECISION_TO_EXEC_DELAY_SEC
- RECONCILE_GREEN == true
- lock owned by this worker and lock_expires_at >= now + MIN_LOCK_TTL_BEFORE_SUBMIT_SEC
- no ACTIVE Level2/Level3 mismatches

If any fail -> abort submit (NO_TRADE with explicit reason).

17.3 PAPER execution (pessimistic)
- No “touch = fill”.
- Maker fill requires:
  - trade-through by >=1 tick AND persists >=3s.
- Fees:
  apply max(actual_fee_bps, PAPER_MIN_FEE_BPS) * PAPER_FEE_MULTIPLIER.

17.4 LIVE execution (maker-first)
- Only limit orders.
- Default: postOnly maker orders.
- Taker-like marketable limits permitted only if:
  EV >= EV_MIN + 0.03 AND volatility low AND spread <= 0.02.

17.5 Idempotency and client_order_id (no attempt_num)
- Each decision produces a deterministic decision_id (hash).
- client_order_id is derived ONLY from decision_id (no attempt counters).
- Format:
  - decision_id_hex = SHA256(decision_canonical_string) hex
  - client_order_id = decision_id_hex
  - If venue max length < len(client_order_id), set:
      client_order_id = first N chars of decision_id_hex (N = venue_client_order_id_max_len)
  - Store mapping decision_id_hex -> client_order_id in DB for reconciliation.

Rule:
- A decision_id may result in at most one LIVE submit.
- There are no submit retries. Any ambiguity goes to PENDING_UNKNOWN and must reconcile.

17.6 PENDING_UNKNOWN handling (submit or cancel)
If submit response is timeout/unknown/5xx:
- set local order status = PENDING_UNKNOWN
- loop reconcile every 5s for up to 60s (REST allowed)
Outcomes:
(A) FOUND:
  - must match decision side exactly
  - size within 1%
  - price within 0.5%
  else HALTED with PENDING_UNKNOWN_MISMATCH
(B) ABSENT_CONFIRMED:
  - mark CANCELLED
  - release exposure reservation immediately
  - block new orders in that market for 300s
  - require new candidate with fresh snapshot before any later attempt
(C) INCONCLUSIVE after 60s:
  - HALTED with ORPHAN_RISK
Additional staleness guard:
- if mid price moved > 2% since PENDING_UNKNOWN started, discard candidate and require fresh evaluation even if ABSENT_CONFIRMED.

17.7 Partial fills and residual cancel race
- If WS fill arrives while order status=CANCEL_REQUESTED:
  - transition to PARTIALLY_FILLED
  - recalc residual
- Residual policy:
  - if residual exists after LIVE_RESIDUAL_CANCEL_AFTER_SEC -> send cancel
  - if cancel response ambiguous -> PENDING_UNKNOWN (cancel) and reconcile
  - if unresolved after 60s -> HALTED with RESIDUAL_CANCEL_UNKNOWN

17.8 Cancel/replace restrictions
- Before placing a replacement order, cancellation must be confirmed absent via reconciliation.
- If cancel confirmation cannot be obtained within 60s -> HALTED.

============================================================
18) Durability Layer (WAL + DB)
============================================================

18.1 Requirements
LIVE must not submit a network order unless ORDER_INTENT is durably written:
- WAL write + fsync
- DB insert

18.2 WAL records
WAL is append-only with fsync per record.
Record types (minimum):
- STATE_CHANGED
- ORDER_INTENT (mode=LIVE only)
- ORDER_INTENT_ABORTED
- ORDER_RESULT
- CANCEL_INTENT (mode=LIVE only)
- CANCEL_RESULT

18.3 LIVE two-phase rule (strict)
Before LIVE submit:
(1) write ORDER_INTENT to WAL + fsync
(2) write ORDER_INTENT to DB (event_log)
If either (1) or (2) fails -> abort submit; do not send network request.
If (1) succeeded and (2) failed -> write ORDER_INTENT_ABORTED to WAL + fsync; remain OBSERVE_ONLY until DB recovers.

After submit returns:
(3) write ORDER_RESULT to WAL + fsync
(4) write ORDER_RESULT to DB
If (4) fails -> set DB_DEGRADED and OBSERVE_ONLY (fail-closed); keep order in PENDING_UNKNOWN until DB recovers and reconcile.

18.4 WAL replay (startup)
- Replay WAL records into DB in WAL offset order (deterministic).
- If any DB insert fails during replay -> HALTED.
- Adopt orphans:
  If ORDER_INTENT exists without ORDER_RESULT and without ORDER_INTENT_ABORTED:
    create local order PENDING_UNKNOWN and immediately enqueue reconciliation.
- PAPER intents are never written to WAL and are never adopted.

============================================================
19) Reconciliation Engine and RECONCILE_GREEN
============================================================

19.1 Reconcile triggers
- Startup after WAL replay
- Before any LIVE submit
- Every RECONCILE_HEARTBEAT_SEC
- After WS reconnect
- After any cancel/replace
- During PENDING_UNKNOWN loops

19.2 REST authority in reconcile
REST is allowed for:
- open orders list
- fills list
- positions
- balances (optional)
This does not violate “WS required for trading” because reconciliation must not create exposure.

19.3 Mismatch table
Persist mismatches with:
- mismatch_id
- market_id (nullable)
- level: 1|2|3
- status: ACTIVE|RESOLVED
- first_seen_utc, last_seen_utc
- details_json

Mismatch clearing rule:
- A mismatch transitions to RESOLVED only when a full reconcile cycle sees local DB state exactly matches venue state for that entity AND exposure delta is zero.

19.4 Severity thresholds (wallet-aware floors)
- MIN_RECONCILE_THRESHOLD_USD = 1.00
- Level 2 threshold = max(0.001*wallet_usd_last_good, MIN_RECONCILE_THRESHOLD_USD)
- Level 3 threshold = max(0.001*wallet_usd_last_good, 5.00)

Cumulative drift guard:
- Track cumulative level-1 deltas per day.
- If cumulative > 3 * MIN_RECONCILE_THRESHOLD_USD -> escalate to Level 2 + alert.

19.5 RECONCILE_GREEN predicate (exact)
RECONCILE_GREEN == true iff:
- last_reconcile_completed_at_utc exists and now - last_reconcile_completed_at_utc <= 120s
- last_reconcile_completed_at_utc >= ws_last_message_time_utc (reconcile happened after last WS activity)
- zero ACTIVE Level2 and Level3 mismatches
- zero PENDING_UNKNOWN orders of any age
- BARRIER == false
- WS_DOWN blocker is false

============================================================
20) Locks and concurrency control
============================================================

20.1 Market lock table
market_locks:
- market_id PK
- owner_instance_id
- owner_worker_id
- lock_version (monotonic)
- owner_heartbeat_utc
- expires_at_utc
- last_renewed_utc

20.2 Acquisition
- Acquire if no row exists OR row expired AND (now - expires_at) >= LOCK_STEAL_GRACE_AFTER_EXPIRY_SEC
  OR owner_heartbeat_utc < now - 2*LOCK_TTL_SEC.
- On acquire: set lock_version += 1.

20.3 Renewal
- Every LOCK_RENEW_EVERY_SEC, update owner_heartbeat_utc and expires_at_utc = now + LOCK_TTL_SEC, increment lock_version.
- If renewal fails:
  - if any PENDING_UNKNOWN exists in that market -> HALTED with LOCK_RENEW_FAILED_DURING_PENDING_UNKNOWN
  - else drop candidate with LOCK_RENEW_FAILED (no submit).

20.4 Pre-exec validation (must run immediately before network send)
- Owner matches
- lock_expires_at >= now + MIN_LOCK_TTL_BEFORE_SUBMIT_SEC
- lock_version matches the version recorded at DECISION time

============================================================
21) Observability, reason codes, and logging
============================================================

21.1 Canonical event log
All modules must write events to DB event_log and also mirror critical events to WAL where required:
- event_id, ts_utc, type, correlation_ids, payload_json, payload_hash

21.2 Required NO_TRADE reason codes (minimum set)
- WS_UNHEALTHY_DECISION
- WS_UNHEALTHY_EXEC
- SNAPSHOT_INVALID_BOOK
- SNAPSHOT_ASK_SUM_ANOMALY
- SPREAD_TOO_WIDE
- DEPTH_TOO_THIN
- MARKET_NOT_ELIGIBLE
- TIME_TO_RESOLUTION_OUT_OF_RANGE
- EVIDENCE_REQUIRED
- EVIDENCE_CONFLICT
- EVIDENCE_TIER1_INSUFFICIENT
- INJECTION_DETECTED
- AI_BUDGET_DENIED
- AI_QUORUM_FAILED
- AI_DISAGREEMENT
- AI_SCHEMA_INVALID
- AI_TIMEOUT
- P_EFF_OUTLIER
- EV_TOO_LOW
- RISK_LIMIT_HIT
- RECONCILE_NOT_GREEN
- LOCK_LOST
- BARRIER_ACTIVE

21.3 Alerts (Telegram)
Send alerts for:
- any transition into LIVE_ARMED or LIVE_TRADING
- any forced downgrade on startup
- any HALTED/HALTED_DAILY
- WS_DOWN, CLOCK_SKEW
- ORPHAN_RISK / PENDING_UNKNOWN_MISMATCH
- COST_ACCOUNTING_DEGRADED
- CONFIG_TAMPER / CONFIG_INVALID

Dedup rule:
- include dedup_key on alerts; example for halt-cancel pending:
  "HALT_CANCEL_PENDING_" + halted_at_utc

============================================================
22) Config integrity, pinned pricing, and secrets
============================================================

22.1 Signed config manifest
A single manifest includes hashes of:
- config.yaml
- evidence_sources.json
- injection_patterns.json
- model_pricing.json (pinned prices + max token caps)
- allowlisted operator IDs and chat IDs
- venue_client_order_id_max_len

Startup rejects if manifest invalid. On reject: HALTED + alert CONFIG_TAMPER.

22.2 Secrets
- LOCAL_STATE_SECRET (HMAC for state signature and arming file)
- TELEGRAM_BOT_TOKEN
- OPENROUTER_API_KEY
- POLYMARKET API keys
Secret file permissions:
- must not be world-readable
- if insecure -> HALTED

============================================================
23) Testing and chaos suite (minimum required before LIVE)
============================================================

Must implement and pass at least these scenarios:

A) WS decision vs exec split:
- decision passes at 5s, exec fails at 4s; no trade.

B) Kill switch race:
- /halt during in-flight submit; no new exposure; reconcile finds and cancels.

C) WAL fsync failure:
- intent fsync fails -> no network submit.

D) WAL orphan adoption:
- LIVE intent without result -> PENDING_UNKNOWN on restart; reconcile resolves.

E) Budget parallelism:
- parallel reservations never exceed daily/window caps; in_flight included.

F) Reaper vs settlement race:
- double-decrement impossible (rows_affected==1 logic).

G) Lock expiry buffer:
- lock must have >=10s TTL pre-submit; steal grace prevents double submit.

H) RECONCILE_GREEN strict:
- any PENDING_UNKNOWN blocks trading.

I) MTM spoof:
- wide-spread single-mid cannot trigger daily stop; TWAP outlier rejection works.

J) Clock drift:
- >5s drift forces OBSERVE_ONLY and blocks arming.

============================================================
24) Phased build plan with Definition of Done (DoD)
============================================================

Phase 0: Repo scaffolding + config signing + DB schema + WAL writer/replay
DoD:
- Config manifest verification blocks startup on mismatch
- WAL fsync works; replay works and is deterministic
- Secrets permission checks halt correctly

Phase 1: Market Registry
DoD:
- Gamma sync works, binary mapping deterministic
- critical_field_hash monitoring + freeze implemented

Phase 2: WS Price Service + Snapshots
DoD:
- Per-market timestamps and orderbook_last_change tracked
- WS health predicates implemented and tested
- REST monitoring/reconcile allowed, but cannot create exposure

Phase 3: Candidate Pipeline + Watchlist + Quarantine
DoD:
- Trigger persistence prevents spoof
- Candidate caps enforced
- Quarantine works and dedup alerts

Phase 4: Evidence Service + Injection Defence
DoD:
- Deterministic evidence bundle hash reproducible
- TTLs enforce published_at anchor
- Injection ruleset versioned and signed; fail-closed if invalid

Phase 5: AI Swarm + Budget Manager
DoD:
- Strict JSON validation, quorum, disagreement
- Atomic reservation/settlement/reaper; parallel tests pass

Phase 6: Calibration + Trust Control
DoD:
- Brier score and bins stored
- w_ai=0 until N_RESOLVED>=50
- LIVE gate conditions enforced

Phase 7: PAPER Execution
DoD:
- Pessimistic fills + 2× fees
- Daily paper report includes reason codes and replay links

Phase 8: LIVE Execution + Durability + Reconcile
DoD:
- Two-phase intent/result enforced
- PENDING_UNKNOWN loops halt after 60s inconclusive
- Cancel/replace confirmation required
- /halt barrier verified in chaos tests

Phase 9: Ops hardening
DoD:
- Runbooks documented
- Backup/restore drills
- Chaos suite automated

LIVE entry gate (must all be true):
- PAPER_RUNWAY_DAYS_MIN completed
- N_RESOLVED >= 50 outcomes recorded for calibration
- Calibration not worse than baseline on trailing window
- Operator performs arming ceremony in current process lifetime
- No blockers active

============================================================
25) Database schema (DDL – minimal required)
============================================================

NOTE: All numeric money fields stored as integer cents where feasible. Prices stored as numeric(10,6).

-- Bot state
CREATE TABLE IF NOT EXISTS bot_state (
  id BOOLEAN PRIMARY KEY DEFAULT TRUE CHECK (id),
  state TEXT NOT NULL,
  counter BIGINT NOT NULL,
  ts_utc TIMESTAMPTZ NOT NULL,
  armed_until_utc TIMESTAMPTZ,
  halt_until_utc TIMESTAMPTZ,
  halt_resume_state TEXT,
  state_signature BYTEA NOT NULL
);

-- Market locks
CREATE TABLE IF NOT EXISTS market_locks (
  market_id TEXT PRIMARY KEY,
  owner_instance_id TEXT NOT NULL,
  owner_worker_id TEXT NOT NULL,
  lock_version BIGINT NOT NULL,
  owner_heartbeat_utc TIMESTAMPTZ NOT NULL,
  expires_at_utc TIMESTAMPTZ NOT NULL,
  last_renewed_utc TIMESTAMPTZ NOT NULL
);

-- Snapshots
CREATE TABLE IF NOT EXISTS snapshots (
  snapshot_id UUID PRIMARY KEY,
  market_id TEXT NOT NULL,
  snapshot_at_unix_ms BIGINT NOT NULL,
  snapshot_source TEXT NOT NULL CHECK (snapshot_source IN ('WS','REST')),
  snapshot_ws_epoch BIGINT NOT NULL,
  ws_last_message_unix_ms BIGINT NOT NULL,
  market_last_ws_update_unix_ms BIGINT,
  orderbook_last_change_unix_ms BIGINT,
  best_bid_yes NUMERIC(10,6),
  best_ask_yes NUMERIC(10,6),
  best_bid_no  NUMERIC(10,6),
  best_ask_no  NUMERIC(10,6),
  depth_yes JSONB NOT NULL,
  depth_no JSONB NOT NULL,
  orderbook_hash BYTEA NOT NULL,
  ask_sum_anomaly BOOLEAN NOT NULL,
  invalid_book_anomaly BOOLEAN NOT NULL
);

-- Decisions
CREATE TABLE IF NOT EXISTS decisions (
  decision_id_hex TEXT PRIMARY KEY,
  market_id TEXT NOT NULL,
  candidate_id UUID NOT NULL,
  created_at_utc TIMESTAMPTZ NOT NULL,
  side TEXT NOT NULL CHECK (side IN ('YES','NO')),
  size_usd_cents BIGINT NOT NULL,
  entry_price NUMERIC(10,6) NOT NULL,
  p_market NUMERIC(10,6) NOT NULL,
  p_eff NUMERIC(10,6) NOT NULL,
  required_edge NUMERIC(10,6) NOT NULL,
  ev NUMERIC(10,6) NOT NULL,
  reason_code TEXT NOT NULL,
  gates JSONB NOT NULL,
  client_order_id TEXT NOT NULL
);

-- Orders
CREATE TABLE IF NOT EXISTS orders (
  local_order_id UUID PRIMARY KEY,
  decision_id_hex TEXT NOT NULL REFERENCES decisions(decision_id_hex),
  market_id TEXT NOT NULL,
  token_side TEXT NOT NULL CHECK (token_side IN ('YES','NO')),
  status TEXT NOT NULL,
  client_order_id TEXT NOT NULL,
  exchange_order_id TEXT,
  price NUMERIC(10,6) NOT NULL,
  size_usd_cents BIGINT NOT NULL,
  filled_usd_cents BIGINT NOT NULL DEFAULT 0,
  residual_usd_cents BIGINT NOT NULL,
  pending_unknown_since_utc TIMESTAMPTZ,
  created_at_utc TIMESTAMPTZ NOT NULL,
  updated_at_utc TIMESTAMPTZ NOT NULL
);

-- Reconcile mismatches
CREATE TABLE IF NOT EXISTS reconcile_mismatches (
  mismatch_id UUID PRIMARY KEY,
  market_id TEXT,
  level INT NOT NULL CHECK (level IN (1,2,3)),
  status TEXT NOT NULL CHECK (status IN ('ACTIVE','RESOLVED')),
  first_seen_utc TIMESTAMPTZ NOT NULL,
  last_seen_utc TIMESTAMPTZ NOT NULL,
  details JSONB NOT NULL
);

-- AI budget
CREATE TABLE IF NOT EXISTS ai_budget_day (
  day_utc DATE PRIMARY KEY,
  spent_usd NUMERIC(12,6) NOT NULL,
  in_flight_usd NUMERIC(12,6) NOT NULL,
  updated_at_utc TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS ai_reservations (
  reservation_id UUID PRIMARY KEY,
  day_utc DATE NOT NULL REFERENCES ai_budget_day(day_utc),
  ts_utc_db TIMESTAMPTZ NOT NULL,
  model_key TEXT NOT NULL,
  reserved_usd NUMERIC(12,6) NOT NULL,
  actual_usd NUMERIC(12,6),
  status TEXT NOT NULL CHECK (status IN ('RESERVED','SETTLED','FORCE_SETTLED','RELEASED')),
  correlation_id UUID NOT NULL,
  expires_at_utc TIMESTAMPTZ NOT NULL
);

-- Event log
CREATE TABLE IF NOT EXISTS event_log (
  event_id UUID PRIMARY KEY,
  ts_utc TIMESTAMPTZ NOT NULL,
  type TEXT NOT NULL,
  correlation_ids JSONB NOT NULL,
  payload JSONB NOT NULL,
  payload_hash BYTEA NOT NULL UNIQUE
);

============================================================
26) Runbooks (minimum)
============================================================

/halt
- Immediately sets BARRIER=true and moves to HALTED with durable logging.
- Cancels all open orders using reconcile+REST if needed.

Recover from HALTED
- Investigate mismatch or cause in logs.
- Resolve root cause (WS, DB, clock, budget).
- /unhalt <totp> returns to OBSERVE_ONLY.
- /resume_paper <totp> starts PAPER_TRADING again.

LIVE operation
- Only arm when system healthy (no blockers).
- /arm_live -> step1 -> create arming file -> step2
- Any restart forces OBSERVE_ONLY and requires re-arming.

End of spec.