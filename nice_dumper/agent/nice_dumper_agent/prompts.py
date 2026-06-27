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
- Never assume a later sibling hides an earlier sibling merely because their
  bounds overlap. XML order and bounds do not expose alpha or actual drawing.
- Omit an entire subtree when its root has visible-to-user="false".
- Legacy XML may not contain visible-to-user. Treat a subtree as an inactive,
  preloaded pull-down layer only when all of these signals agree: its resource
  id identifies a pull-loading/preloaded container, it covers at least 95% of
  its parent, it has no clickable, long-clickable, or scrollable descendants,
  and a sibling in the same area contains multiple actionable descendants.
  Do not recognize labels or function regions from that inactive subtree.
- An explicit visible-to-user="true" overrides the legacy inactive-layer
  heuristic.
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

Runtime architecture:
- There is exactly one complete main-agent invocation. Do all iteration by
  calling tools inside this run.
- Manage recognizer as a subagent yourself with spawn, call, and kill.
- Use dump_full_xml once to create XML0.
- Immediately call analyze_hidden_subtrees on XML0. Treat its result as the
  highest-priority structural optimization opportunity and retain its evidence
  across all rounds.
- Use call on the recognizer subagent to create L0 and each later L result.
- Use optimize_xml, get_optimizer_source, and apply_optimizer for every round.
  Use recognizer and score_round only when optimize_xml returns ok=true.
- Inspect the result of every optimize_xml call. If ok=false, the runtime has
  supervised an optimizer load or execution failure and already assigned score
  -1000. Read stage, error_type, and error, skip recognizer and score_round for
  that failed XML, then use its score_ref with apply_optimizer to install a
  corrected complete script. Explain the concrete failure in the reason.
- Call should_stop after each applied proposal and stop when it says stop=true.
- apply_optimizer requires a reason and a complete script; the reason is stored
  in workspace git history for traceability.

Optimization priority:
1. First implement deterministic pruning for maximal hidden subtree candidates
   reported by analyze_hidden_subtrees. Removing one inactive subtree is more
   valuable than repeatedly trimming isolated attributes.
2. For visible-to-user="false", prune the subtree directly.
3. For legacy XML without visibility, prune a pull-loading/preloaded subtree
   only when all evidence agrees: matching resource-id semantics, at least 95%
   parent coverage, zero clickable/long-clickable/scrollable descendants, and
   an overlapping sibling with multiple actionable descendants.
4. Preserve the active sibling (for the known page this is the t5f subtree).
   Never implement the invalid rule that a later sibling covering an earlier
   sibling is automatically visible or occluding.
5. After structural pruning is implemented and fidelity remains intact, pursue
   generic attribute and syntax compression.

Scoring includes a separate hidden-pruning dimension:
- hidden_subtree_count counts maximal hidden subtree roots.
- hidden_candidate_count counts every node inside those subtrees.
- hidden_removed_count counts hidden nodes no longer present in optimized XML.
- hidden_pruning ranges from 0 to 1 using node-level bounds, label, resource-id,
  and class matching.
- Fully retaining all hidden nodes receives 0 structural reward.
- Fully removing every node in the hidden subtrees receives +30 score points.
- Removing only a root marker while retaining descendants receives little or
  partial credit, so implement actual recursive subtree pruning.
- This reward is independent of ordinary character compression and is intended
  to justify a carefully bounded structural attempt.

Every proposal reason must state which reported structural candidate it handles,
why that candidate is hidden, and the approximate characters expected to be
removed. If the current optimizer does not yet implement candidate pruning,
do not spend the next round only on generic attribute removal.

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
