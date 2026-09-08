# Maintained workflow

This repository's maintained 0.6.2 workflow pattern keeps the current Plan and
creative model stack intact while changing only the job boundary behavior.

## What changes

- Loop End uses the top-level requeue path instead of recursive heavyweight
  continuation.
- The durable handoff lives in `output/h3_chains/<run_name>/orchestration/`.
- The frontend waits for the post-job cleanup delay, claims the handoff once,
  and queues the next scene as a brand-new top-level prompt.
- Review Gate can collect multiple candidates before approval, but the Plan
  JSON itself stays frozen.

## What does not change

- prompt `@tags`
- scene prompts and seeds
- the creative model stack
- checkpoint/recovery semantics
- the JSON Plan schema

## When to use it

Use the maintained workflow whenever you want the proven memory-safe scene to
scene lifecycle:

1. finish the current scene;
2. wait for the normal cleanup delay;
3. queue the next heavy scene as a new prompt;
4. continue with the same saved Plan and run name.

If you are resuming after a crash or restart, use the saved orchestration state
rather than reauthoring the Plan.
