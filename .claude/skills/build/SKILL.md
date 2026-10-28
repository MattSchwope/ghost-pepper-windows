---
name: build
description: Build one phase of docs/BUILD_PLAN.md end to end, from branch to pull request.
argument-hint: "[phase number, e.g. 0, 1, 1b]"
disable-model-invocation: true
---

Build phase **$ARGUMENTS** of `docs/BUILD_PLAN.md`. If no phase was given, pick the first phase whose "Done when" list still has unchecked boxes. Skip Phase 1b unless Phase 1 latency logs show a median above 1.5 seconds.

1. **Load context.** Read `docs/BUILD_PLAN.md` (the phase section plus Decisions, Architecture and Models) and the ADRs in `docs/decisions/`. Read the existing code you will touch before changing it.
2. **Branch.** `git checkout main`, `git pull`, then `git checkout -b phase-<n>`.
3. **Plan.** Make a task list from the phase's Scope bullets and "Done when" boxes. If anything in the plan is ambiguous or conflicts with the code, ask Matt one question before writing code.
4. **Build.** Implement the scope. Small modules, typed Python, clear errors. Port rather than rewrite when `reference/transcribe/` already has working code.
5. **Test.** Add tests for every pure function. Mock all OpenAI calls. Run the full suite and fix failures until it is green.
6. **Check the boxes.** For each "Done when" item:
   - Automatable: prove it with a test or a command, then tick it `[x]` in `docs/BUILD_PLAN.md`.
   - Needs Matt (real mic, real apps, real API key, his clips): leave it unticked and add it to the manual checklist in step 8.
7. **Ship.** Update `README.md` if commands changed. Commit with a clear message, push the branch, and open a PR titled `Phase <n>: <phase name>` with `gh pr create`. If `gh` is missing, push and give Matt the compare link.
8. **Report** in this shape and nothing more:
   - **Built:** 3 to 6 bullets.
   - **Test it yourself:** numbered PowerShell commands Matt can paste, ending with what he should see.
   - **Needs you:** unticked boxes and anything blocked.
   - **Next:** `/build <next phase>`.

Rules: stay inside this phase's scope. Note good ideas for later phases under "Open questions" in the plan instead of building them. Never commit `.env`, audio, notes or eval clips.
