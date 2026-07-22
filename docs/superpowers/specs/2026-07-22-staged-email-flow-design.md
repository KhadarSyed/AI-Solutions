# Staged monitoring email flow — corrected sequence & wording

**Date:** 2026-07-22 · **Status:** approved, implemented on `feat/finalize`

## Problem

The staged email conversation read out of order and with tenses that misrepresented the
sequence. Concretely, a task sent a "Task started — I'm **preparing** the monitoring plan
and will email it **shortly**" acknowledgement that arrived **at or after** the Stage 1 Plan
email itself (the pipeline spawns async, so the ack and the plan raced). The plan also said
"Monitoring **begins** for BeOne" while it was still pre-approval, and inter-stage status
pings didn't tie an approval to what happened next.

The user wants to keep every update (receipt + status pings + the 3 gates + report) but in
the right order, with honest wording.

## Design (approved)

Per task, in order:

1. **Receipt** — sent *before* the pipeline spawns, so it always precedes the Plan.
   "Received — Task `<id>` created for `<brand>`. Your monitoring plan follows in this thread
   just below; review it and reply APPROVE to begin. Nothing runs until you approve each stage."
2. **Stage 1 · Plan** — "Here's the plan for `<brand>` — review and reply APPROVE to begin
   collection, or tell me what to change. Nothing runs until you approve." (No "begins".)
3. *user replies* **APPROVE**
4. **Status** — "Plan approved — now collecting `<brand>` + N competitors (`<window>`). I'll
   email the collected set here for your review next."
5. **Stage 2 · Collection KPIs** (+CSV) → APPROVE
6. **Status** — "Collection approved — now tagging N articles …; tagged set follows for review."
7. **Stage 3 · Tagged Results** (+CSV) → APPROVE
8. **Status** — "Tagging approved — now building your dashboard and branded report."
9. **Final report** (dashboard + attachments)

Stepper labels the current gate ("Stage 1 of 3 · Plan").

## Mechanics

- **Ordering guarantee:** `_start_run` sends the receipt (awaited) *before*
  `run_manager.start`, so the receipt is delivered before the plan the pipeline emits. The
  duplicate ack in `handle_inbound` is removed.
- **Honest tense:** nothing says "begins/collecting" until *after* the relevant approval;
  each status states what was approved → what is now running → what's next.
- No pipeline restructure — wording + one ordering change only.

## Non-goals

- Not changing the number of emails (user chose to keep all updates).
- Not changing gate logic, CC/threading, or the self-recovery/self-heal behavior.
