#!/usr/bin/env node

/**
 * generate-pdf.mjs
 *
 * Takes a populated HTML resume file and converts it to an ATS-optimized PDF
 * using Playwright's Chromium engine.
 *
 * Usage:
 *   node generate-pdf.mjs <input.html> [output.pdf]
 *
 * If no output path is given, saves to output/ with a timestamped filename.
 */

import { chromium } from 'playwright';
import { readFileSync, existsSync, mkdirSync } from 'fs';
import { resolve, basename } from 'path';

const inputPath = process.argv[2];
const outputPath = process.argv[3];

if (!inputPath) {
  console.error('Usage: node generate-pdf.mjs <input.html> [output.pdf]');
  console.error('Example: node generate-pdf.mjs output/jane-smith-resume.html');
  process.exit(1);
}

const resolvedInput = resolve(inputPath);

if (!existsSync(resolvedInput)) {
  console.error(`Error: File not found: ${resolvedInput}`);
  process.exit(1);
}

// Default output path
const outputDir = resolve('output');
if (!existsSync(outputDir)) {
  mkdirSync(outputDir, { recursive: true });
}

const defaultOutput = resolve(
  outputDir,
  basename(inputPath).replace('.html', '.pdf')
);
const resolvedOutput = outputPath ? resolve(outputPath) : defaultOutput;

async function generatePDF() {
  console.log(`\n📄 Generating PDF...`);
  console.log(`   Input:  ${resolvedInput}`);
  console.log(`   Output: ${resolvedOutput}`);

  const browser = await chromium.launch();
  const page = await browser.newPage();

  // Load the HTML file
  const html = readFileSync(resolvedInput, 'utf-8');
  await page.setContent(html, { waitUntil: 'networkidle' });

  // Generate PDF with print-optimized settings
  await page.pdf({
    path: resolvedOutput,
    format: 'Letter',
    margin: {
      top: '0.5in',
      right: '0.6in',
      bottom: '0.5in',
      left: '0.6in',
    },
    printBackground: false,       // No background colors (ATS-friendly)
    preferCSSPageSize: true,
  });

  // Count pages (rough estimate based on file size)
  const { size } = await import('fs').then(fs =>
    fs.promises.stat(resolvedOutput)
  );
  const estimatedPages = Math.max(1, Math.ceil(size / 50000));

  await browser.close();

  console.log(`\n✅ PDF generated successfully!`);
  console.log(`   File: ${resolvedOutput}`);
  console.log(`   Size: ${(size / 1024).toFixed(1)} KB`);
  console.log(`   Estimated pages: ${estimatedPages}`);
  console.log(`\n   Tip: Open the PDF and verify all text is selectable (ATS requirement).`);
}

generatePDF().catch((err) => {
  console.error(`\n❌ PDF generation failed: ${err.message}`);
  console.error(`\n   Common fixes:`);
  console.error(`   - Run: npx playwright install chromium`);
  console.error(`   - Check the HTML file isn't empty`);
  process.exit(1);
});
