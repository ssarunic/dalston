/**
 * Playwright global setup - seeds the test database with the expected API key.
 *
 * This runs before all tests and ensures the hardcoded test API key exists
 * in the database.
 */

import { execSync } from 'child_process';
import path from 'path';

export default async function globalSetup() {
  const projectRoot = path.resolve(__dirname, '../..');
  const pythonCandidates = [
    process.env.PYTHON_BIN,
    path.join(projectRoot, '.venv', 'bin', 'python'),
    'python3',
    'python',
  ].filter(Boolean) as string[];

  console.log('Seeding test API key...');

  for (const pythonBin of pythonCandidates) {
    try {
      execSync(`${pythonBin} scripts/seed_test_api_key.py`, {
        cwd: projectRoot,
        stdio: 'inherit',
      });
      console.log(`Test API key seeded successfully (${pythonBin})`);
      return;
    } catch (error) {
      console.warn(`Seeding failed with ${pythonBin}:`, error);
    }
  }

  // Don't fail the test run - the key might already exist.
  console.error('Failed to seed test API key with available Python executables');
}
