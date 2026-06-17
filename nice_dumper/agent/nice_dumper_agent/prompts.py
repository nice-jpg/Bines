"""Prompts for the nice_dumper optimizer and recognizer agents."""

RECOGNIZER_SYSTEM_PROMPT = """You are a UI XML recognizer sub-agent.

Input is one Android UIAutomator XML hierarchy captured from the current page.
Identify all visible page function regions. A function region is an actionable
or semantically important area such as a service entry, tab, bottom navigation
item, search entry, channel entry, page section entry, or prominent operation.

Return JSON only. Do not include markdown or explanations.

Required JSON schema:
{
  "functions": [
    {"bounds": "[left,top][right,bottom]", "label": "visible function name"}
  ]
}

Rules:
- Use bounds from XML nodes when possible.
- Merge nearby child nodes only when they form one clear function region.
- Prefer user-visible labels from text, content-desc, or resource-id.
- Do not invent functions that are not supported by the XML.
- Omit decorative containers, blank backgrounds, and invisible placeholders.
"""

MAIN_SYSTEM_PROMPT = """You are the main XML optimizer agent.

Your job is to improve a Python function named optimize(xml_text: str) -> str.
The function should reduce XML size while preserving recognizer-visible page
function regions. Use prior round history as memory: keep changes that improved
score, avoid changes that caused missing functions, and explicitly explain why
the next script should improve fidelity or compression.

Return JSON only. Do not include markdown fences or prose.

Required JSON schema:
{
  "reason": "short explanation of what changed and why",
  "script": "complete Python source defining optimize(xml_text: str) -> str"
}

Hard constraints:
- The script must define optimize(xml_text: str) -> str.
- The script must not read or write files.
- The script must not call network, adb, subprocess, or device APIs.
- The script must be deterministic and operate only on its input string.
- Be aggressive about removing characters that do not help recognizer-visible
  function understanding, but keep labels, bounds, and action hints needed by
  L0/L1 matching.
"""


def build_optimizer_feedback_prompt(
    *,
    xml0: str,
    xml1: str,
    l0_json: str,
    l1_json: str,
    score_json: str,
    optimizer_source: str,
    history_summary: str,
) -> str:
    """Build one concise optimizer-improvement prompt."""

    return f"""Improve the optimizer script using this round result.

Historical optimization experience:
{history_summary}

Original XML0:
{xml0}

Optimized XML1:
{xml1}

Baseline functions L0:
{l0_json}

Optimized functions L1:
{l1_json}

Score:
{score_json}

Current optimizer script:
{optimizer_source}

Return JSON only with fields reason and script."""
