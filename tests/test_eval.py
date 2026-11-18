from clipper.eval.gold import Moment, weak_gold_from_clips
from clipper.eval.metrics import chance_precision, precision_at, recall_at


def test_weak_gold_merges_overlapping_clips_and_marks_good():
    spans = [(100, 130, 50), (110, 140, 900), (125, 150, 10), (1000, 1030, 3), (2000, 2030, 2)]
    gold = weak_gold_from_clips(spans, good_quantile=0.8, good_min_clips=3)
    assert len(gold) == 3
    first = gold[0]
    assert (first.start, first.end, first.n, first.weight, first.good) == (100, 150, 3, 960.0, True)
    assert not gold[2].good


def test_metrics():
    gold = [Moment(100, 130, True), Moment(500, 530, True), Moment(900, 930, False)]
    events = [120, 905, 50, 520]
    assert precision_at(events, gold, k=2) == 0.5  # only good moments count when bad ones exist
    assert recall_at(events, gold, k=4) == 1.0
    assert recall_at(events, gold, k=1) == 0.5
    assert 0 < chance_precision(gold, 3600) < 0.1
