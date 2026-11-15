import { createRoot } from 'react-dom/client';
import App from './App';
import { UnifiedPlanProvider } from './components/UnifiedMissionWorkspace';
import { LanguageProvider } from './i18n';
import './style.css';
import './cinematic.css';
import './mission-refresh.css';

createRoot(document.getElementById('root')!).render(
  <LanguageProvider>
    <UnifiedPlanProvider>
      <App />
    </UnifiedPlanProvider>
  </LanguageProvider>,
);
