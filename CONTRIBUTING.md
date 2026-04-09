# Contributing to Placement-Ops

Thanks for wanting to contribute! Here's how to get involved.

## How It Works

Placement-Ops is a set of Claude Code skill modes (markdown files in `modes/`) plus configuration templates and a PDF generation script. The "logic" lives in the mode files — they're instructions that Claude Code follows when you run `/placement-ops`.

## What You Can Contribute

**New portals** — Add companies to `config/portals.example.yml` with their careers page URL, ATS type, and scan method. This is the easiest way to contribute.

**Mode improvements** — The mode files in `modes/` contain the evaluation rubric, scanning logic, and prep instructions. If you find a better way to score candidates or structure submissions, open a PR.

**Templates** — Better CV templates, cover memo formats, or scorecard designs. The CV template in `templates/cv-template.html` should be ATS-optimized and single-column.

**Niche expansions** — The default config targets Data/ML/AI roles. Fork and adapt for your niche (engineering, product, design, sales, etc.) and share your keyword lists and portal configs.

**Bug fixes** — The `generate-pdf.mjs` script and `scripts/doctor.mjs` are Node.js. Standard PR process.

## PR Guidelines

1. Keep mode files readable — they're instructions for an AI, not code. Write them like you're briefing a smart colleague.
2. Don't add candidate data or any PII to the repo, even as examples. Use obviously fake data.
3. Test portal URLs before submitting — careers pages change frequently.
4. One feature per PR. Small PRs get reviewed faster.

## Local Development

```bash
git clone https://github.com/YOUR_USERNAME/placement-ops.git
cd placement-ops && npm install
npx playwright install chromium
cp config/profile.example.yml config/profile.yml
cp config/portals.example.yml config/portals.yml
npm run doctor
```

## Code of Conduct

Be respectful. This is a tool for recruiters — we're all trying to make placements and help people find jobs. Keep it professional.

## License

By contributing, you agree that your contributions will be licensed under the MIT License.
