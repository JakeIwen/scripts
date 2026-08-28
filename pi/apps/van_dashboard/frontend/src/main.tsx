import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';

import { App } from './App';
import { ToastProvider } from './components/ToastProvider';
import './styles/tokens.css';
import './styles/base.css';
import './styles/dashboard.css';

const root = document.getElementById('root');
if (!root) throw new Error('The dashboard root element is missing');

createRoot(root).render(
  <StrictMode>
    <ToastProvider>
      <App />
    </ToastProvider>
  </StrictMode>,
);
