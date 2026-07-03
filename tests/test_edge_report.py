import edge_report

_BUCKETS = [[None, 96, 0.3], [97, 98, 0.6], [99, 100, 0.1]]


def test_settled_bucket_closed_range():
    assert edge_report.settled_bucket(97.0, _BUCKETS) == (97, 98)
    assert edge_report.settled_bucket(98.0, _BUCKETS) == (97, 98)


def test_settled_bucket_open_low_end():
    assert edge_report.settled_bucket(95.0, _BUCKETS) == (None, 96)


def test_settled_bucket_miss_returns_none():
    assert edge_report.settled_bucket(105.0, _BUCKETS) is None


def test_top_bucket():
    assert edge_report.top_bucket(_BUCKETS) == (97, 98)


def test_is_boundary():
    # Kalshi even|odd edges sit at even+0.5 (...94.5, 96.5, 98.5...).
    assert edge_report.is_boundary(96.5) is True          # on the 96|97 edge, dist 0
    assert edge_report.is_boundary(97.0) is True          # 0.5 from 96.5
    assert edge_report.is_boundary(95.4) is False         # 1.1 from 96.5
    assert edge_report.is_boundary(97.6) is False         # 1.1 from 96.5 and 98.5
