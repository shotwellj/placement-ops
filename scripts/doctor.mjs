#!/usr/bin/env node

/**
 * doctor.mjs
 *
 * Validates that all prerequisites for Placement-Ops are installed and configured.
 * Run with: npm run doctor
 */

import { execSync } from 'child_process';
import { existsSync } from 'fs';
import { resolve } from 'path';

const checks = [];
let allPassed = true;

function check(name, fn) {
  try {
    const result = fn();
    checks.push({ name, status: '✅', detail: result });
  } catch (err) {
    checks.push({ name, status: '❌', detail: err.message });
    allPassed = false;
  }
}

console.log('\n🔍 Placement-Ops Doctor\n');
console.log('Checking prerequisites...\n');

// Node.js version
check('Node.js >= 18', () => {
  const version = process.version;
  const major = parseInt(version.slice(1).split('.')[0]);
  if (major < 18) throw new Error(`Found ${version}, need >= 18`);
  return version;
});

// npm
check('npm installed', () => {
  const version = execSync('npm --version', { encoding: 'utf-8' }).trim();
  return `v${version}`;
});

// Playwright
check('Playwright installed', () => {
  try {
    execSync('npx playwright --version', { encoding: 'utf-8', stdio: 'pipe' });
    return 'installed';
  } catch {
    throw new Error('Run: npm install && npx playwright install chromium');
  }
});

// Chromium browser
check('Chromium browser', () => {
  try {
    execSync('npx playwright install --dry-run chromium', {
      encoding: 'utf-8',
      stdio: 'pipe',
    });
    return 'available';
  } catch {
    throw new Error('Run: npx playwright install chromium');
  }
});

// Profile config
check('Profile configured', () => {
  const path = resolve('config/profile.yml');
  if (!existsSync(path)) {
    throw new Error('Run: cp config/profile.example.yml config/profile.yml');
  }
  return 'config/profile.yml exists';
});

// Portals config
check('Portals configured', () => {
  const path = resolve('config/portals.yml');
  if (!existsSync(path)) {
    throw new Error('Run: cp config/portals.example.yml config/portals.yml');
  }
  return 'config/portals.yml exists';
});

// Data directory
check('Data directory', () => {
  const path = resolve('data');
  if (!existsSync(path)) {
    throw new Error('Missing data/ directory');
  }
  return 'data/ exists';
});

// Output directory
check('Output directory', () => {
  const path = resolve('output');
  if (!existsSync(path)) {
    throw new Error('Missing output/ directory');
  }
  return 'output/ exists';
});

// Claude Code (optional but recommended)
check('Claude Code available', () => {
  try {
    execSync('claude --version', { encoding: 'utf-8', stdio: 'pipe' });
    return 'installed';
  } catch {
    throw new Error('Optional but recommended. Install from docs.anthropic.com');
  }
});

// Print results
console.log('─'.repeat(50));
for (const c of checks) {
  console.log(`${c.status}  ${c.name}: ${c.detail}`);
}
console.log('─'.repeat(50));

if (allPassed) {
  console.log('\n✅ All checks passed! You\'re ready to go.\n');
  console.log('Next steps:');
  console.log('  1. Edit config/profile.yml with your info');
  console.log('  2. Edit config/portals.yml with your target companies');
  console.log('  3. Add candidates to candidates/ folder');
  console.log('  4. Run: claude /placement-ops\n');
} else {
  console.log('\n⚠️  Some checks failed. Fix the issues above and run again.\n');
  process.exit(1);
}
