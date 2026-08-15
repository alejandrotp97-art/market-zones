"""Confidence grading of the regime panel.

The grade answers one question: how much should the user trust the conditional
scenario? Two things decide it — how many INDEPENDENT observations back it, and
whether the excess over the unconditional baseline is distinguishable from zero.

The subtlety that broke this: forward windows overlap, so `n` analogue days at
horizon `h` carry only about `n/h` independent observations. That correction is
right. What was wrong is that the thresholds were written for a RAW count and
never rescaled when the divisor arrived, and the count was taken at 12m while
the interval that qualifies it was taken at 3m. Two different estimates, one
verdict.

These tests drive `_confidence` directly with synthetic scenario tables. No
network, no fetch, no cache.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from regime.dashboard import _confidence


def _row(n_eff, excess, *, elo, ehi, baseline=0.02, evidence=None):
    """A scenario row shaped exactly as `_build` emits it.

    `elo`/`ehi` are the bounds of the EXCESS interval; the row stores them
    re-based, which is how the real table carries them. `evidence` defaults to
    the same reading `_build` derives, and can be pinned to reproduce a row
    where the stored bounds and the badge disagree.
    """
    if evidence is None:
        evidence = "pos" if elo > 0 else ("neg" if ehi < 0 else "flat")
    return {"n_eff": n_eff, "baseline": baseline, "excess": excess,
            "ci_lo": baseline + elo, "ci_hi": baseline + ehi,
            "evidence": evidence}


def _spy(elo, ehi):
    """SPY as it actually measures today: 25 years, regime 'Alcista sano'.

    n_eff comes from the real analogue counts — 1876 days at 3m (/63) and
    1751 at 12m (/252). The 12m figure is the one that used to decide, and it
    is what pinned every asset to 'Baja'.
    """
    return {"1m": _row(90.7, 0.004, elo=-0.005, ehi=0.012),
            "3m": _row(29.8, 0.006, elo=elo, ehi=ehi),
            "6m": _row(14.6, 0.009, elo=-0.020, ehi=0.038),
            "12m": _row(6.9, 0.011, elo=-0.031, ehi=0.052)}


def test_alta_is_reachable_at_all():
    """The bug, stated as a requirement.

    The old rule asked for n_eff(12m) >= 30, i.e. 7560 analogue days inside a
    single regime. SPY holds 6299 rows in its ENTIRE 25-year history, so even a
    market that never once changed regime scored 25 and fell short. 'Alta' was
    not hard to earn, it was unreachable — for every asset, forever.

    Here the sample is ample and the excess is clearly positive, so the grade
    must be 'Alta'. If it is not, the ceiling is still nailed shut.
    """
    conf, _ = _confidence(_spy(elo=0.011, ehi=0.026))
    assert conf == "Alta", f"'Alta' is still unreachable with an ample, clear sample (got {conf})"


def test_ample_sample_without_signal_is_media_not_baja():
    """SPY's real reading: ~30 independent quarters, but the excess interval
    straddles zero ([-1.2%, +1.3%]).

    That is not a data shortage — it is a genuine absence of signal, and the two
    must not be reported with the same word. 'Baja' tells the user to go find
    more history; there is no more history. 'Media' tells the truth: the sample
    is there, the effect is not.
    """
    conf, _ = _confidence(_spy(elo=-0.012, ehi=0.013))
    assert conf == "Media", f"an ample sample with no signal reads as {conf}"


def test_thin_sample_is_still_baja():
    """The fix must not turn 'Baja' into a grade nothing can earn either.

    A regime seen for 30 days carries well under one independent quarter, and
    that IS a data shortage — even when the interval happens to exclude zero.
    """
    thin = {"3m": _row(0.5, 0.05, elo=0.01, ehi=0.09)}
    conf, _ = _confidence(thin)
    assert conf == "Baja", f"half an independent observation graded {conf}"


def test_unstable_sign_blocks_alta():
    """Horizons that disagree on direction cannot produce a confident read,
    however wide the sample. BTC measures exactly this today."""
    s = _spy(elo=0.011, ehi=0.026)
    s["12m"] = _row(6.9, -0.030, elo=-0.055, ehi=-0.004)   # negative excess
    conf, _ = _confidence(s)
    assert conf != "Alta", "horizons pointing opposite ways still graded Alta"


def test_sample_and_interval_are_measured_on_the_same_horizon():
    """The incoherence, isolated.

    The interval is read at 3m, so the sample size must be read at 3m too. Here
    3m is richly sampled while 12m is threadbare; grading on 12m would call this
    thin, but nothing at 12m enters the verdict.
    """
    s = {"3m": _row(24.0, 0.006, elo=0.002, ehi=0.010),
         "12m": _row(0.4, 0.011, elo=-0.031, ehi=0.052)}
    conf, drivers = _confidence(s)
    assert conf == "Alta", f"graded {conf} on a horizon it never tested"
    assert any("3m" in d for d in drivers), f"drivers do not name the horizon used: {drivers}"


def test_falls_back_to_whatever_horizon_survived():
    """When 3m produced no full metrics the shortest surviving horizon decides,
    and it must decide BOTH halves — sample and interval — not one each."""
    s = {"1m": _row(40.0, 0.004, elo=0.001, ehi=0.007)}
    conf, drivers = _confidence(s)
    assert conf == "Alta"
    assert any("1m" in d for d in drivers), drivers


def test_empty_table_is_baja_and_says_so():
    """No horizon cleared the bootstrap floor. The panel must not crash, and
    must not print an effective N for a horizon that reported nothing."""
    conf, drivers = _confidence({})
    assert conf == "Baja"
    assert drivers, "an empty verdict with no explanation"
    assert not any("N efectivo" in d for d in drivers), \
        f"claims an effective N with no horizon behind it: {drivers}"


def test_missing_baseline_cannot_manufacture_confidence():
    """Without a baseline there is no excess to distinguish, so 'Alta' — which
    requires a distinguishable excess — must stay out of reach."""
    s = {"3m": {"n_eff": 30.0, "baseline": None, "excess": None,
                "ci_lo": 0.01, "ci_hi": 0.02, "evidence": None}}
    conf, _ = _confidence(s)
    assert conf != "Alta", "graded Alta with no baseline to measure against"


def test_verdict_agrees_with_the_evidence_badge_on_the_same_row():
    """KO measures exactly this today, and the panel contradicts itself.

    `evidence` is derived in `_build` from the UNROUNDED bootstrap bounds; the
    row then stores those bounds rounded to four decimals. On KO's 3m row the
    upper bound rounds to the baseline exactly (0.0166 both), so recomputing
    `hi < 0` from the stored numbers reads 'flat' while the badge printed beside
    it reads 'neg'. The row is shown as evidence AND described as no evidence.

    The verdict must read the badge, not re-derive it from lossier numbers.
    """
    s = {"3m": _row(32.7, -0.0171, elo=-0.0333, ehi=0.0, baseline=0.0166,
                    evidence="neg")}
    conf, drivers = _confidence(s)
    assert any("Exceso distinguible" in d for d in drivers), \
        f"verdict contradicts the row's own evidence badge: {drivers}"
    assert conf == "Alta", f"ample sample with distinguishable evidence graded {conf}"


def test_drivers_report_the_interval_width():
    conf, drivers = _confidence(_spy(elo=-0.012, ehi=0.013))
    assert any("IC" in d for d in drivers), drivers
    assert any("indistinguible" in d for d in drivers), drivers


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for f in fns:
        f()
        print("PASS", f.__name__)
    print(f"\n{len(fns)} passed")
