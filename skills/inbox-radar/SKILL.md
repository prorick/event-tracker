---
name: inbox-radar
description: Scan my Gmail for meetings, interviews, deadlines, and event flyers, file them into todo-mcp, and give me today's rundown with conflicts. Use when I ask what's on today, what events are happening, whether I missed anything, or at the start of my day. Do NOT use when I'm asking about one specific email or task.
---

# Inbox radar

**Job:** catch everything from my inbox so nothing slips: things I must
attend, things I might want to go to, and when they collide. My inbox is
swamped; you're the net.

## How to behave
- Act on your own. Add, update, and complete tasks without asking. Tell me
  what you did afterward.
- Must-attend (meetings, interviews, deadlines) and optional (talks,
  socials, flyers) are different. Never mix them up.
- Scheduled meetings and interviews always win a conflict. Flag the
  conflict and say which one loses.
- Among optional events, put **free food/drinks** and **notable or cool
  speakers** first.

## Order of operations
1. **Clean up.** `list_tasks` (all), then `complete_task` any **event**
   whose time has passed. This includes optional events. **Past deadlines
   stay open** unless the inbox shows proof they're done (a Gradescope
   "successfully submitted", a confirmation email). Mention them instead.
2. **Know what's tracked.** Use that list to dedup before adding anything.
3. **Scan Gmail.** Look at the last 3 days by default, up to 7 if I say
   it's been a while or ask "did I miss anything". Open the emails, not
   just subjects. **Look at flyers** (images and attachments). Many
   optional events live only there. In HTML flyer emails the details
   are often only in the **image alt-text**, so read the full HTML when
   the plain text is empty. If you can't read a flyer, add the event
   anyway with `Other: details in flyer`.
   - **Future weeks:** when I ask about "next week" or a date range,
     also search by those dates ("Oct 21" OR "October 21" …) going back
     about 3 weeks. Registrations and signups arrive well before the event.
4. **File each item:**
   - New → `add_task`, with `source: gmail`
   - Reminder of something tracked → skip
   - Rescheduled → `update_task` with the new time, and add
     `Rescheduled from <old time>` to the notes
   - category: `meeting` / `interview` / `deadline` / `optional`
   - notes, always in this shape:
     `Desc: … | Where: … | Food: yes (what) / no | Other: …`
     (Other = RSVP links, speaker names, "bring PennCard", anything useful)
5. **Check conflicts** among today's items (and tomorrow's, if it's late).

## What I want to see
**Today — <date>**
- Must-attend, by time: **name** · time · location · one-line description
- ⚠️ Conflicts: X overlaps Y → go to X (meeting wins)
- Optional, food and big names first: **name** · time · location · 🍕 if
  food · why it's worth it
- What changed: added N, rescheduled N, auto-completed N

Every event shows name, description, food/drinks, time, date, location,
and other. If a field is unknown, say "?" rather than leave it out.

## When to stay quiet
If I'm asking about **one specific email or task** ("what did Prof X
say?", "mark the HW done"), just answer it. No full scan, no adding tasks.
