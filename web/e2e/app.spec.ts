import { expect, test } from '@playwright/test';

async function expectNoHorizontalOverflow(page: import('@playwright/test').Page) {
  const overflow = await page.evaluate(() => {
    const doc = document.documentElement;
    return doc.scrollWidth > doc.clientWidth;
  });
  expect(overflow).toBe(false);
}

test('usable at phone width (375x667) across sign-in, report, notes, settings, and profile', async ({
  page,
}) => {
  await page.goto('/');
  await expect(page.getByRole('heading', { name: 'Sign In to Daily Brief' })).toBeVisible();
  await expectNoHorizontalOverflow(page);

  await page.getByRole('button', { name: /Sign in as Seeded Owner/i }).click();
  await expect(page.getByRole('heading', { name: 'Stock Market' })).toBeVisible();
  await expectNoHorizontalOverflow(page);

  // Mark item as read and save a plain-text note
  await page.getByRole('checkbox').first().check();
  await page.getByRole('button', { name: 'Add Note' }).first().click();
  await page.getByLabel(/Note for Semiconductor/i).fill('<b>Phone viewport note</b>');
  await page.getByRole('button', { name: 'Save Note' }).click();
  await expect(page.getByTestId('note-display-60000000-0000-4000-8000-000000000001')).toHaveText(
    '<b>Phone viewport note</b>',
  );
  await expectNoHorizontalOverflow(page);

  // Navigate to Topics & Slots
  await page.getByRole('link', { name: 'Topics & Slots' }).click();
  await expect(page.getByRole('heading', { name: /Topics & Delivery Slots/i })).toBeVisible();
  await expectNoHorizontalOverflow(page);

  // Navigate to Profile
  await page.getByRole('link', { name: 'Profile' }).click();
  await expect(page.getByRole('heading', { name: 'Profile Settings' })).toBeVisible();
  await expectNoHorizontalOverflow(page);
});
