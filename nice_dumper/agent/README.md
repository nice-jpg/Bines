# nice_dumper agent

This directory contains a standalone LangChain-based optimizer loop for XML
captured by `nice_dumper`.

The trusted full dumper is the installed Android launcher:

```bash
adb shell su -c '/data/local/tmp/project -d /sdcard/nice-dumper-agent/full.xml'
adb shell su -c 'cat /sdcard/nice-dumper-agent/full.xml'
```

The agent system does not reimplement UI dumping. It only orchestrates:

1. capture full XML as `XML0`;
2. ask a recognizer sub-agent for function regions `L0`;
3. run `workspace/optimize_xml.py` to produce `XML1`;
4. ask the recognizer for `L1`;
5. score fidelity and compression;
6. ask the main LLM for the next optimizer script;
7. repeat until score growth stalls.

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
