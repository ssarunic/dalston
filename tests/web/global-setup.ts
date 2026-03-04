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

  console.log('Seeding test API key...');

  try {
    execSync('python scripts/seed_test_api_key.py', {
      cwd: projectRoot,
      stdio: 'inherit',
    });
    console.log('Test API key seeded successfully');
  } catch (error) {
    console.error('Failed to seed test API key:', error);
    // Don't fail the test run - the key might already exist
  }
}
