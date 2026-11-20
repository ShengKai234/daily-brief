import '@testing-library/jest-dom/vitest';
import { beforeEach } from 'vitest';
import { resetMockApiState } from '../api/mockApi';

beforeEach(() => {
  window.localStorage.clear();
  resetMockApiState();
});
