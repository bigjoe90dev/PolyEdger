"""Locked defaults from PolyEdge Automator v2.5 spec §3.

Every constant here corresponds 1:1 to a named value in the spec.
Values MUST NOT be overridden at runtime except through the signed config manifest.
"""

from __future__ import annotations

# ── §3.1  Categories ──────────────────────────────────────────────────────────
ALLOWLIST_CATEGORIES = frozenset({"geopolitics", "economics", "tech/AI"})
DENYLIST_CATEGORIES = frozenset({"sports"})

# ── §3.2  Risk limits (percentage-of-wallet) ──────────────────────────────────
DAILY_STOP_LOSS_PCT = 0.03
MAX_PER_MARKET_PCT = 0.02
MAX_TOTAL_EXPOSURE_PCT = 0.10
MAX_OPEN_POSITIONS = 5

# ── §3.3  AI budget ──────────────────────────────────────────────────────────
AI_CAP_USD_USER = 2.00
AI_CAP_PCT_PER_DAY_DEFAULT = 0.005
AI_WINDOW_SEC = 600
AI_WINDOW_CAP_PCT_OF_DAILY = 0.20
AI_ANALYSES_PER_DAY_HARD_CAP = 100

# ── §3.4  Paper runway ───────────────────────────────────────────────────────
PAPER_RUNWAY_DAYS_MIN = 30
PAPER_FEE_MULTIPLIER = 2.0
PAPER_MIN_FEE_BPS = 10

# ── §3.5  Watchlist and throughput caps ───────────────────────────────────────
WATCHLIST_MAX = 200
PROBATION_MAX = 50
CANDIDATES_PER_MIN_MAX = 50
PER_MARKET_CANDIDATES_PER_MIN_MAX = 10
EVIDENCE_FETCHES_PER_HOUR_MAX = 60

# ── §3.6  WS intervals ───────────────────────────────────────────────────────
FAST_LOOP_SEC = 2
WS_HEARTBEAT_SEC = 10

# Decision freshness
MAX_MARKET_SNAPSHOT_AGE_DECISION_SEC = 6
# Execution freshness
MAX_MARKET_SNAPSHOT_AGE_EXEC_SEC = 3
MAX_DECISION_TO_EXEC_DELAY_SEC = 8
# Candidate max age
CANDIDATE_MAX_AGE_SEC = 120

# Trigger persistence / spoof resistance
TRIGGER_PERSIST_UPDATES = 3
TRIGGER_PERSIST_MIN_SEC = 6

# ── §3.7  Execution guardrails ───────────────────────────────────────────────
RECONCILE_HEARTBEAT_SEC = 60
RECONCILIATION_LAG_SEC = 5
RECONCILE_RETRY_N = 3
RECONCILE_RETRY_BACKOFF_SEC = 2
LIVE_RESIDUAL_CANCEL_AFTER_SEC = 30
MAX_REPLACE_PER_MARKET_PER_MIN = 3
MIN_REPLACE_INTERVAL_SEC = 5

# Locks
LOCK_TTL_SEC = 60
LOCK_RENEW_EVERY_SEC = 10
LOCK_STEAL_GRACE_AFTER_EXPIRY_SEC = 5
MIN_LOCK_TTL_BEFORE_SUBMIT_SEC = 10

# ── §3.8  Arming constants ───────────────────────────────────────────────────
ARMING_WINDOW_SEC = 300
ARMING_NONCE1_TTL_SEC = 120
ARMING_FILE_MAX_AGE_SEC = 900
TOTP_REPLAY_BLOCK_SEC = 60

# ── §3.9  Market quality thresholds ──────────────────────────────────────────
TIME_TO_RESOLUTION_MIN_SEC = 3600
TIME_TO_RESOLUTION_MAX_SEC = 90 * 86400
MIN_VOLUME_24H_USD = 500.0
MIN_LIQUIDITY_USD = 1000.0
MAX_SPREAD_ABS = 0.03
MIN_DEPTH_USD_NEAR_TOP = 50.0
BOOK_LEVELS_REQUIRED = 3

# Binary consistency anomaly
ASK_SUM_LOW = 0.98
ASK_SUM_HIGH = 2.00

# ── §3.10  Clock drift ───────────────────────────────────────────────────────
CLOCK_SKEW_MAX_SEC = 5

# ── §14  Calibration + trust ─────────────────────────────────────────────────
W_AI_MAX = 0.35
N_RESOLVED_MIN = 50
DELTA_MAX_DEFAULT = 0.10
DELTA_MAX_HIGH_DISPUTE = 0.05
P_EFF_OUTLIER_THRESHOLD = 0.20

# ── §15  Decision engine ─────────────────────────────────────────────────────
EV_MIN = 0.01

# ── §16  Risk ─────────────────────────────────────────────────────────────────
MIN_RECONCILE_THRESHOLD_USD = 1.00

# ── Valid durable states §5.1 ─────────────────────────────────────────────────
VALID_STATES = frozenset({
    "OBSERVE_ONLY",
    "PAPER_TRADING",
    "LIVE_ARMED",
    "LIVE_TRADING",
    "HALTED",
    "HALTED_DAILY",
})
