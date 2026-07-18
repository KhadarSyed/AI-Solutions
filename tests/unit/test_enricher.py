from app.agents.source_enricher import country_from_tld


def test_cctld_mapping_longest_suffix_wins():
    assert country_from_tld("coolingpost.co.uk") == "GB"
    assert country_from_tld("news.com.au") == "AU"
    assert country_from_tld("spiegel.de") == "DE"
    assert country_from_tld("achrnews.com") is None
