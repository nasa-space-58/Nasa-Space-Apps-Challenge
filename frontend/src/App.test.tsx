import { render, screen, cleanup } from '@testing-library/react';
import { afterEach, expect, test } from 'vitest';
import App from './App';

afterEach(cleanup);
test('shows the development state and API entry point', () => {
  render(<App />);
  expect(screen.getByRole('heading', { name: 'Lunar Landing Navigator' })).toBeTruthy();
  expect(screen.getByRole('link').getAttribute('href')).toBe('/api/docs');
});
