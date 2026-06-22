from nice_dumper_agent.main_agent import parse_optimizer_proposal


def test_parse_optimizer_proposal_reads_reason_and_script() -> None:
    raw = '{"reason":"drop unused attributes","script":"def optimize(xml_text: str) -> str:\\n    return xml_text.strip()"}'

    proposal = parse_optimizer_proposal(raw, "fallback")

    assert proposal.reason == "drop unused attributes"
    assert "return xml_text.strip()" in proposal.source
