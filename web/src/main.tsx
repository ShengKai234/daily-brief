import React from 'react';
import ReactDOM from 'react-dom/client';
import { BrowserRouter } from 'react-router-dom';
import { AppRoutes } from './App';
import { AuthProvider } from './auth/AuthContext';
import { registerPwaServiceWorker } from './pwa';
import './styles/global.css';

registerPwaServiceWorker();

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <BrowserRouter>
      <AuthProvider>
        <AppRoutes />
      </AuthProvider>
    </BrowserRouter>
  </React.StrictMode>,
);
