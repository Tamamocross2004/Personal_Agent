from knowledge.long_probe import coverage


def test_coverage_merges_delivered_intervals_and_respects_document_page():
    fact = {'name': 'manual.pdf', 'page': 36, 'start': 100, 'end': 250}
    hits = [{'name': 'manual.pdf', 'page': 36, 'start': 80, 'end': 180},
            {'name': 'manual.pdf', 'page': 36, 'start': 170, 'end': 280}]
    assert coverage(hits, {'f': fact}) == {'f': True}
    hits[1]['page'] = 35
    assert coverage(hits, {'f': fact}) == {'f': False}
    hits[1]['page'] = 36
    hits[1]['name'] = 'another.pdf'
    assert coverage(hits, {'f': fact}) == {'f': False}


def test_coverage_does_not_bridge_unread_gap_or_truncation():
    fact = {'name': 'a.txt', 'page': 1, 'start': 100, 'end': 250}
    hits = [{'name': 'a.txt', 'page': 1, 'start': 80, 'end': 180},
            {'name': 'a.txt', 'page': 1, 'start': 181, 'end': 280}]
    assert not coverage(hits, {'f': fact})['f']
    assert not coverage(hits[:1], {'f': fact})['f']
    assert coverage(hits, {}) == {}
