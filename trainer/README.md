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

## Run with mind_controller

```bash
cd /Users/nice/Project/misc/Bines
conda activate bines
PYTHONPATH=. python mind_controller/run_master.py \
  --slave trainer.raw_run_adapter:slave \
  --max-rounds 10
```

The default evaluator in `src/raw_run.py` currently returns the constant score
`50`. Prompt optimization cannot observe improvement until that evaluator is
replaced with a result-sensitive score.

