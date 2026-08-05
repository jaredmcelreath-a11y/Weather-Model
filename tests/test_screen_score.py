import screen_score as ss


def _cand(ticker, ts, price=0.30, kind="forecast", gap=5.0, hours=10.0,
          yes_bid=None, storm=None):
    row = {"ticker": ticker, "ts": ts, "series": "KXLOWTDEN", "variable": "low",
           "kind": kind, "gap": gap, "price": price, "hours_to_close": hours}
    if yes_bid is not None:
        row["yes_bid"] = yes_bid
    if storm is not None:
        row["storm"] = storm
    return row


def _settled(ticker, result):
    return {"ticker": ticker, "result": result, "settled_at": "2026-08-04T14:00Z"}


# ---- One decision per bracket ---------------------------------------------

def test_the_earliest_flag_is_the_one_scored():
    # A bracket flagged at 12:00 and again at 18:00 is ONE decision, taken at
    # the price you could first have acted on.
    rows = [_cand("A", "2026-08-04T18:00Z", price=0.50),
            _cand("A", "2026-08-04T12:00Z", price=0.30)]
    first = ss.first_flags(rows)
    assert first["A"]["price"] == 0.30


def test_firings_counts_how_long_it_persisted():
    rows = [_cand("A", "2026-08-04T12:00Z"), _cand("A", "2026-08-04T13:00Z"),
            _cand("A", "2026-08-04T14:00Z"), _cand("B", "2026-08-04T12:00Z")]
    first = ss.first_flags(rows)
    assert first["A"]["firings"] == 3
    assert first["B"]["firings"] == 1


# ---- The join --------------------------------------------------------------

def test_an_unsettled_bracket_is_excluded_not_counted_as_a_loss():
    # Today's brackets have no result yet. Treating them as losses would make
    # every fresh firing look like a disaster.
    rows = [_cand("A", "2026-08-04T12:00Z"), _cand("PENDING", "2026-08-04T12:00Z")]
    got = ss.score(rows, [_settled("A", "no")])
    assert [r["ticker"] for r in got] == ["A"]


def test_a_settlement_with_no_candidate_is_ignored():
    # The settled log covers every bracket in 40 cities; only flagged ones count.
    got = ss.score([_cand("A", "t")], [_settled("A", "no"), _settled("Z", "yes")])
    assert len(got) == 1


# ---- Profit arithmetic -----------------------------------------------------

def test_a_winning_fade_pays_the_rest_of_the_dollar():
    # YES ask 0.30 -> the fade costs 0.70 and returns 1.00: +0.30.
    got = ss.score([_cand("A", "t", price=0.30)], [_settled("A", "no")])[0]
    assert got["won"] is True
    assert got["cost"] == 0.70
    assert round(got["pnl"], 4) == 0.30


def test_a_losing_fade_costs_what_it_paid():
    got = ss.score([_cand("A", "t", price=0.30)], [_settled("A", "yes")])[0]
    assert got["won"] is False
    assert round(got["pnl"], 4) == -0.70


def test_the_exact_no_cost_is_used_when_the_bid_was_logged():
    # Buying NO sells against the YES bid, so the real cost is 1 - bid = 0.75,
    # worse than the 0.70 the ask implies. Legacy rows have no bid.
    got = ss.score([_cand("A", "t", price=0.30, yes_bid=0.25)],
                   [_settled("A", "no")])[0]
    assert got["cost"] == 0.75
    assert got["exact"] is True


def test_a_legacy_row_falls_back_to_the_ask_and_says_so():
    got = ss.score([_cand("A", "t", price=0.30)], [_settled("A", "no")])[0]
    assert got["exact"] is False


# ---- The headline ----------------------------------------------------------

def test_edge_is_the_win_rate_less_what_the_market_charged():
    # Two fades at cost 0.70, one wins: hit rate 50%, implied 70% -> -20 points.
    records = ss.score([_cand("A", "t"), _cand("B", "t")],
                       [_settled("A", "no"), _settled("B", "yes")])
    s = ss.summarize(records)
    assert s["n"] == 2
    assert s["hit_rate"] == 0.5
    assert round(s["mean_implied"], 4) == 0.70
    assert round(s["edge"], 4) == -0.20


def test_ev_per_contract_is_the_same_number_as_edge():
    # pnl = 1{won} - cost, so mean pnl IS hit rate - mean cost. Reported as one
    # number in two units, never as two independent findings.
    records = ss.score([_cand("A", "t", price=0.20), _cand("B", "t", price=0.45)],
                       [_settled("A", "no"), _settled("B", "no")])
    s = ss.summarize(records)
    assert round(s["edge"], 6) == round(s["ev_per_contract"], 6)


def test_a_high_hit_rate_against_a_high_price_is_no_edge():
    # THE trap: 83% of all brackets settle NO. Fading at 0.83 and winning 83%
    # of the time is worth exactly nothing, and must not read as a win.
    rows = [_cand(str(i), "t", price=0.17) for i in range(100)]
    settled = [_settled(str(i), "no" if i < 83 else "yes") for i in range(100)]
    s = ss.summarize(ss.score(rows, settled))
    assert s["hit_rate"] == 0.83
    assert abs(s["edge"]) < 1e-9


# ---- Refusing to over-read -------------------------------------------------

def test_a_thin_sample_reports_counts_but_declines_a_verdict():
    records = ss.score([_cand("A", "t")], [_settled("A", "no")])
    s = ss.summarize(records)
    assert s["n"] == 1
    assert s["enough"] is False
    assert s["edge"] is not None          # still computed, just not trusted


def test_the_gate_opens_at_min_sample():
    rows = [_cand(str(i), "t") for i in range(ss.MIN_SAMPLE)]
    settled = [_settled(str(i), "no") for i in range(ss.MIN_SAMPLE)]
    assert ss.summarize(ss.score(rows, settled))["enough"] is True


def test_the_standard_error_shrinks_with_the_sample():
    def se(n):
        rows = [_cand(str(i), "t") for i in range(n)]
        st = [_settled(str(i), "no" if i % 2 else "yes") for i in range(n)]
        return ss.summarize(ss.score(rows, st))["se"]
    assert se(100) < se(16)


def test_summarizing_nothing_does_not_divide_by_zero():
    s = ss.summarize([])
    assert s["n"] == 0
    assert s["edge"] is None
    assert s["enough"] is False


# ---- Splits ----------------------------------------------------------------

def test_records_split_by_kind_without_loss():
    records = ss.score(
        [_cand("A", "t", kind="dead"), _cand("B", "t", kind="forecast"),
         _cand("C", "t", kind="dead")],
        [_settled("A", "no"), _settled("B", "yes"), _settled("C", "no")])
    groups = ss.by_kind(records)
    assert groups["dead"]["n"] == 2
    assert groups["forecast"]["n"] == 1
    assert sum(g["n"] for g in groups.values()) == len(records)


def test_gap_bands_partition_every_record():
    records = ss.score(
        [_cand("A", "t", gap=4.2), _cand("B", "t", gap=6.0),
         _cand("C", "t", gap=15.0)],
        [_settled(t, "no") for t in "ABC"])
    assert sum(g["n"] for g in ss.by_gap(records).values()) == 3


def test_the_base_rate_comes_from_every_settled_bracket():
    # The reference line: what fading a bracket at random would have returned.
    settled = [_settled(str(i), "no" if i < 83 else "yes") for i in range(100)]
    assert ss.base_rate(settled) == 0.83


# ---- Splitting by strength, not raw gap ------------------------------------
# Raw gap is the wrong axis: 4F means ~5.7 sigma on a same-day high and ~2.4 on
# a same-day low, so a gap bucket mixes strong and weak evidence together.

def test_a_record_carries_its_lead_adjusted_strength():
    rows = [_cand("A", "t", gap=4.0, hours=3.0)]
    rows[0]["variable"] = "high"
    got = ss.score(rows, [_settled("A", "no")])[0]
    assert got["strength"] == 1.0


def test_strength_bands_separate_strong_from_weak():
    def row(ticker, gap, hours):
        r = _cand(ticker, "t", gap=gap, hours=hours)
        r["variable"] = "high"
        return r
    records = ss.score(
        [row("weak", 4.0, 36.0), row("strong", 8.0, 3.0)],
        [_settled("weak", "yes"), _settled("strong", "no")])
    bands = ss.by_strength(records)
    assert sum(g["n"] for g in bands.values()) == 2
    # The strong one won and the weak one lost -- the split must show that.
    strong = [g for k, g in bands.items() if k[0] >= 1.0]
    assert strong and strong[0]["hit_rate"] == 1.0


def test_a_record_without_a_lead_still_scores():
    # Strength is unknown, but the trade still happened and still counts.
    rows = [_cand("A", "t", hours=None)]
    got = ss.score(rows, [_settled("A", "no")])[0]
    assert got["strength"] is None
    assert got["won"] is True
