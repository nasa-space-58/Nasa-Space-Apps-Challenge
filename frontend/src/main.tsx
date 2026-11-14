import { createRoot } from 'react-dom/client';
import App from './App';
import { LanguageProvider } from './i18n';
import './style.css';
import './cinematic.css';

createRoot(document.getElementById('root')!).render(
  <LanguageProvider>
    <App />
  </LanguageProvider>,
);
