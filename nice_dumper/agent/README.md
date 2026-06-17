# nice_dumper agent

This directory contains a standalone LangChain-based optimizer loop for XML
captured by `nice_dumper`.

The trusted full dumper is the installed Android launcher:

```bash
adb shell su -c '/data/local/tmp/project -d /sdcard/nice-dumper-agent/full.xml'
adb shell su -c 'cat /sdcard/nice-dumper-agent/full.xml'
```

The agent system does not reimplement UI dumping. The main workflow is one
complete `create_agent(...).invoke(...)`; iteration happens inside LangChain's
tool-calling run loop, with middleware injecting compact state before each model
step. It orchestrates:

1. capture full XML as `XML0`;
2. spawn a recognizer sub-agent with the `spawn` tool;
3. call the recognizer sub-agent with `call` for function regions `L0`;
4. run `workspace/optimize_xml.py` through `optimize_xml` to produce `XML1`;
5. call the recognizer sub-agent again for `L1`;
6. score fidelity and compression with `score_round`;
7. apply a reasoned script proposal with `apply_optimizer`;
8. repeat until `should_stop` says to stop, then `kill` the sub-agent.

The sub-agent lifecycle tools exposed to the main agent are:

```text
spawn(role="recognizer") -> subagent_id
call(subagent_id, xml_ref) -> recognition_ref
kill(subagent_id) -> killed
```

Every optimizer change is committed under `workspace/.git` with a round report
in `workspace/rounds/`.

## Install

```bash
cd /Users/nice/Project/misc/Bines/nice_dumper/agent
python -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
```

The runner accepts either an explicit LangChain model string or OpenAI-compatible
environment variables.

```bash
export OPENAI_API_KEY=...
export OPENAI_BASE_URL=...
export OPENAI_MODEL=...
```

DeepSeek-style names are also accepted:

```bash
export DEEPSEEK_API_KEY=...
export DEEPSEEK_BASE_URL=...
export DEEPSEEK_MODEL=...
```

## Run

Install or refresh the dumper first:

```bash
cd /Users/nice/Project/misc/Bines/nice_dumper
scripts/install.sh
```

Then run the optimizer:

```bash
cd /Users/nice/Project/misc/Bines/nice_dumper/agent
python run_optimizer.py --max-rounds 3
```

Useful options:

```text
--adb <path>                    adb executable. Default: adb
--remote-output <path>          device XML path. Default: /sdcard/nice-dumper-agent/full.xml
--timeout-ms <n>                dumper timeout. Default: 15000
--max-rounds <n>                optimization rounds. Default: 10
--output <path>                 final optimizer script path
--model <name>                  LangChain model string or OpenAI-compatible model name
--fixture-xml <path>            skip adb and use a local XML file
```

The final artifact is:

```text
workspace/optimize_xml.py
```

## Output Contract

The recognizer sub-agent must return JSON only:

```json
{
  "functions": [
    {"bounds": "[645,586][854,808]", "label": "外卖"}
  ]
}
```

`bounds` must use `[left,top][right,bottom]`. `label` should be the visible
function name, such as a service entry, tab, navigation item, search entry, or
channel entry.
