from firewall.enrichment.models import Claims, PaperClaim
from firewall.enrichment.scholar import ScholarConnector, _Candidate


def candidate(title, authors, year=2017):
    return _Candidate(title, tuple(authors), year, None, "https://example.org/paper")


class FakeScholar(ScholarConnector):
    def __init__(self, **sources):
        super().__init__()
        self.sources = sources

    def _load(self, name):
        value = self.sources.get(name, [])
        if isinstance(value, Exception):
            raise value
        return value

    def _crossref(self, claim):
        return self._load("crossref")

    def _openalex(self, claim):
        return self._load("openalex")

    def _semantic_scholar(self, claim):
        return self._load("semantic_scholar")

    def _arxiv(self, claim):
        return self._load("arxiv")

    def _dblp(self, claim):
        return self._load("dblp")


def claims(title="Attention Is All You Need", authors=("Vaswani",), year=2017, name="Priya Raman"):
    return Claims(application_name=name, papers=[PaperClaim(title=title, authors=list(authors), year=year)])


def test_matching_title_author_and_year_is_corroborated():
    real = candidate("Attention Is All You Need", ["Ashish Vaswani", "Noam Shazeer"])
    signals = FakeScholar(crossref=[real]).check(claims())
    assert [signal.code for signal in signals] == ["PAPER_CORROBORATED"]
    assert signals[0].polarity == "positive"
    assert signals[0].source == "crossref"
    assert signals[0].confidence >= 0.9


def test_similar_but_different_title_is_not_accepted():
    near_miss = candidate("Is Attention All You Need?", ["Jane Doe"])
    assert FakeScholar(crossref=[near_miss]).check(claims()) == []


def test_matching_title_with_no_claimed_author_is_a_weak_mismatch():
    real = candidate("Attention Is All You Need", ["Ashish Vaswani", "Noam Shazeer"])
    signals = FakeScholar(openalex=[real]).check(claims(authors=("Smith",)))
    assert [signal.code for signal in signals] == ["PAPER_AUTHOR_MISMATCH"]
    assert signals[0].polarity == "negative"
    assert signals[0].confidence <= 0.5


def test_applicant_who_is_a_real_author_is_never_a_mismatch():
    real = candidate("Attention Is All You Need", ["Ashish Vaswani", "Noam Shazeer"])
    signals = FakeScholar(openalex=[real]).check(claims(authors=("Someone Else",), name="Ashish Vaswani"))
    assert signals == []


def test_year_outside_tolerance_is_rejected():
    real = candidate("Attention Is All You Need", ["Ashish Vaswani"], year=2021)
    assert FakeScholar(crossref=[real]).check(claims(year=2017)) == []


def test_chain_falls_through_a_failing_source():
    real = candidate("Attention Is All You Need", ["Ashish Vaswani"])
    signals = FakeScholar(crossref=RuntimeError("timeout"), openalex=[real]).check(claims())
    assert [signal.source for signal in signals] == ["openalex"]


def test_absence_everywhere_is_neutral():
    assert FakeScholar().check(claims()) == []


def test_claim_without_authors_or_title_is_skipped():
    assert FakeScholar().check(Claims(papers=[PaperClaim(title="", authors=["Vaswani"])])) == []
    assert FakeScholar().check(Claims(papers=[PaperClaim(title="A real title", authors=[])])) == []
