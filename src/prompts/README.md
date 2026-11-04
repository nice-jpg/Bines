# Model-Affine Prompts

Prompt bodies are UTF-8 Markdown files. The directory name is the actual model
identifier, not its configuration group or provider name:

```text
prompts/
  gpt-6.1-sol/
    root.md
    assistant.md
    communication.md
    conductor.md
    collector.md
    page_mechanism/
      meituan/
        PAGE.md
        task.md
  gpt-6.1-astra/
    collector.md
  deepseek-v4.1-flash/
    conductor.md
```

The shipped `gpt-6.1-sol` directory contains the existing instructions and page
mechanisms. The other directories illustrate optional overrides. There is no
`default` prompt directory.

For each requested file, lookup checks the current model, its configured affinity,
then `gpt-6.1-sol`. Overrides replace the entire body of that file. A model directory
can override only one role while the other files fall back independently. An empty
or unreadable override is an error, not a reason to silently ignore the file.

```toml
[model.providers.fast]
provider = "openai"
model = "deepseek-v4.1-flash"
affinity = "gpt-6.1-sol"
base_url = "https://your-provider.example/v1"
api_key = "your-key"
```

`affinity` is a literal model directory name, not `${...}` interpolation, a provider
group, or an API model override. It does not change the model receiving requests.
No recursive affinity chain is followed. If neither model directory contains a
requested file, the baseline supplies it. Prompt names must be single path segments;
path traversal and symlinks escaping this directory are rejected.

## Boundaries

- Configuration stores optional affinity and puts `prompt_model` and
  `prompt_affinity` in model metadata. It does not read prompt files.
- `loader.load_prompt(name, profile)` only resolves and reads files. It does not
  import configuration, model clients, agent classes, or device interfaces.
- Engine loads communication instructions and its role-specific instructions
  when building its graph. Existing agents keep their prompt; new agents read
  current files without Python import caching.
- Root uses `root.md`; Assistant adds `assistant.md`; conductor and collector use
  their corresponding files. All agents add `communication.md`.
- Reco interface instructions and patch grammar remain sourced from their owning
  modules and are composed by the relevant business engine, not duplicated here.
- Page mechanisms live in `<model>/page_mechanism`. The same lookup order selects
  one complete directory, not individual page files. An incomplete override fails
  validation rather than mixing its contracts, guides, and scripts with another
  model's files.
- Conductor selects its mechanism directory when creating its page service.
  Delegated collectors share that service: training edits the exact contracts and
  scripts used by conductor collection. Collector's own role prompt still follows
  its coding model profile. Page notes, contracts, guides, and script edits stay in
  the selected tree, including when that tree was selected through affinity.
- Canonical paths remain application-relative (`meituan/...`); model directory
  names do not enter navigation arguments. Manual digest refresh is unchanged.

There is no model-name branching in business agents and no automatic prompt
rewriting or efficacy scoring. Add a model override only after checking its tool
contracts and behavior against the baseline.
