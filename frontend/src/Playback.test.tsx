import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, test, vi } from 'vitest';
import App from './App';
import example from './fixtures/malapert';

vi.mock('./components/MoonScene', () => ({default: () => null}));
afterEach(() => {cleanup(); localStorage.clear(); vi.unstubAllGlobals();});
test('restores real API results and scrubs positions and next-setting state together', async () => {
 localStorage.setItem('lunar-analysis-id','saved-job');
 vi.stubGlobal('fetch', vi.fn(async (path: string) => ({ok:true,json:async()=> path.endsWith('/result') ? example : path.endsWith('/presets') ? {sites:[]} : {id:'saved-job',status:'ready'}})));
 render(<App />);
 await screen.findByText('20.5%');
 expect(screen.getByRole('img',{name:/Terrain horizon. Sun partial/})).toBeTruthy();
 const slider=screen.getByRole('slider',{name:'Observation time'});
 fireEvent.change(slider,{target:{value:'1'}});
 await waitFor(()=>expect(slider.getAttribute('aria-valuetext')).toBe('04 Nov, 23:00 UTC'));
 expect(screen.getByRole('img',{name:/Terrain horizon. Sun dark/})).toBeTruthy();
 expect(screen.getAllByText('No event in range')).toHaveLength(2);
});
