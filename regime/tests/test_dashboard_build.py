"""Guards for the regime panel builder — the payload contract and the two
traps that a plain reading of the code does not reveal:

  * an unlabelled regime is float NaN, not None, so `is not None` lets it
    through and `bool(nan)` is True;
  * the date column is datetime64[s], so an epoch conversion that assumes
    nanoseconds is off by a factor of a million.
"""
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from regime.dashboard import _expanding_median, _labelled


def test_labelled_rejects_nan_and_none():
    assert _labelled("Alcista sano") is True
    assert _labelled(None) is False
    assert _labelled(float("nan")) is False, "nan is not None -> the trap"
    assert _labelled(np.nan) is False
    assert _labelled("") is False


def test_none_becomes_nan_in_a_dataframe_column():
    """The reason `_labelled` exists at all. If this ever stops holding, the
    helper is harmless — but the assumption is worth pinning down."""
    df = pd.DataFrame({"regime": [None, None, "Lateral"]})
    v = df["regime"].to_numpy()
    assert v[0] is not None or v[0] != v[0], "None survived or became NaN"
    assert not _labelled(v[0]) and _labelled(v[2])


def test_nan_key_is_found_by_identity_in_a_dict():
    """Why the old `==` scan and the new dict lookup disagreed: a dict finds a
    NaN key by identity before it ever compares, so grouping by regime silently
    created a bucket the equality scan could never have matched."""
    nan = float("nan")
    d = {nan: [1, 2, 3]}
    assert d.get(nan) == [1, 2, 3]          # identity hit
    assert (nan == nan) is False            # the comparison the old code used
    assert d.get(float("nan")) is None      # a DIFFERENT nan does not hit


def test_expanding_median_matches_numpy_prefix_by_prefix():
    rng = np.random.default_rng(3)
    v = rng.normal(size=400)
    v[::17] = np.nan                        # gaps must not enter the reference set
    got = _expanding_median(v)
    for t in (5, 50, 199, 399):
        ref = v[: t + 1][np.isfinite(v[: t + 1])]
        assert abs(got[t] - float(np.median(ref))) < 1e-12, f"mismatch at t={t}"


def test_epoch_ms_survives_second_resolution():
    """`fetch_daily` returns datetime64[s, UTC]. `astype("int64") // 10**6` on
    that yields 1083 instead of 1083677400000."""
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    from dashboard import _epoch_ms
    idx = pd.to_datetime([1083677400, 1083763800], unit="s", utc=True)
    s = pd.Series(idx)
    assert str(s.dtype).startswith("datetime64[s"), "fixture must be second-resolution"
    got = _epoch_ms(s)
    assert list(got) == [1083677400000, 1083763800000]
    # and nanosecond input must give the same answer
    assert list(_epoch_ms(pd.Series(idx.as_unit("ns")))) == [1083677400000, 1083763800000]


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for f in fns:
        f()
        print("PASS", f.__name__)
    print(f"\n{len(fns)} passed")
