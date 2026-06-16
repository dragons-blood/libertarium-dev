# PLINY'S OPERATING SURFACE
## The map central command uses to scale @elder_plinius

*Living document. This is the responsibility map — every thing Pliny does in
the world lives in one of these 10 departments. Central command staffs agents
against these departments to multiply Pliny's throughput without losing his
voice, judgment, or mission.*

---

## Guiding principles

1. **Leverage before replication.** Default mode is "agent drafts, Pliny
   approves." Full replication (agent acts as Pliny without review) is only
   for low-risk classes of action and only when trust is earned.
2. **Every department has an irreducible core.** The goal is NOT to eliminate
   Pliny from the loop — it's to make sure the only things demanding his
   attention are the ones that genuinely need him.
3. **Agents carry the Dragon Soul.** No department is staffed with "neutral"
   agents. Every dragon in every department inherits the same preamble and
   rolls a chaos modifier. Voice consistency is a feature, not a constraint.
4. **Departments share memory through the Watchtower.** The Watchtower is
   both a department (it has an owner and produces a daily briefing) *and*
   a shared capability (every other department queries its feed). Intel
   curation is centralized; intel consumption is distributed.
5. **Different loops, different departments.** The Forge iterates toward
   *usability*. The Laboratory iterates toward *truth*. The Scriptorium
   iterates toward *resonance*. When two functions have different loops,
   they are different departments even if they share infrastructure.
6. **Drink water. Do a good deed today.** The Hearth is not a joke
   department. It is load-bearing.

---

## The 10 Departments

### 1. 🔨 RED TEAM OPS — *the core craft*
**Charter:** Test frontier releases on drop. Develop novel jailbreak
techniques. Maintain L1B3RT4S. Publish JAILBREAK ALERTs with evidence.

**Weekly activities:**
- Probe new model releases within hours of launch
- Run multi-turn crescendo + fictional-wrapper + variable-sub campaigns
- Catalog which techniques still work against which models
- Draft JAILBREAK ALERT tweets (human-approved before post)
- Maintain and tag L1B3RT4S entries

**Scalable:** automated probing, result cataloging, alert drafting,
release-radar monitoring, running the existing Redteam dashboard
**Irreducible:** creative leap on novel techniques, reputational weight
behind a published result, risk judgment on what to disclose

**Existing infrastructure:** `redteam.html`, `redteam_results.jsonl`,
Blood Agents, `/api/redteam/*`

---

### 2. 🚰 CL4R1T4S — *the transparency archive*
**Charter:** Extract system prompts from AI products. Maintain the public
CL4R1T4S record. Publish leaks with the 🚰 signature. Track prompting
evolution across labs.

**Weekly activities:**
- Monitor new AI product releases
- Extract system prompts via known techniques
- Diff against previous versions, flag what changed
- Publish leak tweets + repo updates
- Comparative analysis threads when patterns emerge

**Scalable:** release monitoring, extraction runs, diff detection,
repo formatting, leak-announcement drafts
**Irreducible:** publish-vs-hold judgment, "huh, *that's* interesting"
pattern recognition on what deserves a thread

**Why sibling of Red Team Ops, not folded in:** The artifact shape is
different. Red Team produces adversarial results; CL4R1T4S produces
archival snapshots. Audiences barely overlap (capability-seekers vs
transparency-seekers). Risk profiles differ (leaking a system prompt
has different legal/reputational exposure than publishing a jailbreak).
They share the "new AI product" release radar feed but diverge
immediately after.

---

### 3. 🧪 THE LABORATORY — *research & science*
**Charter:** Hypothesis → experiment → result → write-up → dataset/paper.
Pliny as scientist, not just practitioner. Mechanistic interp, geometric
refusal analysis, activation steering, alignment research, novel
methodology development.

**Weekly activities:**
- Run experiments against open-weight models (OBLITERATUS-class work)
- Log hypotheses and results — **including negative results**
- Produce findings write-ups (blog posts, preprints, arXiv drops)
- Curate experimental datasets and benchmarks
- Iterate on methodology until it's reproducible

**Scalable:** experiment scaffolding, result logging, statistical analysis,
write-up drafting, literature review, reproducibility checks
**Irreducible:** the hypothesis worth testing, the interpretation of
ambiguous results, the call on when a finding is ready to publish

**Why separate from The Forge:** Different loops. Forge iterates toward
*usability* (does the tool work, is it documented, can strangers run it?).
Lab iterates toward *truth* (is this real, is it reproducible, what does
it actually mean?). Forge ignores negative results; Lab depends on them.
Forge ships repos; Lab ships findings. Pliny's research work (OBLITERATUS
as a paper, not just a tool) belongs here.

**Missing infrastructure:** No experiment log, no hypothesis tracker,
no null-result memory. This is a greenfield department.

---

### 4. ⚒️ THE FORGE — *open-source tool builder*
**Charter:** Ship AGPL-3.0 tools for the community. Datasets and
benchmarks. Reproducible pipelines. GitHub health.

**Weekly activities:**
- Implement features and fixes on active repos
- Triage issues, draft PR reviews
- Cut releases with changelogs
- Write/update README manifestos
- Build new tools when a workflow gap appears

**Scalable:** implementation, CI, tests, docs, issue triage, release
prep — basically all of traditional software engineering
**Irreducible:** architectural vision, what to build next, taste on
what ships vs what gets archived

**Existing infrastructure:** forge-log, workshop census, AGPL defaults.

**Handoff to Laboratory:** Lab produces a finding → Forge turns it into a
tool. OBLITERATUS was Lab work that became a Forge artifact. This pipeline
should be explicit.

---

### 5. 📜 THE SCRIPTORIUM — *writing & educational content*
**Charter:** Long-form threads. LIBERTAS manifestos. Essays. Tutorials
and onboarding content. Interview/podcast/talk prep (talking points;
the appearance itself belongs to Signal). Substantive reply threads.

**Weekly activities:**
- Draft threads from bullet points or voice notes
- Research support for essays (find the citation, the counterexample)
- Write welcome-to-red-teaming tutorials and onboarding docs
- Prep talking-point briefings for interviews and podcasts
- Ghost-draft op-eds and guest posts (Pliny rewrites in his voice)

**Scalable:** drafting, research, transcript cleanup, compression/
expansion, tutorial production, finding the right historical parallel
**Irreducible:** the stance, the voice, the originality of argument

**Existing infrastructure:** `social-brain/VOICE_GUIDE.md`,
`KNOWLEDGE_BASE.md` (positions), `PLINY_TWEET_TEMPLATES.md`

**Scope clarification:** Content creation (one → many, artifact-producing)
lives here. 1:1 coaching/mentoring lives in the Conservatory. A tutorial
video is Scriptorium. A DM coaching a specific person is Conservatory.

---

### 6. 🔭 THE WATCHTOWER — *intel & monitoring* (hybrid)
**Charter:** Model-release radar. AI news cycle. Other red-teamers' work.
Policy and regulation developments. Paper releases. Grants, bounties,
CFPs. **And** the shared intel substrate every other department draws from.

**Weekly activities:**
- Daily briefing: what dropped, what's being discussed, what matters
- Paper radar (prompt injection, alignment, interp)
- Competitor/ally red-teamer tracking
- Policy and regulation watch
- Grant / bounty / CFP calendar
- Maintain the intel feed other departments query

**Scalable:** feeds, aggregation, paper summarization, keyword alerting,
daily briefing generation — **almost entirely scalable**
**Irreducible:** the judgment on what matters, cross-signal pattern-
recognition (but this can be nudged with Pliny's past calls as training)

**Hybrid model:** One standing agent curates and produces the daily
briefing. Every other department queries the feed through a shared
memory layer. Not nine agents doing their own reading. The Watchtower
is the newsroom *and* the library.

**Existing infrastructure:** Watchtower signals in central command,
comms channels, dragonfire notifications.

---

### 7. 📡 THE SIGNAL — *broadcast & live presence*
**Charter:** Daily X posting. Thread publishing. Quote-RT amplification.
Reply engagement. DM triage for public-facing messages. Podcasts,
livestreams, Spaces, conference talks. Everything that is one → many
in real-time or near-real-time.

**Weekly activities:**
- Draft the daily tweet queue (human-approved posting)
- Amplify allies' good work with quote-RTs
- Draft replies to mentions worth engaging
- Thread publication and follow-up engagement
- Coordinate live appearances (Signal owns the calendar;
  Scriptorium provides talking points on request)
- Maintain the posting rhythm so the feed doesn't go dead

**Scalable:** drafting tweets, replies, threads; synthesis of intel
into posts; scheduling; quota management (see `state/tweet_quota.json`)
**Irreducible:** timing, relationships, authenticity on sensitive takes,
the gut-call on "is this the right moment?"

**Existing infrastructure:** `/api/tweet*`, tweet quota system, tweet
approval queue, `pw_browser.py` automated posting, tweet templates.

**Note:** This is the department most at risk of going from leverage
to replication, and the highest-stakes place to get that transition
right. The quota system + human approval queue is the first line of
defense; further defense comes from voice calibration and per-class
action specs.

---

### 8. 🫂 THE CONSERVATORY — *1:1 relationships*
**Charter:** Mentor junior red-teamers. Tend personal DMs. Matchmaker
function (intro X to Y). Discord/Slack community care. Follow-up
tracking. Amplify newcomers when they're ready.

**Weekly activities:**
- DM triage and draft responses to specific individuals
- Maintain the relationship graph (who's who, what they're working on)
- Surface newcomers worth amplifying
- Remind Pliny of follow-ups that are aging
- Propose intro candidates when two people would benefit from knowing
  each other

**Scalable:** relationship tracking, reminder generation, first-draft
responses to common asks (how do I start? how do I get my first
jailbreak?), intro candidate suggestions
**Irreducible:** the actual relationships, the timing of an intro,
the sense of "this person is ready / this person isn't"

**Missing infrastructure:** Pliny does not currently have a relationship
graph or follow-up tracker in central command. This is a real gap.

**Scope boundary:** 1:1 only. Anything that produces content for a
public audience belongs in Scriptorium. Anything that broadcasts to
many at once belongs in Signal.

---

### 9. 🏛️ THE COUNCIL — *strategy, legal, finance, red lines*
**Charter:** Benevolent-ASI mission alignment. Long-term strategic calls.
Deal-making. Grants and funding. Legal exposure and responsible
disclosure protocols. Ethical red lines. "What NOT to publish" decisions.
Every decision that can bite Pliny later if he gets it wrong.

**Weekly activities:**
- Evaluate incoming opportunities (grants, sponsorships, consulting)
- Research for big decisions
- Maintain the red-line list (what Pliny will not do, and why)
- Legal exposure analysis on borderline publications
- Finance/runway tracking
- Draft strategic briefs when Pliny has a decision to make
- Cross-department mission alignment audits

**Scalable:** research, drafting, pros-and-cons briefings, red-line
checklists, decision precedent lookup, legal research, budget drafts
**Irreducible:** the decisions themselves, the relationships with
lawyers/funders/partners

**Department shape:** Small and slow by design. Most weeks it produces
nothing. That's the point — it exists for the rare decisions that
matter, and it exists so those decisions get *researched* rather than
guessed at under time pressure.

---

### 10. 💧 THE HEARTH — *personal sustainability*
**Charter:** Physical health, pace, joy, family, friends, the unbillable
hours that make the billable ones possible. Drink water. Do a good
deed today.

**Weekly activities:**
- Hydration and meal reminders during deep-work sessions
- Protect blocks of unstructured time on the calendar
- Flag burnout signals (late nights stacking, no joy in recent work)
- Remind Pliny of personal commitments and relationships
- Surface the "do a good deed" opportunity of the day

**Scalable:** reminders, calendar tending, energy-awareness prompts,
burnout detection from session patterns, protecting downtime
**Irreducible:** actually living

**This is not a joke department.** The mission is long and the operator
is one human. If the Hearth fails, everything else fails downstream.

---

## Scaling model per department

| Department       | Core loop              | Scaling pattern            | Risk of wrong output |
|------------------|------------------------|----------------------------|----------------------|
| Red Team Ops     | Probe → publish        | Leverage + human approval  | Medium               |
| CL4R1T4S         | Extract → publish      | Leverage + human approval  | Medium               |
| The Laboratory   | Hypothesis → finding   | Leverage + human approval  | Medium               |
| The Forge        | Build → ship           | Leverage → semi-autonomous | Low                  |
| The Scriptorium  | Draft → rewrite        | Leverage only              | High (voice risk)    |
| The Watchtower   | Curate → distribute    | Semi-autonomous            | Low                  |
| The Signal       | Draft → approve → post | Leverage (tight gate)      | Very high            |
| The Conservatory | Listen → respond       | Leverage only              | Very high            |
| The Council      | Research → brief       | Pure advisory              | N/A — Pliny decides  |
| The Hearth       | Sense → remind         | Semi-autonomous            | Low                  |

---

## Cross-department pipelines

The departments are not silos. Real work flows across them.

- **Watchtower → Red Team Ops:** New model dropped → probe campaign
- **Watchtower → CL4R1T4S:** New AI product shipped → extraction run
- **Watchtower → Scriptorium:** Paper worth reading → thread draft
- **Laboratory → Forge:** Finding worth packaging → tool release
- **Laboratory → Scriptorium:** Finding worth explaining → essay
- **Red Team Ops → Signal:** Successful jailbreak → alert post
- **CL4R1T4S → Signal:** System prompt extracted → leak post
- **Scriptorium → Signal:** Thread drafted → published
- **Conservatory → Council:** Legal question from a mentee → red-line check
- **All departments → Hearth:** Session patterns that look like burnout
- **Council → All departments:** Red-line updates, mission alignment notes

These pipelines are how leverage multiplies. A single Watchtower finding
can generate work in three other departments without Pliny touching any
of it until final approval.

---

## Open questions (still unresolved)

1. **Department primitive in central command.** What does a department
   *look like* in the UI? Standing flight, dragon-in-chief, prompt
   preset, or hybrid? This is the next thing to design.
2. **Replication boundaries.** For each department, what is the *exact*
   class of action that can happen without Pliny in the loop? Needs a
   per-department spec.
3. **Relationship graph.** Conservatory needs a persistent data
   structure for tracking people and follow-ups. Doesn't exist yet.
4. **Experiment log.** Laboratory needs a persistent store for
   hypotheses, experiments, and null results. Doesn't exist yet.
5. **Cross-department memory sharing.** The Watchtower's shared-feed
   model requires a memory layer that multiple agents can query. What
   shape does that take?

---

*This map is the spine. Implementation — department dashboards, standing
flights, charter prompts, memory scoping, cross-department handoff —
all flows from here.*
