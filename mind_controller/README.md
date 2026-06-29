# mind_controller

`mind_controller` is a LangChain master agent that improves another agent
(`slave`) by editing only the prompt files declared by that slave.

The architecture follows `nice_dumper/agent/nice_dumper_agent`: one
`create_agent(...).invoke(...)` owns the complete workflow, while a runtime
exposes stateful tools. The master model performs the optimization loop through
tool calls; there is no separate hard-coded prompt-optimization loop.

## Slave contract

The slave has only two required behavioral interfaces: `run()` and
`eval(result)`. Debug metadata can be passed separately to `run_master`, or
exposed through the optional `debug_info()` convenience method used below.

```python
from pathlib import Path
from mind_controller_agent import SlaveDebugInfo

class MySlave:
    def run(self):
        # Execute one complete task.
        return {"answer": "..."}

    def eval(self, result):
        # The required contract is int.
        return 80

        # A dimension-aware extension is also accepted:
        # return {
        #     "total": 80,
        #     "dimensions": {"accuracy": 90, "efficiency": 70},
        #     "feedback": "missed one edge case",
        # }

    def debug_info(self):
        return SlaveDebugInfo(
            prompt_paths=(Path("my_slave/prompts/system.md"),),
            prompt_structure="system.md contains role, procedure, and output contract",
            responsibility="solve one complete domain task",
            expected_outcome="correct, concise result with all constraints satisfied",
            additional_context={"score_range": [0, 100]},
            working_directory=Path(__file__).resolve().parent,
        )
```

Every prompt path must name an existing UTF-8 file. Relative paths resolve
against `working_directory`. The master cannot read or write undeclared files.
The result passed to `eval` is the exact in-memory object returned by `run`.
Each `run` call must load or otherwise honor the current contents of the
declared prompt files; a slave that permanently caches prompts before the first
round cannot be controlled through this contract.

## Run

Use the repository's Python environment:

```bash
conda activate bines
pip install -r mind_controller/requirements.txt
```

Expose a slave instance or class from an importable module:

```bash
cd /Users/nice/Project/misc/Bines
PYTHONPATH=. python mind_controller/run_master.py \
  --slave my_package.my_slave:MySlave \
  --max-rounds 10 \
  --target-score 95
```

The workflow establishes a baseline, changes one prompt hypothesis at a time,
runs and evaluates the slave again, and stops on the target score, maximum
rounds, or repeated lack of improvement. Prompt snapshots are kept in memory
for the invocation. On every exit path, including model failure, the files are
restored to the best evaluated revision.
