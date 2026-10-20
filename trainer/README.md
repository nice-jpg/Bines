# trainer raw-run adapter

`trainer.raw_run_adapter` exposes the workflow in `src/application/raw_run.py` as a
mind_controller-compatible slave.

TrainingEngine inherits all business assembly from ConductorEngine:

- configured model from `build_model()`;
- common tools with plain operation/captcha notifications;
- user task `执行应用探测任务`;
- session `main`;
- recursion limit `1000`;
- evaluation delegated to `src/application/raw_run.py:eval`.

Unlike the current `raw_run.run()`, the adapter returns the real
training `RunResult` (output, evaluation state and compact summary) instead of only printing it. Before every round it reloads the
conductor_prompt.py binding, so edits made by mind_controller take effect.
`PAGE.md` manuals are already read dynamically by `query_manual`.

If captcha HITL interrupts a task, `run()` keeps the same runtime and session
alive, waits for the exact input `done`, and submits `done` through TrainingChannel and Gateway. AgentCoordinator then selects
`resume_turn()`; ConductorEngine builds the standard `respond` payload. It repeats this process
for consecutive interruptions and returns only after the task is complete.
When called from a master tool node, the adapter invokes the slave in a detached
Python context so LangChain does not classify it as a nested subgraph and bubble
its `GraphInterrupt` into the master workflow.
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

The default evaluator in `src/application/raw_run.py` returns a mind_controller-compatible
structured score with `result_count_grade`, `context_grade`, and
`correctness_grade` dimensions. Merchant/product/error counts are included as
evaluation details.

Runtime customization belongs to an Engine subclass supplied through `engine_factory`;
the adapter does not assemble tools separately. It owns a process-local Gateway for the
duration of a run through the singleton GatewayManager, which generates the ID and supports
other independent Gateways in the same process. The adapter ends output after closing
Coordinator, joins delivery, and releases only its own Gateway. Each Engine owns its
ConversationSession; Coordinator never consumes the evaluation state.

## Configuration initialization

The main thread must initialize `ConfigurationManager` before a training run is
dispatched to a worker. The standard mind_controller model entrypoint does this
and prepares the shared Trace client. A direct main-thread `RawRunSlave.run()`
also initializes the service; a worker call only reads the existing service.
Custom hosts supplying their own master model must initialize configuration on
the main thread before dispatch. See [configuration](../docs/configuration-architecture.md).
