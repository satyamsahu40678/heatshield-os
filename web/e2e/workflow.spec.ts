import { expect, test, type Page, type Response } from '@playwright/test';
import type { DemoWorkspace } from '../src/types';

const DEMO_PASSWORD = 'HeatShieldDemo2026!';

async function signIn(page: Page, email: string) {
  await page.goto('/');
  await page.getByLabel('Email', { exact: true }).fill(email);
  await page.getByLabel('Password', { exact: true }).fill(DEMO_PASSWORD);
  const login = page.waitForResponse(
    (response) => response.url().endsWith('/api/v1/auth/login')
      && response.request().method() === 'POST',
  );
  await page.getByRole('button', { name: 'Sign in', exact: true }).click();
  expect((await login).status()).toBe(200);
  await expect(page.getByRole('button', { name: 'Sign out', exact: true })).toBeVisible();
}

async function expectMutation(page: Page, suffix: string, action: () => Promise<unknown>) {
  const completed = page.waitForResponse(
    (response) => response.url().endsWith(suffix)
      && response.request().method() === 'POST',
  );
  await action();
  const response = await completed;
  expect(response.ok(), await response.text()).toBeTruthy();
  return response;
}

async function completedOptimization(response: Response) {
  if (!response.url().includes('/api/v1/optimization-runs/') || !response.ok()) return false;
  const run = await response.json();
  return run.status === 'COMPLETED';
}

test('the seeded command center presents real plans, evidence and human follow-up', async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 1440, height: 1050 });
  const workspaceResponse = page.waitForResponse(response => response.url().endsWith('/api/v1/demo/workspace') && response.ok());
  await signIn(page, 'safety@example.test');
  const workspace = await (await workspaceResponse).json() as DemoWorkspace;
  await expect(page.getByRole('heading', { name: workspace.worksite.name, exact: true })).toBeVisible();
  expect(workspace.scenarios.length).toBeGreaterThanOrEqual(6);
  await expect(page.locator('.scenario-operation-card')).toHaveCount(workspace.scenarios.length);
  const workerMetric = page.locator('.operation-metric').filter({ has: page.getByText('Workers in the workspace', { exact: true }) });
  await expect(workerMetric.locator('strong')).toHaveText(String(workspace.metrics.workers));
  const recoveryMetric = page.locator('.operation-metric').filter({ has: page.getByText('Protected recovery', { exact: true }) });
  await expect(recoveryMetric.locator('strong')).toHaveText(String(workspace.metrics.protected_recovery_minutes));
  await expect(page.getByRole('img', { name: /WBGT observations/ })).toBeVisible();
  await expect(page.getByText('Not recorded', { exact: true })).toBeVisible();
  await page.screenshot({ path: testInfo.outputPath('operations-dashboard.png'), fullPage: true, animations: 'disabled' });
  await page.setViewportSize({ width: 390, height: 844 });
  const mobileWidth = await page.evaluate(() => ({ content: document.documentElement.scrollWidth, viewport: window.innerWidth }));
  expect(mobileWidth.content).toBeLessThanOrEqual(mobileWidth.viewport);
  await page.screenshot({ path: testInfo.outputPath('operations-dashboard-mobile.png'), fullPage: true, animations: 'disabled' });
  await page.setViewportSize({ width: 1440, height: 1050 });
  const missingEvidence = workspace.scenarios.find(scenario => scenario.status === 'REVIEW_REQUIRED');
  expect(missingEvidence).toBeTruthy();
  await page.getByLabel('Selected shift', { exact: true }).selectOption(missingEvidence!.id);
  await expect(page.getByText('No WBGT observations for this shift', { exact: true })).toBeVisible();
  await expect(page.getByText('No admissible proposal — review inputs', { exact: true })).toBeVisible();
  const reviewReady = workspace.scenarios.find(scenario => scenario.status === 'OPTIMIZED');
  expect(reviewReady).toBeTruthy();
  await page.getByLabel('Selected shift', { exact: true }).selectOption(reviewReady!.id);
  await expect(page.getByRole('img', { name: new RegExp(`WBGT observations for ${reviewReady!.name}`) })).toBeVisible();
  await page.getByRole('button', { name: reviewReady!.name, exact: true }).click();
  await expect(page.getByRole('heading', { name: reviewReady!.name, exact: true })).toBeVisible();
  await expect(page.getByText('Checks passed', { exact: true }).first()).toBeVisible();
  await page.getByRole('button', { name: 'Comparison', exact: true }).click();
  await expect(page.getByText('Fixed roster', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Overview', exact: true }).click();
  await page.getByRole('button', { name: 'Open the intervention log', exact: true }).click();
  await expect(page.getByText(/active stop-work hold/)).toBeVisible();
  await expect(page.getByLabel('Verification note', { exact: true }).first()).toBeVisible();
  expect(JSON.stringify(workspace)).not.toContain('"symptoms"');
  expect(JSON.stringify(workspace)).not.toContain('"severity"');
});

test('a reviewed demo plan is published, acknowledged and independently verified', async ({ page }, testInfo) => {
  test.setTimeout(90_000);
  const scenarioName = `Browser workflow ${Date.now()}`;
  await signIn(page, 'safety@example.test');
  await expect(page.getByText('Synthetic demo', { exact: true }).first()).toBeVisible();

  await page.getByRole('button', { name: 'New scenario', exact: true }).click();
  const fixture = page.waitForResponse((response) => response.url().endsWith('/api/v1/demo/scenario'));
  await page.getByRole('button', { name: 'Load demo fixture', exact: true }).click();
  expect((await fixture).ok()).toBeTruthy();
  await page.getByLabel('Scenario name', { exact: true }).fill(scenarioName);
  const created = await expectMutation(page, '/api/v1/scenarios', () =>
    page.getByRole('button', { name: 'Create scenario', exact: true }).click(),
  );
  const scenario = await created.json();
  expect(scenario.id).toBeTruthy();
  await expect(page.getByRole('heading', { name: scenarioName, exact: true })).toBeVisible();

  const optimized = page.waitForResponse(completedOptimization, { timeout: 45_000 });
  const queued = await expectMutation(page, `/api/v1/scenarios/${scenario.id}/optimize`, () =>
    page.getByRole('button', { name: 'Generate plan', exact: true }).click(),
  );
  expect(queued.status()).toBe(202);
  const run = await (await optimized).json();
  expect(['OPTIMAL', 'FEASIBLE']).toContain(run.solver_status);
  expect(run.result.validation.valid).toBe(true);
  expect(run.result.validation.operational_approval_allowed).toBe(false);
  await expect(page.getByText('Plan generated', { exact: true })).toBeVisible();
  await expect(page.getByText('Checks passed', { exact: true }).first()).toBeVisible();

  await page.getByRole('button', { name: 'Comparison', exact: true }).click();
  await expect(page.getByText(/Fixed roster/i).first()).toBeVisible();
  await expect(page.getByText(/Greedy/i).first()).toBeVisible();
  await expect(page.getByText(/CP-SAT/i).first()).toBeVisible();
  await page.getByRole('button', { name: 'Timeline', exact: true }).click();
  await page.screenshot({ path: testInfo.outputPath('supervisor-plan.png'), fullPage: true, animations: 'disabled' });

  await expectMutation(page, `/api/v1/scenarios/${scenario.id}/approve`, () =>
    page.getByRole('button', { name: 'Record demo review', exact: true }).click(),
  );
  const published = await expectMutation(page, `/api/v1/scenarios/${scenario.id}/publish`, () =>
    page.getByRole('button', { name: 'Publish demo', exact: true }).click(),
  );
  expect((await published.json()).published_at).toBeTruthy();

  await page.getByRole('button', { name: 'Sign out', exact: true }).click();
  await signIn(page, 'worker@example.test');
  await expect(page.getByRole('button', { name: 'Overview', exact: true })).toHaveCount(0);
  await expect(page.getByRole('button', { name: 'New scenario', exact: true })).toHaveCount(0);
  const personalSchedule = page.getByRole('region', { name: scenarioName, exact: true });
  await expect(personalSchedule).toBeVisible();
  const acknowledged = await expectMutation(page, '/acknowledge', () =>
    personalSchedule.getByRole('button', { name: 'Acknowledge', exact: true }).first().click(),
  );
  const intervention = await acknowledged.json();
  expect(intervention.id).toBeTruthy();
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({ path: testInfo.outputPath('worker-mobile-published.png'), fullPage: true, animations: 'disabled' });
  await page.setViewportSize({ width: 1280, height: 720 });

  await page.getByRole('button', { name: 'Sign out', exact: true }).click();
  await signIn(page, 'supervisor@example.test');
  await page.getByRole('button', { name: 'Interventions', exact: true }).click();
  const interventionCard = page.getByTestId(`intervention-${intervention.id}`);
  await expect(interventionCard).toBeVisible();
  await interventionCard.getByLabel('Verification note', { exact: true })
    .fill('Browser test: protected recovery confirmed in this synthetic demonstration.');
  await expectMutation(page, '/verify', () =>
    interventionCard.getByRole('button', { name: 'Verify intervention', exact: true }).click(),
  );
  await expect(interventionCard.getByText('Verified', { exact: true })).toBeVisible();

  await page.getByRole('button', { name: 'Audit', exact: true }).click();
  await expect(page.getByRole('heading', { name: /Audit/i })).toBeVisible();
  await expect(page.getByText(/intervention[._ ]verified/i).first()).toBeVisible();

  await page.getByRole('button', { name: 'Sign out', exact: true }).click();
  await signIn(page, 'safety@example.test');
  await page.getByRole('button', { name: scenarioName, exact: false }).first().click();
  await page.getByRole('button', { name: 'Withdraw demo', exact: true }).click();
  await page.getByLabel('Withdrawal note', { exact: true })
    .fill('Synthetic demonstration complete; withdraw the active plan and retain its history.');
  const withdrawn = await expectMutation(page, `/api/v1/scenarios/${scenario.id}/withdraw`, () =>
    page.getByRole('button', { name: 'Confirm withdrawal', exact: true }).click(),
  );
  expect((await withdrawn.json()).status).toBe('WITHDRAWN');
  await expect(page.getByRole('heading', { name: 'Demo plan withdrawn', exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Sign out', exact: true }).click();
  await signIn(page, 'worker@example.test');
  await expect(page.getByRole('region', { name: scenarioName, exact: true })).toHaveCount(0);
  await expect(page.getByRole('heading', { name: 'My schedule', exact: true })).toBeVisible();
});

test('worker portal fits a narrow screen and exposes no planning controls', async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await signIn(page, 'worker@example.test');
  await expect(page.getByRole('heading', { name: /My schedule/i })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Generate plan', exact: true })).toHaveCount(0);
  await expect(page.getByRole('button', { name: 'Record demo review', exact: true })).toHaveCount(0);
  const width = await page.evaluate(() => ({
    content: document.documentElement.scrollWidth,
    viewport: window.innerWidth,
  }));
  expect(width.content).toBeLessThanOrEqual(width.viewport);
  await page.screenshot({ path: testInfo.outputPath('worker-mobile.png'), fullPage: true, animations: 'disabled' });
});

test('an invalid sign-in shows a recoverable error', async ({ page }) => {
  await page.goto('/');
  await page.getByLabel('Email', { exact: true }).fill('safety@example.test');
  await page.getByLabel('Password', { exact: true }).fill('incorrect-password');
  await page.getByRole('button', { name: 'Sign in', exact: true }).click();
  await expect(page.getByRole('alert')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Sign in', exact: true })).toBeEnabled();
});

test('a severe private report reaches a human-owned hold without exposing symptoms to staff', async ({ page }) => {
  await signIn(page, 'worker@example.test');
  await page.getByRole('button', { name: 'Report symptoms', exact: true }).first().click();
  await page.getByLabel('Confusion', { exact: true }).check();
  await page.getByLabel('Reported severity', { exact: true }).selectOption('severe');
  const report = await expectMutation(page, '/api/v1/workers/me/symptoms', () =>
    page.getByRole('button', { name: 'Submit private report', exact: true }).click(),
  );
  const result = await report.json();
  expect(result.stop_work).toBe(true);
  expect(result.dispatch_performed).toBe(false);
  await expect(page.getByText('No emergency dispatch was performed.', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Return to my schedule', exact: true }).click();
  await expect(page.getByRole('alert')).toContainText('Stop work');

  try {
    await page.getByRole('button', { name: 'Sign out', exact: true }).click();
    await signIn(page, 'supervisor@example.test');
    await page.getByRole('button', { name: 'Interventions', exact: true }).click();
    await expect(page.getByText(/active stop-work hold/)).toBeVisible();
    await expect(page.getByText('Confusion', { exact: true })).toHaveCount(0);
    await expect(page.getByRole('button', { name: 'Release demo hold', exact: true })).toHaveCount(0);
    await page.getByRole('button', { name: 'Sign out', exact: true }).click();
    await signIn(page, 'safety@example.test');
    await page.getByRole('button', { name: 'Interventions', exact: true }).click();
    const hold = page.getByTestId('hold-worker-01');
    await hold.getByLabel('Demo release note', { exact: true })
      .fill('Synthetic human follow-up recorded. This is not medical clearance.');
    await expectMutation(page, '/api/v1/workers/worker-01/release-hold', () =>
      hold.getByRole('button', { name: 'Release demo hold', exact: true }).click(),
    );
    await expect(hold).toHaveCount(0);
  } finally {
    // A failed UI assertion must not leave an active hold in the next local run.
    const signedIn = await page.request.post('/api/v1/auth/login', {
      data: { email: 'safety@example.test', password: DEMO_PASSWORD },
    });
    if (signedIn.ok()) {
      const session = await signedIn.json();
      await page.request.post('/api/v1/workers/worker-01/release-hold', {
        headers: { Authorization: `Bearer ${session.access_token}` },
        data: { note: 'Synthetic browser test cleanup after human-response verification.' },
      });
    }
  }
});
