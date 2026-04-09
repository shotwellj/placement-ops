# Placement-Ops

**Talent engineering platform for third-party recruiters, built on Claude Code.**

Stop keyword-matching resumes to JDs. Start running a data-driven recruiting desk with taxonomy-driven skill matching, weighted scoring algorithms, and calibration that learns from your placements.

Scan company portals for open reqs → evaluate candidates with structured scoring → generate tailored submission packages → track your pipeline → calibrate from outcomes. The whole desk, engineered.

> Inspired by [career-ops](https://github.com/santifer/career-ops) by Santiago Ferreira — which automates the job search from the *candidate* side. Placement-Ops flips the model for *recruiters* and adds a talent engineering layer.

---

## What Makes This Different

Most recruiting tools do keyword matching: "Does the resume contain the words from the JD?" That's a coin flip.

Placement-Ops uses a **skill taxonomy** — a structured map of 150+ technologies with adjacency relationships. When a JD asks for PyTorch and a candidate has TensorFlow, the system knows those are adjacent ML frameworks and gives partial credit (0.6) instead of marking it as a zero. Every score factors in skill recency, depth of experience, and importance weighting.

The result: fewer false negatives (good candidates you'd have missed), more placements, and scores you can actually explain to clients.

The system also **calibrates over time**. Every placement and rejection gets fed back in. After 10-20 outcomes, it learns things like "Stripe is strict on Spark experience" and "Kubernetes gaps don't actually cause rejections" — turning your tribal knowledge into data.

---

## What It Does

### Core Operations

| Mode | What It Does |
|------|-------------|
| `scan` | Crawls 150+ company career pages for new openings matching your niche |
| `evaluate` | Scores a candidate against a JD using taxonomy-driven matching across 8 dimensions + competency framework |
| `prep` | Generates a tailored resume + interview prep + match analysis |
| `submit` | Creates a polished submission package (cover memo + tailored CV + scorecard) |
| `track` | Maintains your pipeline — every req, candidate, submission, and status |
| `pipeline` | Dashboard view of all active searches and where they stand |
| `batch` | Evaluate multiple candidates against a role in parallel with ranked output |

### Intelligence

| Mode | What It Does |
|------|-------------|
| `research` | Deep-dive on a company — culture, comp data, hiring patterns, interview process |
| `benchmark` | Market scarcity analysis + comp benchmarking for a candidate |
| `forecast` | Predict hiring needs 30-90 days out using signal detection and expansion tracking |

### Optimization

| Mode | What It Does |
|------|-------------|
| `calibrate` | Feed placement/rejection outcomes back to improve matching accuracy |
| `analytics` | Funnel conversion rates, time-to-fill trends, revenue per hour, client scoreboard |
| `retention` | Post-placement tracking with structured check-in cadence and retention health scoring |

### Strategy

| Mode | What It Does |
|------|-------------|
| `strategy` | Client-facing talent strategy reports — org design, hiring sequences, workforce planning |

### Data & Integrations

| Mode | What It Does |
|------|-------------|
| `integrate` | Sync data from ATS (Greenhouse, Lever, Ashby, Merge.dev) and HRIS (BambooHR, Rippling) |
| `market-intel` | Competitive landscape, comp hygiene, posting quality audit, talent flow analysis |

## Quick Start

### Prerequisites

- [Claude Code](https://docs.anthropic.com/en/docs/claude-code) installed and configured
- Node.js 18+
- An Anthropic API key

### Install

```bash
git clone https://github.com/shotwellj/placement-ops.git
cd placement-ops && npm install
npx playwright install chromium

# Validate your setup
npm run doctor
```

### Configure

```bash
# 1. Set up your recruiter profile
cp config/profile.example.yml config/profile.yml
# Edit with your info — name, agency, niche, fee structure

# 2. Set up your target company list
cp config/portals.example.yml config/portals.yml
# Pre-loaded with 150+ Data/ML/AI companies across 14 categories. Add your own.

# 3. Add your first candidate
cp candidates/example.yml candidates/jane-doe.yml
# Fill in their resume, preferences, and target roles.
```

### Run

```bash
# Start the interactive menu
claude /placement-ops

# Or jump straight to a mode
claude /placement-ops scan          # Find new openings
claude /placement-ops evaluate      # Score a candidate (taxonomy-driven)
claude /placement-ops benchmark     # Market scarcity for a candidate
claude /placement-ops pipeline      # See your full pipeline
claude /placement-ops calibrate     # Feed outcomes back
claude /placement-ops analytics     # Funnel metrics + revenue analysis
claude /placement-ops forecast      # Predict hiring needs 30-90 days out
claude /placement-ops retention     # Track post-placement health
claude /placement-ops strategy      # Generate talent strategy for a client
claude /placement-ops integrate     # Sync ATS/HRIS data
claude /placement-ops market-intel  # Competitive landscape + comp hygiene
```

---

## The Matching Engine

The core of Placement-Ops is a structured matching algorithm that replaces gut-feel with reproducible scores.

### How It Works

**1. Extract requirements** from the JD — each skill tagged as required, preferred, or nice-to-have.

**2. Extract candidate skills** from the resume — each tagged with recency (current, recent, dated) and depth (expert, production, project, mentioned).

**3. Taxonomy lookup** — for each requirement, find the best candidate skill:

| Match Type | Score | Example |
|-----------|-------|---------|
| Exact | 1.0 | JD: "PyTorch" → Resume: "PyTorch" |
| Alias | 1.0 | JD: "scikit-learn" → Resume: "sklearn" |
| Adjacent | 0.6 | JD: "PyTorch" → Resume: "TensorFlow" |
| Parent category | 0.3 | JD: "PyTorch" → Resume: "ML frameworks" |
| No match | 0.0 | JD: "Spark" → Resume: nothing related |

**4. Apply multipliers**: recency × depth × importance = final skill score.

**5. Detect blockers**: any required skill with 0.0 and no adjacent match caps the composite at 3.0 (Pass territory).

**6. Output the compatibility matrix** — a visual scorecard:

```
╔══════════════════════════════════════════════════════════════╗
║  COMPATIBILITY MATRIX: Jane Smith → Anthropic Senior MLE    ║
╠══════════════════════════════════════════════════════════════╣
║                                                              ║
║  Technical Match    ████████░░  4.2 / 5.0                   ║
║  Seniority Fit      █████████░  4.5 / 5.0                   ║
║  Location/Remote    ██████████  5.0 / 5.0                   ║
║  Comp Alignment     ███████░░░  3.5 / 5.0                   ║
║  Culture Signals    ██████░░░░  3.0 / 5.0                   ║
║  Gap Severity       ████████░░  4.0 / 5.0                   ║
║  Presentation Risk  ████████░░  4.0 / 5.0                   ║
║  Fill Probability   ███████░░░  3.8 / 5.0                   ║
║                                                              ║
║  COMPOSITE:         ████████░░  4.05 / 5.0  → SUBMIT       ║
║                                                              ║
║  Skill Match:  8 exact | 2 adjacent | 2 gaps | 0 blockers  ║
║  Est. Fee: $39,000                                           ║
╚══════════════════════════════════════════════════════════════╝
```

### The Skill Taxonomy

The file `taxonomy/skills.yml` maps 150+ technologies across 8 categories:

- Programming Languages (Python, R, SQL, Scala, Go, Rust...)
- ML/AI Frameworks (PyTorch, TensorFlow, JAX, scikit-learn, XGBoost...)
- LLM/GenAI (LangChain, RAG, fine-tuning, agent systems, vector DBs...)
- Data Engineering (Spark, Snowflake, dbt, Airflow, Kafka, Delta Lake...)
- MLOps (MLflow, W&B, SageMaker, Vertex AI, feature stores...)
- Cloud/Infra (AWS, GCP, Azure, Docker, Kubernetes, Terraform...)
- Domain Knowledge (RecSys, NLP, CV, fraud detection, time series...)
- Leadership Skills (people management, hiring, architecture, product thinking...)

Each skill has aliases (so "sklearn" matches "scikit-learn"), adjacent skills (so TensorFlow gets partial credit for PyTorch), and a weight (how important it is in Data/ML/AI recruiting).

**Fork and customize for your niche.** The defaults cover Data/ML/AI. Add your own skills, adjacencies, and weights for whatever roles you fill.

### Calibration (The System Gets Smarter)

Every placement and rejection teaches the system something:

```bash
claude /placement-ops calibrate
# "Jane got placed at Anthropic"
# "John got rejected at Stripe — they said not enough Spark"
```

After 5+ outcomes, the system identifies patterns:

- Which "required" skills clients actually reject for vs. just list
- Which adjacency matches convert to placements (TensorFlow → PyTorch = 85% success)
- Company-specific quirks ("Stripe is strict on Spark; Anthropic doesn't care about K8s")
- Whether your scoring threshold is calibrated right

**Your tribal knowledge becomes data.** After 20 placements, the matching engine knows your market better than any keyword matcher ever could.

---

## Project Structure

```
placement-ops/
├── modes/                      # Core skill modes (the brains)
│   ├── _shared.md              # Shared context, scoring rubric, archetypes
│   ├── _matching-engine.md     # The formal matching algorithm
│   ├── scan.md                 # Portal scanning for open reqs
│   ├── evaluate.md             # Taxonomy-driven candidate scoring
│   ├── prep.md                 # Tailored resume + interview prep
│   ├── submit.md               # Submission package generation
│   ├── track.md                # Pipeline tracking and status updates
│   ├── pipeline.md             # Dashboard / overview mode
│   ├── batch.md                # Parallel candidate evaluation
│   ├── research.md             # Company deep-dives
│   ├── benchmark.md            # Market scarcity + comp analysis
│   ├── calibrate.md            # Outcome tracking + pattern learning
│   ├── analytics.md            # Funnel metrics, revenue analytics, client scoreboard
│   ├── forecast.md             # Hiring signal detection + expansion predictions
│   ├── retention.md            # Post-placement tracking + retention health
│   ├── strategy.md             # Client-facing talent strategy + workforce plans
│   ├── integrate.md            # ATS/HRIS data sync + webhook support
│   └── market-intel.md         # Competitive intelligence + comp hygiene
├── taxonomy/
│   ├── skills.yml              # 150+ skills with adjacency relationships
│   └── competencies.yml        # Competency framework (14 competencies, 4 categories)
├── config/
│   ├── integrations.example.yml # ATS/HRIS/data source connection config
│   └── ...
├── ui/
│   ├── dashboard.html          # Agency-side interactive dashboard (16 views)
│   └── people-ops.html         # Company-side internal talent engineering dashboard
├── config/
│   ├── profile.example.yml     # Your recruiter profile template
│   └── portals.example.yml     # 150+ pre-loaded Data/ML/AI companies
├── templates/
│   ├── compatibility-matrix.md # Visual scoring output template
│   ├── submission-memo.md      # Client cover memo template
│   ├── match-scorecard.md      # 8-dimension scorecard template
│   ├── cv-template.html        # ATS-optimized resume template
│   └── states.yml              # Pipeline status + follow-up rules
├── candidates/                 # Candidate profiles (gitignored)
│   └── example.yml             # Example candidate file
├── data/                       # Pipeline + calibration data (gitignored)
├── reports/                    # Evaluation reports (gitignored)
├── output/                     # Generated PDFs and packages (gitignored)
├── scripts/
│   └── doctor.mjs              # Prerequisite checker
├── package.json
└── generate-pdf.mjs            # Playwright PDF generation
```

---

## Philosophy

- **Talent engineering, not keyword matching.** Every score has a formula. Every recommendation has evidence. Every placement makes the next one better.
- **Full lifecycle, not just placement.** Scan → evaluate → place → retain → expand. The system tracks outcomes from first submission through 12-month retention.
- **Strategic, not transactional.** Generate workforce plans, predict hiring needs, and advise on org design. Be the partner, not the vendor.
- **Skills + competencies.** Technical skills tell you what someone knows. Competencies tell you how they work. Both matter for long-term retention.
- **Quality over volume.** Don't submit candidates below 4.0. The system explicitly prevents spray-and-pray.
- **Human in the loop.** AI recommends; you decide. Every submission requires your review.
- **Calibration is the moat.** After 20 placements, your system knows your market. That knowledge compounds.
- **Your data stays local.** Everything runs on your machine. Candidate data never touches a third-party database.
- **Recruiter-friendly.** YAML configs, clear commands, no coding required. If you can follow instructions, you can use this.

---

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). The easiest ways to contribute:

- Add companies to the portal list
- Expand the skill taxonomy for new niches (engineering, product, design, sales)
- Improve the matching engine scoring
- Share anonymized calibration insights

## Credits

- Inspired by [career-ops](https://github.com/santifer/career-ops) by [Santiago Ferreira](https://santifer.io)
- Built with [Claude Code](https://docs.anthropic.com/en/docs/claude-code) by Anthropic

## License

MIT — see [LICENSE](LICENSE)

