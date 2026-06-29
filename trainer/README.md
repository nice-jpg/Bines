# trainer raw-run adapter

`trainer.raw_run_adapter` exposes the workflow in `src/raw_run.py` as a
mind_controller-compatible slave.

It preserves the original runtime configuration:

- Codex model from `build_codex_model()`;
- common tools with plain operation/captcha notifications;
- user task `执行应用探测任务`;
- session `main`;
- recursion limit `1000`;
- evaluation delegated to `src/raw_run.py:eval`.

Unlike the current `raw_run.run()`, the adapter returns the real
`AgentRunResult` instead of only printing it. Before every round it reloads the
global system prompt binding, so edits made by mind_controller take effect.
`PAGE.md` manuals are already read dynamically by `query_manual`.

If captcha HITL interrupts a task, `run()` keeps the same runtime and session
alive, waits for the exact input `done`, and calls `resume_turn()` with the
standard `respond` resume path owned by `AgentRuntime`. It repeats this process
for consecutive interruptions and returns only after the task is complete.
Non-interactive callers must inject a blocking input source:

```python
slave = RawRunSlave(
    human_input_provider=lambda interrupted_result: wait_for_external_done()
)
```

## Run with mind_controller

```bash
cd /Users/nice/Project/misc/Bines
conda activate bines
PYTHONPATH=. python mind_controller/run_master.py \
  --slave trainer.raw_run_adapter:slave \
  --max-rounds 10
```

The default evaluator in `src/raw_run.py` returns a mind_controller-compatible
structured score with `result_count_grade`, `context_grade`, and
`correctness_grade` dimensions. Merchant/product/error counts are included as
evaluation details.
